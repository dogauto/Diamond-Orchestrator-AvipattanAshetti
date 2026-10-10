import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

from diamond.config import DiamondConfig, load_config
from diamond.delivery import run_stage
from diamond.delivery_contracts import DeliverySnapshot, DeliverySpec, DeliveryStatus, ROLES
from diamond.delivery_gates import evidence
from diamond.delivery_store import DeliveryStore
from diamond.diagnostics import ProbeResult, probe_version
from diamond.domain import Provider, Role
from diamond.evidence import digest_text, environment_secrets, safe_excerpt
from diamond.errors import NotFoundError
from diamond.integration import capture_components
from diamond.live import _CODE_BRIEF, _CODE_CHECKS, candidate_manifest, validate_code
from diamond.store import EventStore
from diamond.validation import canonical_json, utc_now
from diamond.workflow import record, run_step
from diamond.workflow_cli import protect_inputs
from diamond.workflow_contracts import TestCommand, WorkflowSnapshot, WorkflowSpec, WorkflowStatus
from diamond.workflow_store import WorkflowStore
from diamond.workspace import workspace_manifest


BUGGY = """import re
def parse_duration(value: str) -> int:
    if not isinstance(value, str):
        raise TypeError('must be a string')
    match = re.fullmatch(r'([0-9]+)m([0-9]+)s', value)
    if match is None:
        raise ValueError('invalid syntax')
    minutes, seconds = (int(part.lstrip('0') or '0') for part in match.groups())
    if minutes > 1440 or seconds > 59:
        raise ValueError('out of range')
    return minutes * 60 + seconds + 1
"""


def acceptance_checks() -> str:
    before, separator, after = _CODE_CHECKS.rpartition("\nprint(")
    if not separator:
        raise RuntimeError("duration acceptance template has no final report")
    additional = """
for value in ('0' * 5000 + '1m02s', '1m' + '0' * 5000 + '02s'):
    assert parse_duration(value) == 62, 'long leading-zero representation'
    passed += 1
"""
    return before + additional + "print(" + after


def fresh_read(project: Path, ledger: EventStore, snapshot: DeliverySnapshot) -> bool:
    environment = dict(os.environ, PYTHONPATH=str(project / "src"), PYTHONDONTWRITEBYTECODE="1")
    response = subprocess.run([sys.executable, "-B", "-m", "diamond", "--project", str(project),
        "--state-dir", str(ledger.database.parent), "--json", "delivery", "show", snapshot.spec.delivery_id],
        capture_output=True, text=True, timeout=10, env=environment)
    return response.returncode == 0 and json.loads(response.stdout)["result"]["delivery"] == snapshot.to_dict()


async def source_workflow(ledger: EventStore, root: Path, config: DiamondConfig, config_path: Path,
                          routes: dict[Role, ProbeResult], report: dict[str, Any], report_path: Path,
                          *, injected: bool) -> WorkflowSnapshot:
    digest = hashlib.sha256(config_path.read_bytes()).hexdigest()
    objective = "Implement src/duration.py. " + _CODE_BRIEF.split("Implement ", 1)[1]
    objective += (" Module body must contain only optional import re and parse_duration. No module-level assignments, "
        "constants, regex compilation or other actions. Put regex operations inside the function. "
        "No underscore-prefixed attributes; use literal error messages. "
        "The acceptance suite includes 5000 leading zeroes in either numeric field.")
    check = "tests/component-sanity.py" if injected else "tests/check.py"
    test = TestCommand("component sanity" if injected else "duration 24 cases", (sys.executable, "-B", check))
    spec = WorkflowSpec(uuid4().hex, objective, str(root), ("src/duration.py",), (test,),
        config.resolve_provider(Role.BUILDER), config.resolve_provider(Role.DEBUGGER), digest, workspace_manifest(root),
        correction_round_limit=1, timeout_seconds=180, max_workers=config.controller.max_workers)
    report.update(current_workflow_id=spec.workflow_id, current_operation="source_creation")
    persist_report(report_path, report)
    protect_inputs(spec, config_path, config)
    workflows = WorkflowStore(ledger)
    created = workflows.create(spec, f"live-source:{spec.workflow_id}")
    publish = WorkflowStore.publish

    def validated_publish(store: WorkflowStore, expected: WorkflowSnapshot, response: dict) -> WorkflowSnapshot:
        files = [dict(item) for item in response["files"]]
        if len(files) != 1 or files[0]["relative_path"] != "src/duration.py":
            raise ValueError("fixture requires its one declared source file")
        original = validate_code(files[0]["content"])
        if injected and expected.status is WorkflowStatus.BUILDING:
            record(ledger, expected.active_attempt_id, "fixture_fault_injection", {
                "origin": "controller_acceptance_fixture", "reason": "upstream sanity test deliberately does not establish full acceptance",
                "provider_source_sha256": hashlib.sha256(original.encode()).hexdigest(),
                "injected_source_sha256": hashlib.sha256(BUGGY.encode()).hexdigest()}, "fixture:injection")
            files[0]["content"] = BUGGY
        return publish(store, expected, {**response, "files": files})

    builder = routes[Role.BUILDER]
    with patch.object(WorkflowStore, "publish", new=validated_publish):
        report["current_operation"] = "source_builder"
        persist_report(report_path, report)
        result = await run_step(ledger, spec.workflow_id, created.revision, digest, builder.executable, builder.version)
        if result.status is WorkflowStatus.NEEDS_CORRECTION:
            debugger = routes[Role.DEBUGGER]
            report["current_operation"] = "source_debugger"
            persist_report(report_path, report)
            result = await run_step(ledger, spec.workflow_id, result.revision, digest, debugger.executable, debugger.version)
    return result


def persist_report(path: Path, report: dict[str, Any]) -> None:
    path.write_text(canonical_json(report) + "\n", encoding="utf-8")


def verified_backup(project: Path, ledger: EventStore, state: Path, accepted: str) -> dict[str, Any]:
    directory = state / "verified-backup"
    directory.mkdir()
    path = ledger.backup(directory / "diamond.sqlite3")
    restored = EventStore(path)
    expected = {"tasks", "events", "commands", "attempts", "attempt_commands", "attempt_evidence", "dispatch_claims",
                "workflows", "workflow_events", "workflow_commands", "deliveries", "delivery_events", "delivery_commands"}
    rows = []
    for store in (ledger, restored):
        with store._connection(write=False) as connection:
            names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if names != expected or connection.execute("PRAGMA user_version").fetchone()[0] != 6:
                raise RuntimeError("backup ledger tables/schema differ from the acceptance contract")
            if [tuple(row) for row in connection.execute("PRAGMA integrity_check")] != [("ok",)] or list(connection.execute("PRAGMA foreign_key_check")):
                raise RuntimeError("backup ledger integrity or foreign-key validation failed")
            rows.append({name: sorted((dict(row) for row in connection.execute(f'SELECT * FROM "{name}"')), key=canonical_json)
                         for name in sorted(expected)})
    if rows[0] != rows[1]:
        raise RuntimeError("backup differs from original ledger rows")
    for task in ledger.list():
        if ledger.replay(task.spec.task_id) != restored.replay(task.spec.task_id):
            raise RuntimeError("backup task replay differs")
        for attempt in ledger.attempts(task.spec.task_id):
            if ledger.replay_attempt(attempt.attempt_id) != restored.replay_attempt(attempt.attempt_id):
                raise RuntimeError("backup attempt replay differs")
    for workflow in WorkflowStore(ledger).list():
        if WorkflowStore(ledger).replay(workflow.spec.workflow_id) != WorkflowStore(restored).replay(workflow.spec.workflow_id):
            raise RuntimeError("backup workflow replay differs")
    for delivery in DeliveryStore(ledger).list():
        if DeliveryStore(ledger).replay(delivery.spec.delivery_id) != DeliveryStore(restored).replay(delivery.spec.delivery_id):
            raise RuntimeError("backup delivery replay differs")
    current = DeliveryStore(restored).get(accepted)
    if not fresh_read(project, restored, current):
        raise RuntimeError("fresh CLI process did not read the restored delivery")
    if DeliveryStore(ledger).certificate(accepted, current.spec.config_digest) != DeliveryStore(restored).certificate(accepted, current.spec.config_digest):
        raise RuntimeError("restored acceptance certificate differs")
    semantic = hashlib.sha256()
    for name in sorted(expected):
        semantic.update(name.encode("utf-8"))
        semantic.update(b"\0")
        semantic.update(canonical_json(rows[0][name]).encode("utf-8"))
        semantic.update(b"\0")
    return {"status": "PASS", "database_schema_version": 6, "backup": str(path), "integrity_check": "ok",
        "foreign_key_violations": 0, "all_table_rows_match": True, "task_attempt_workflow_delivery_replays_match": True,
        "fresh_restored_cli_matches": True, "restored_certificate_matches": True,
        "row_counts": {name: len(values) for name, values in rows[0].items()}, "semantic_rows_sha256": semantic.hexdigest()}


def delivery_receipt(project: Path, state: Path, ledger: EventStore, current: DeliverySnapshot,
                     source: WorkflowSnapshot, observed: list[dict[str, Any]], name: str, injected: bool,
                     report: dict[str, Any]) -> dict[str, Any]:
    spec = current.spec
    expected = DeliveryStatus.REJECTED if injected else DeliveryStatus.ACCEPTED
    retained = state / f"duration-{spec.delivery_id}.py"
    retained.write_bytes((Path(spec.workspace_root) / "src/duration.py").read_bytes())
    receipt = {"name": name, "status": "FAIL", "delivery_id": spec.delivery_id,
        "source_workflow_id": source.spec.workflow_id, "source_workflow_revision": source.revision,
        "expected_status": expected.value, "actual_status": current.status.value, "stages": observed,
        "manifest_json": current.manifest_json, "candidate_digest": digest_text(current.manifest_json),
        "retained_artifact": str(retained), "artifact_sha256": hashlib.sha256(retained.read_bytes()).hexdigest(),
        "supersedes": spec.supersedes, "inherited_findings": json.loads(current.inherited_findings_json),
        "reports": json.loads(current.reports_json), "replay_matches": False, "fresh_process_matches": False,
        "detail": current.detail}
    report["outcomes"].append(receipt)
    receipt["actual_checks"] = {role: evidence(ledger, gate["tests_evidence_id"]).to_dict()["payload"]
                               for role, gate in receipt["reports"].items()}
    receipt["replay_matches"] = DeliveryStore(ledger).replay(spec.delivery_id) == current
    receipt["fresh_process_matches"] = fresh_read(project, ledger, current)
    passed = current.status is expected and receipt["replay_matches"] and receipt["fresh_process_matches"]
    if not injected and passed:
        passed = set(receipt["actual_checks"]) == {role.value for role in ROLES}
        passed = passed and all(json.loads(item["tests"][0]["stdout"]) == {"passed": 24, "status": "PASS"}
                                for item in receipt["actual_checks"].values())
        if passed:
            receipt["certificate"] = DeliveryStore(ledger).certificate(spec.delivery_id, spec.config_digest)
    receipt["status"] = "PASS" if passed else "FAIL"
    receipt["attempt_receipt"] = "NOT_CREATED"
    if current.active_attempt_id is not None:
        try:
            ledger.get_attempt(current.active_attempt_id)
        except NotFoundError:
            pass
        else:
            receipt["attempt_receipt"] = "PERSISTED"
            record(ledger, current.active_attempt_id, "delivery_live_acceptance", receipt, "fixture:delivery-acceptance")
    return receipt


async def run_scenarios(project: Path, state: Path, ledger: EventStore, config: DiamondConfig,
                        report: dict[str, Any], report_path: Path) -> None:
    config_path = project / "config" / "diamond.toml"
    config_digest = hashlib.sha256(config_path.read_bytes()).hexdigest()
    stages = (Role.BUILDER, Role.DEBUGGER, *ROLES)
    if any(config.resolve_provider(role) is Provider.GEMINI for role in stages):
        raise RuntimeError("Gemini quota confirmation remains blocked; no probe or execution is authorized")
    routes = {role: probe_version(config.resolve_provider(role), config.providers[config.resolve_provider(role)].executable) for role in stages}
    if any(probe.status != "available" for probe in routes.values()):
        raise RuntimeError("explicit live delivery routes are unavailable")
    target = state / "workspaces" / "target"
    (target / "src").mkdir(parents=True)
    (target / "tests").mkdir()
    (target / "src/duration.py").write_text("def parse_duration(value: str) -> int: return 0\n", encoding="utf-8")
    (target / "tests/check.py").write_text("import sys\nsys.path.insert(0,'.')\n" + acceptance_checks(), encoding="utf-8")
    (target / "tests/component-sanity.py").write_text("import sys\nsys.path.insert(0,'.')\n"
        "from src.duration import parse_duration\nassert parse_duration('0m0s') in (0,1)\n"
        "print('component sanity passed')\n", encoding="utf-8")
    tests = (TestCommand("duration 24 cases", (sys.executable, "-B", "tests/check.py")),)
    criteria = canonical_json({"duration-contract": {"description": _CODE_BRIEF.split("Implement ", 1)[1], "tests": ["duration 24 cases"]}})
    objective = ("Integrate the accepted duration utility source unchanged. Select exactly one component for each source path. "
        "The controller executes the full acceptance suite. The explicit fault-fixture round must preserve its captured source "
        "so actual checks determine rejection; subsequent corrected revisions require independent QA, Reviewer and Release audit.")
    deliveries = DeliveryStore(ledger)
    prior = None
    for name, injected in (("reject-weak-upstream", True), ("accept-corrected-revision", False)):
        report.update(current_scenario=name, current_workflow_id=None, current_delivery_id=None,
                      current_operation="source_workspace")
        persist_report(report_path, report)
        source_root = state / "workspaces" / f"source-{name}"
        shutil.copytree(target, source_root)
        source = await source_workflow(ledger, source_root, config, config_path, routes, report, report_path, injected=injected)
        if source.status is not WorkflowStatus.VERIFIED:
            report["outcomes"].append({"name": name, "status": "FAIL", "source_workflow_id": source.spec.workflow_id,
                                       "source_status": source.status.value, "detail": source.detail})
            return
        base = workspace_manifest(target)
        components = capture_components(ledger, ((source.spec.workflow_id, source.revision),), target, base)
        spec = DeliverySpec(uuid4().hex, objective, str(target), base, components, tests, criteria,
            tuple(config.resolve_provider(role) for role in ROLES), config_digest, supersedes=prior.spec.delivery_id if prior else None,
            timeout_seconds=180, max_workers=config.controller.max_workers)
        report.update(current_delivery_id=spec.delivery_id, current_operation="delivery_creation")
        persist_report(report_path, report)
        protect_inputs(spec.work_spec(), config_path, config)
        current = deliveries.create(spec, f"live-delivery:{spec.delivery_id}")
        observed = []
        for role in (Role.INTEGRATOR,) if injected else ROLES:
            probe = routes[role]
            report.update(current_operation=f"delivery:{role.value}", current_role=role.value, current_delivery_status=current.status.value,
                          current_delivery_revision=current.revision)
            persist_report(report_path, report)
            current = await run_stage(ledger, spec.delivery_id, current.revision, config_digest, probe.executable, probe.version)
            observed.append({"role": role.value, "status": current.status.value, "revision": current.revision})
            if current.status in {DeliveryStatus.BLOCKED, DeliveryStatus.REJECTED, DeliveryStatus.STALE}:
                break
        report.update(current_operation="receipt_verification", current_delivery_status=current.status.value,
                      current_delivery_revision=current.revision)
        persist_report(report_path, report)
        receipt = delivery_receipt(project, state, ledger, current, source, observed, name, injected, report)
        persist_report(report_path, report)
        if receipt["status"] != "PASS":
            return
        prior = current


async def main(project: Path | None = None, *, state: Path | None = None) -> int:
    project = project or Path(__file__).resolve().parents[1]
    state = state or project / "docs" / "verification" / "delivery-live-2026-10-08" / f"run-{uuid4().hex}"
    state.mkdir(parents=True, exist_ok=False)
    path = state / f"delivery-acceptance-{uuid4().hex}.json"
    result = {"schema_version": 1, "scope": "bounded_candidate_integration_and_acceptance", "status": "RUNNING",
        "started_at": utc_now(), "source_candidate_sha256": None, "source_manifest": None, "outcomes": [],
        "database": str(state / "diamond.sqlite3"), "ok": False, "current_operation": "initialization",
        "gemini": "NOT_INVOKED_QUOTA_BLOCKED", "production_authorization": False}
    persist_report(path, result)
    try:
        manifest = candidate_manifest(project)
        result.update(source_manifest=manifest, source_candidate_sha256=digest_text(canonical_json(manifest)))
        persist_report(path, result)
        config = load_config(project / "config" / "diamond.toml")
        ledger = EventStore(state / "diamond.sqlite3")
        await run_scenarios(project, state, ledger, config, result, path)
        if candidate_manifest(project) != manifest:
            raise RuntimeError("live source candidate changed; evidence is stale")
        passed = len(result["outcomes"]) == 2 and all(item["status"] == "PASS" for item in result["outcomes"])
        if passed:
            result["current_operation"] = "backup_verification"
            persist_report(path, result)
            result["backup_validation"] = verified_backup(project, ledger, state, result["outcomes"][-1]["delivery_id"])
        result["ok"] = passed
    except Exception as exc:
        result["error"] = {"type": type(exc).__name__, "detail": safe_excerpt(str(exc), 4096, environment_secrets())}
    result.update(status="PASS" if result["ok"] else "FAIL", completed_at=utc_now())
    persist_report(path, result)
    print(json.dumps({"ok": result["ok"], "source_candidate_sha256": result["source_candidate_sha256"], "report": str(path),
        "error": result.get("error"), "outcomes": [{key: item.get(key) for key in
        ("name", "status", "delivery_id", "actual_status", "stages", "detail")} for item in result["outcomes"]]}, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
