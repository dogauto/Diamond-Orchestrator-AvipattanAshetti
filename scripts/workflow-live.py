import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from diamond.config import load_config
from diamond.diagnostics import probe_version
from diamond.domain import Role
from diamond.evidence import digest_text
from diamond.live import _CODE_BRIEF, _CODE_CHECKS, candidate_manifest, validate_code
from diamond.store import EventStore
from diamond.validation import canonical_json
from diamond.workflow import record, run_step
from diamond.workflow_cli import protect_inputs
from diamond.workflow_contracts import TestCommand, WorkflowSnapshot, WorkflowSpec, WorkflowStatus
from diamond.workflow_store import WorkflowStore
from diamond.workspace import workspace_manifest


_BUGGY = """import re
def parse_duration(value: str) -> int:
    if not isinstance(value, str):
        raise TypeError('must be a string')
    match = re.fullmatch(r'([0-9]+)m([0-9]+)s', value)
    if match is None:
        raise ValueError('invalid syntax')
    minutes, seconds = map(int, match.groups())
    if minutes > 1440 or seconds > 59:
        raise ValueError('out of range')
    return minutes * 60 + seconds + 1
"""


def fresh_restart(project: Path, ledger: EventStore, final: WorkflowSnapshot) -> bool:
    env = dict(os.environ, PYTHONPATH=str(project / "src"), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-B", "-m", "diamond", "--project", str(project),
        "--state-dir", str(ledger.database.parent), "--json", "workflow", "show", final.spec.workflow_id],
        capture_output=True, text=True, timeout=10, env=env)
    return result.returncode == 0 and json.loads(result.stdout)["result"]["workflow"] == final.to_dict()


async def main(project: Path | None = None, *, state: Path | None = None) -> int:
    project = project or Path(__file__).resolve().parents[1]
    state = state or project / "docs" / "verification" / "workflow-live-2026-10-08" / f"run-{uuid4().hex}"
    ledger = EventStore(state / "diamond.sqlite3")
    workflows = WorkflowStore(ledger)
    config_path = project / "config" / "diamond.toml"
    config = load_config(config_path)
    config_digest = hashlib.sha256(config_path.read_bytes()).hexdigest()
    routes = {role: probe_version(config.resolve_provider(role), config.providers[config.resolve_provider(role)].executable)
              for role in (Role.BUILDER, Role.DEBUGGER)}
    if any(diagnostic.status != "available" for diagnostic in routes.values()):
        raise RuntimeError("live workflow routes are unavailable")
    manifest = candidate_manifest(project)
    source_digest = digest_text(canonical_json(manifest))
    outcomes = []
    for name in ("build-from-brief", "imported-bug-reproduction-and-correction"):
        with tempfile.TemporaryDirectory(prefix="diamond-workflow-real-", dir=os.environ["TMPDIR"]) as directory:
            root = Path(directory).resolve()
            (root / "tests").mkdir()
            (root / "src").mkdir()
            (root / "tests" / "check.py").write_text("import sys\nsys.path.insert(0,'.')\n" + _CODE_CHECKS, encoding="utf-8")
            objective = "Implement src/duration.py with parse_duration. " + _CODE_BRIEF.split("Implement ", 1)[1]
            objective += (" Source contract: the module body must contain only an optional import re and the "
                "parse_duration function definition. No module-level assignments, constants, regex compilation, "
                "or executable statements. Put any regex operations inside the function. "
                "Do not access attributes whose names start with an underscore; use simple literal error messages.")
            spec = WorkflowSpec(uuid4().hex, objective, str(root), ("src/duration.py",),
                (TestCommand("duration acceptance 22 cases", (sys.executable, "-B", "tests/check.py")),),
                config.resolve_provider(Role.BUILDER), config.resolve_provider(Role.DEBUGGER), config_digest,
                workspace_manifest(root), correction_round_limit=1, timeout_seconds=180, max_workers=config.controller.max_workers)
            protect_inputs(spec, config_path, config)
            created = workflows.create(spec, f"live-workflow:{spec.workflow_id}:create")
            builder = routes[Role.BUILDER]
            injected = {}
            publish = WorkflowStore.publish

            def fixture_publish(store: WorkflowStore, expected: WorkflowSnapshot, response: object):
                files = [dict(item) for item in response["files"]]
                if len(files) != 1 or files[0]["relative_path"] != "src/duration.py":
                    raise ValueError("duration fixture requires exactly its declared source file")
                source = validate_code(files[0]["content"])
                if name == "imported-bug-reproduction-and-correction" and expected.status is WorkflowStatus.BUILDING:
                    injected.update(origin="controller_acceptance_fixture", reason="explicit off-by-one fault",
                        provider_source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                        injected_source_sha256=hashlib.sha256(_BUGGY.encode()).hexdigest())
                    record(ledger, expected.active_attempt_id, "fixture_fault_injection", injected, "fixture:injection")
                    files[0]["content"] = _BUGGY
                return publish(store, expected, {**response, "files": files})

            with patch.object(WorkflowStore, "publish", new=fixture_publish):
                first = await run_step(ledger, spec.workflow_id, created.revision, config_digest, builder.executable, builder.version)
            artifact = root / "src" / "duration.py"
            initial_source = artifact.read_bytes() if artifact.is_file() else None
            initial_digest = hashlib.sha256(initial_source).hexdigest() if initial_source is not None else None
            if initial_source is not None:
                (state / f"duration-{spec.workflow_id}-builder.py").write_bytes(initial_source)
            final = first
            if first.status is WorkflowStatus.NEEDS_CORRECTION:
                debugger = routes[Role.DEBUGGER]
                with patch.object(WorkflowStore, "publish", new=fixture_publish):
                    final = await run_step(ledger, spec.workflow_id, first.revision, config_digest, debugger.executable, debugger.version)
            artifact_digest = hashlib.sha256(artifact.read_bytes()).hexdigest() if artifact.is_file() else None
            passed = final.status is WorkflowStatus.VERIFIED and (name == "build-from-brief" or
                bool(injected) and initial_digest == injected["injected_source_sha256"] and
                first.status is WorkflowStatus.NEEDS_CORRECTION and final.corrections_used == 1)
            verification = [item.to_dict()["payload"] for item in ledger.evidence(final.active_task_id)
                            if item.kind == "required_tests" and item.attempt_id == final.active_attempt_id]
            checks = json.loads(verification[-1]["tests"][0]["stdout"]) if passed else None
            passed = passed and checks == {"passed": 22, "status": "PASS"}
            receipt = {"name": name, "workflow_id": spec.workflow_id, "source_candidate_sha256": source_digest,
                "first_status": first.status.value, "final_status": final.status.value, "corrections_used": final.corrections_used,
                "status": "PASS" if passed else "FAIL", "detail": final.detail,
                "initial_artifact_sha256": initial_digest, "artifact_sha256": artifact_digest,
                "fault_injection": injected or None, "verified_checks": checks, "candidate_digest": digest_text(final.manifest_json),
                "failure_packet": first.failure.to_dict() if first.failure else None,
                "restart_replay_matches": WorkflowStore(EventStore(ledger.database)).replay(spec.workflow_id) == final,
                "fresh_process_restart_matches": fresh_restart(project, ledger, final),
                "steps": [{"task_id": task.spec.task_id, "role": task.spec.role.value,
                    "attempts": [attempt.to_dict() for attempt in ledger.attempts(task.spec.task_id)]}
                    for task in ledger.list() if task.spec.task_id in {first.active_task_id, final.active_task_id}]}
            if artifact.is_file():
                retained = state / f"duration-{spec.workflow_id}-final.py"
                retained.write_bytes(artifact.read_bytes())
                receipt["retained_artifact"] = str(retained)
            if ledger.attempts(final.active_task_id):
                record(ledger, final.active_attempt_id, "workflow_live_acceptance", receipt, "fixture:acceptance")
            outcomes.append(receipt)
    current = candidate_manifest(project)
    if current != manifest:
        raise RuntimeError("workflow live evidence is stale because its source candidate changed")
    report = {"source_candidate_sha256": source_digest, "source_manifest": manifest, "outcomes": outcomes,
              "ok": all(item["status"] == "PASS" and item["restart_replay_matches"] and item["fresh_process_restart_matches"] for item in outcomes),
              "gemini": "NOT_INVOKED_QUOTA_BLOCKED", "database": str(ledger.database)}
    path = state / f"workflow-acceptance-{uuid4().hex}.json"
    path.write_text(canonical_json(report) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "source_candidate_sha256": source_digest, "report": str(path),
                      "outcomes": outcomes}, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
