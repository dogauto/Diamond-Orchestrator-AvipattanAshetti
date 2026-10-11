"""Bounded goal planning and queue execution for Diamond.

Autonomy is deliberately finite and controller-owned: planning produces only
validated objectives, execution can select only persisted workflows, and every
step still goes through the normal workflow verification gates.
"""

import asyncio
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid4, uuid5

from .config import DiamondConfig
from .diagnostics import probe_version
from .domain import Role
from .errors import ConflictError, ExecutionGuardError, StorageError, ValidationError
from .store import EventStore
from .validation import bounded_int, canonical_json, text
from .results import parse_result
from .workflow_cli import workflow_command
from .workflow_contracts import WorkflowStatus
from .workflow_store import WorkflowStore


PLAN_SCHEMA = canonical_json({
    "type": "object",
    "properties": {"steps": {"type": "array", "minItems": 1, "maxItems": 8,
                               "items": {"type": "object", "properties": {
                                   "objective": {"type": "string", "minLength": 1, "maxLength": 8192}},
                                   "required": ["objective"], "additionalProperties": False}}},
    "required": ["steps"], "additionalProperties": False,
})


@dataclass(frozen=True)
class AutonomyLimits:
    max_steps: int = 8
    max_failures: int = 1

    def __post_init__(self) -> None:
        bounded_int(self.max_steps, "autonomy max steps", 1, 100)
        bounded_int(self.max_failures, "autonomy max failures", 0, 100)


def stop_path(state: Path) -> Path:
    return state / "autonomy.stop"


def request_stop(state: Path) -> None:
    state.mkdir(parents=True, exist_ok=True)
    marker = stop_path(state)
    temporary = marker.with_name(f".{marker.name}.{os.getpid()}")
    temporary.write_text("stop requested\n", encoding="utf-8")
    os.replace(temporary, marker)


def clear_stop(state: Path) -> None:
    try:
        stop_path(state).unlink()
    except FileNotFoundError:
        pass


def _split_goal(goal: str) -> list[str]:
    """Provide a deterministic offline fallback for short, compound goals."""
    parts = [item.strip(" \t\r\n.;") for item in re.split(r"\n+|(?<=[.!?])\s+|;\s*", goal)]
    return [item for item in parts if item]


def parse_plan(value: object) -> tuple[str, ...]:
    if not isinstance(value, dict) or set(value) != {"steps"} or not isinstance(value["steps"], list):
        raise ValidationError("planner response must contain only a steps array")
    result: list[str] = []
    for item in value["steps"]:
        if not isinstance(item, dict) or set(item) != {"objective"} or not isinstance(item["objective"], str):
            raise ValidationError("planner steps must contain only bounded objectives")
        objective = item["objective"].strip()
        text(objective, "planned objective", 8192)
        if not objective:
            raise ValidationError("planner objectives cannot be empty")
        result.append(objective)
    if not 1 <= len(result) <= 8:
        raise ValidationError("planner must return 1..8 steps")
    return tuple(result)


def fallback_plan(goal: str) -> tuple[str, ...]:
    text(goal, "autonomy goal", 8192)
    steps = _split_goal(goal)
    if not steps:
        raise ValidationError("autonomy goal cannot be empty")
    return tuple(steps[:8])


def _planner_prompt(goal: str) -> str:
    return ("Return only a JSON object matching this schema: " + PLAN_SCHEMA +
            "\nDecompose this goal into 1 to 8 concrete implementation objectives. "
            "Do not invent files, tests, credentials, or claims. Do not use tools.\n" + goal)


def plan_goal(goal: str, config: DiamondConfig, project: Path, *, timeout_seconds: int = 60) -> tuple[str, ...]:
    """Ask the configured planner for a bounded plan, with safe offline fallback."""
    text(goal, "autonomy goal", 8192)
    provider = config.resolve_provider(Role.PLANNER)
    diagnostic = probe_version(provider, config.providers[provider].executable)
    if diagnostic.status != "available" or diagnostic.executable is None:
        return fallback_plan(goal)
    from .adapters import PermissionMode, build_invocation
    from .supervisor import ExecutionStatus, run

    invocation = build_invocation(provider, diagnostic.executable, _planner_prompt(goal),
                                   permission_mode=PermissionMode.PLAN, json_schema=PLAN_SCHEMA
                                   if provider.value in {"antigravity", "claude"} else None)
    execution = asyncio.run(run(invocation, project, timeout_seconds, 131072,
                                {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"},
                                enforce_paths=True))
    result = parse_result(invocation, execution)
    if execution.status is not ExecutionStatus.SUCCEEDED or (not result.content and not result.structured_output):
        return fallback_plan(goal)
    try:
        return parse_plan(json.loads(result.structured_output or result.content))
    except (ValueError, TypeError, ValidationError):
        return fallback_plan(goal)


def _lock(state: Path) -> Path:
    state.mkdir(parents=True, exist_ok=True)
    path = state / "autonomy.lock"
    for attempt in range(2):
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(f"{os.getpid()}\n")
            return path
        except FileExistsError as exc:
            if attempt == 0:
                try:
                    owner = int(path.read_text(encoding="utf-8").strip())
                    os.kill(owner, 0)
                except PermissionError as exc:
                    raise ConflictError("autonomy lock is owned by another process") from exc
                except (FileNotFoundError, ProcessLookupError, ValueError, OSError):
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        continue
                    continue
            raise ConflictError("another autonomy run is active; use autonomy stop or inspect its report") from exc
    return path


def run_queue(config: DiamondConfig, config_path: Path, store: EventStore, state: Path,
              limits: AutonomyLimits = AutonomyLimits()) -> dict[str, object]:
    """Run queued workflows until the finite budget or a safety condition stops it."""
    lock = _lock(state)
    run_id = uuid4().hex
    started = time.monotonic()
    outcomes: list[dict[str, object]] = []
    failures = 0
    try:
        workflows = WorkflowStore(store)
        for snapshot in workflows.list():
            if len(outcomes) >= limits.max_steps:
                break
            if stop_path(state).exists():
                break
            if snapshot.status not in {WorkflowStatus.CREATED, WorkflowStatus.NEEDS_CORRECTION}:
                continue
            try:
                provider = snapshot.spec.builder_provider if snapshot.status is WorkflowStatus.CREATED else snapshot.spec.debugger_provider
                diagnostic = probe_version(provider, config.providers[provider].executable)
                if diagnostic.status != "available" or diagnostic.executable is None:
                    raise ExecutionGuardError(f"workflow provider is unavailable: {diagnostic.status}")
                namespace = SimpleNamespace(workflow_command="step", workflow_id=snapshot.spec.workflow_id,
                                            expected_revision=snapshot.revision)
                result, code = workflow_command(namespace, config_path, config, store)
                current = workflows.get(snapshot.spec.workflow_id)
                outcomes.append({"workflow_id": snapshot.spec.workflow_id, "status": current.status.value,
                                 "exit_code": code, "detail": current.detail})
                if code != 0:
                    failures += 1
            except (ConflictError, ExecutionGuardError, StorageError, ValidationError) as exc:
                failures += 1
                outcomes.append({"workflow_id": snapshot.spec.workflow_id, "status": "ERROR", "error": str(exc)})
            if failures > limits.max_failures:
                break
        report = {"run_id": run_id, "steps": outcomes, "failures": failures,
                  "stopped": stop_path(state).exists(), "elapsed_seconds": round(time.monotonic() - started, 3)}
        state.mkdir(parents=True, exist_ok=True)
        (state / f"autonomy-{run_id}.json").write_text(canonical_json(report) + "\n", encoding="utf-8")
        return report
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def plan_and_enqueue(goal: str, config: DiamondConfig, config_path: Path, project: Path,
                     store: EventStore, workspace: Path, owned_paths: tuple[str, ...],
                     tests: tuple[str, ...], command_id: str) -> dict[str, object]:
    """Plan a goal and enqueue validated workflows sharing explicit safe inputs."""
    from .workflow_cli import protect_inputs
    from .workflow_contracts import TestCommand, WorkflowSpec
    from .workflow_store import WorkflowStore
    from .workspace import workspace_manifest

    try:
        workspace = workspace.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValidationError("autonomy workspace must be an existing directory") from exc
    objectives = plan_goal(goal, config, project)
    created = []
    for index, objective in enumerate(objectives):
        workflow_id = uuid5(NAMESPACE_URL, f"diamond:autonomy:{command_id}:{index}").hex
        spec = WorkflowSpec(workflow_id, objective, str(workspace.resolve()), owned_paths,
                            tuple(TestCommand.from_dict(json.loads(item)) for item in tests),
                            config.resolve_provider(Role.BUILDER), config.resolve_provider(Role.DEBUGGER),
                            hashlib.sha256(config_path.read_bytes()).hexdigest(),
                            workspace_manifest(workspace), correction_round_limit=config.controller.correction_round_limit,
                            timeout_seconds=min(config.controller.task_timeout_seconds, 900), max_workers=config.controller.max_workers)
        protect_inputs(spec, config_path, config)
        created.append(WorkflowStore(store).create(spec, f"{command_id}:{index}").to_dict())
    return {"goal": goal, "objectives": list(objectives), "workflows": created}
