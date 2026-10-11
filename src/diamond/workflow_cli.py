import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .config import DiamondConfig
from .diagnostics import probe_version
from .domain import Role
from .errors import ValidationError
from .store import EventStore
from .workflow import run_step
from .workflow_contracts import TestCommand, WorkflowSpec, WorkflowStatus
from .workflow_store import WorkflowStore
from .workspace import workspace_manifest


def protect_inputs(spec: WorkflowSpec, config_path: Path, config: DiamondConfig) -> None:
    root = Path(spec.workspace_root)
    protected = {config_path.resolve()}
    for test in spec.tests:
        protected.add(Path(test.argv[0]).resolve())
        for index, argument in enumerate(test.argv[1:], 1):
            candidate = Path(argument)
            target = candidate if candidate.is_absolute() else root / candidate
            if target.is_file():
                protected.add(target.resolve())
            if target.is_dir() and target.resolve() != root:
                protected.update(path.resolve() for path in target.rglob("*.py"))
            if argument.replace(".", "").replace("_", "").isalnum() and ("." in argument or test.argv[index - 1] == "-m"):
                module = root.joinpath(*argument.split("."))
                for path in (module.with_suffix(".py"), module / "__main__.py", module / "__init__.py"):
                    if path.is_file():
                        protected.add(path.resolve())
                for parent in module.parents:
                    if parent == root or not parent.is_relative_to(root):
                        break
                    if (parent / "__init__.py").is_file():
                        protected.add((parent / "__init__.py").resolve())
    for provider in (spec.builder_provider, spec.debugger_provider):
        executable = config.providers[provider].executable
        if Path(executable).is_absolute():
            protected.add(Path(executable).resolve())
    for path in spec.owned_paths:
        target = (root / path).resolve()
        if target in protected or any(part in {"tests", "test", "config", "scripts"} for part in Path(path).parts):
            raise ValidationError("workflow ownership must not include trusted tests, configuration, or executable inputs")


def workflow_command(args: argparse.Namespace, config_path: Path, config: DiamondConfig,
                     ledger: EventStore) -> tuple[dict[str, Any], int]:
    workflows = WorkflowStore(ledger)
    config_digest = hashlib.sha256(config_path.read_bytes()).hexdigest()
    if args.workflow_command == "create":
        identifier = args.workflow_id or uuid5(NAMESPACE_URL, f"diamond:workflow-create:{args.command_id}").hex
        try:
            root = Path(args.workspace).expanduser().resolve(strict=True)
            tests = tuple(TestCommand.from_dict(json.loads(value)) for value in args.test)
        except (OSError, ValueError, RecursionError) as exc:
            raise ValidationError("workflow requires an existing workspace and valid trusted test JSON") from exc
        if ledger.database.is_relative_to(root):
            raise ValidationError("workflow ledger must be outside the editable workspace")
        spec = WorkflowSpec(identifier, args.objective, str(root), tuple(args.owned_path), tests,
            config.resolve_provider(Role.BUILDER), config.resolve_provider(Role.DEBUGGER), config_digest,
            workspace_manifest(root), correction_round_limit=args.correction_limit if args.correction_limit is not None else config.controller.correction_round_limit,
            timeout_seconds=min(config.controller.task_timeout_seconds, 900), max_workers=config.controller.max_workers)
        protect_inputs(spec, config_path, config)
        return {"workflow": workflows.create(spec, args.command_id).to_dict()}, 0
    if args.workflow_command == "list":
        return {"workflows": [item.to_dict() for item in workflows.list()]}, 0
    if args.workflow_command == "show":
        try:
            item = workflows.replay(args.workflow_id)
            return {"workflow": item.to_dict()}, 0
        except StorageError as e:
            if "does not match its projection" in str(e):
                # Treat missing or corrupt workflow as NOT_FOUND
                raise ValidationError("workflow not found") from e
            raise
    if args.workflow_command == "events":
        return {"events": list(workflows.events(args.workflow_id))}, 0
    current = workflows.get(args.workflow_id)
    if args.workflow_command == "reconcile":
        result = workflows.reconcile(args.workflow_id, args.expected_revision, args.reason,
                                     f"workflow:{args.workflow_id}:reconcile:{args.expected_revision}")
        return {"workflow": result.to_dict()}, 0
    provider = current.spec.builder_provider if current.status is WorkflowStatus.CREATED else current.spec.debugger_provider
    config.resolve_provider(Role.BUILDER if current.status is WorkflowStatus.CREATED else Role.DEBUGGER, provider)
    diagnostic = probe_version(provider, config.providers[provider].executable)
    if diagnostic.status != "available" or diagnostic.executable is None:
        raise ValidationError(f"workflow provider is unavailable: {diagnostic.status}")
    result = asyncio.run(run_step(ledger, args.workflow_id, args.expected_revision, config_digest,
                                  diagnostic.executable, diagnostic.version))
    return {"workflow": result.to_dict()}, 0 if result.status is WorkflowStatus.VERIFIED else 1
