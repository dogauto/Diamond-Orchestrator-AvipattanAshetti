import json
from pathlib import Path
from typing import Any

from .delivery_contracts import DeliverySnapshot, ROLES, requirements
from .domain import Role, TaskStatus
from .errors import ExecutionGuardError, ValidationError
from .evidence import AttemptStatus, EvidenceRecord, digest_text
from .store import EventStore
from .validation import bounded_int, canonical_json, command_id, exact_keys, parse_canonical_json, text


def evidence(ledger: EventStore, identifier: str) -> EvidenceRecord:
    from .validation import task_id
    task_id(identifier)
    with ledger._connection(write=False) as connection:
        row = connection.execute("SELECT * FROM attempt_evidence WHERE evidence_id=?", (identifier,)).fetchone()
        if row is None:
            raise ExecutionGuardError("required delivery evidence is missing")
        return ledger._decode_evidence(row)


def provider_response(ledger: EventStore, snapshot: DeliverySnapshot, role: Role,
                      provider_evidence_id: str) -> tuple[dict[str, Any], str]:
    attempt = ledger.replay_attempt(snapshot.active_attempt_id)
    task = ledger.replay(snapshot.active_task_id)
    if (attempt.task_id, attempt.role, attempt.provider, attempt.workspace_root, attempt.status, attempt.returncode) != (
            task.spec.task_id, role, snapshot.spec.provider(role), snapshot.spec.workspace_root, AttemptStatus.SUCCEEDED, 0):
        raise ExecutionGuardError("delivery approval has no authorized successful role attempt")
    if (task.spec.role != role or task.spec.provider != attempt.provider or ledger.get(task.spec.task_id) != task
            or task.status is not TaskStatus.CREATED or task.revision != attempt.task_revision):
        raise ExecutionGuardError("role task authorization changed")
    record = evidence(ledger, provider_evidence_id)
    payload = parse_canonical_json(record.payload_json)
    if record.kind != "provider_result" or record.attempt_id != attempt.attempt_id:
        raise ValidationError("role evidence belongs to another attempt")
    if (payload.get("execution_status"), payload.get("result_status"), payload.get("cleanup_complete"),
            payload.get("session_id"), payload.get("stdout_sha256")) != (
            "succeeded", "valid", True, attempt.provider_session_id, attempt.stdout_sha256):
        raise ExecutionGuardError("role identity, output or cleanup evidence is unproven")
    if payload.get("cleanup_complete") is not True or type(attempt.returncode) is not int:
        raise ExecutionGuardError("role cleanup and exit must have strict validated types")
    session = attempt.provider_session_id
    text(session, "fresh delivery session identity", 4096)
    authors = [author for component in parse_canonical_json(snapshot.spec.components_json) for author in component["authors"]]
    if any(not author["session_id"] for author in authors):
        raise ExecutionGuardError("author session identity is unknown; independent acceptance is unproven")
    previous = [(author["provider"], author["session_id"]) for author in authors]
    previous += [(item["provider"], item["session_id"]) for item in parse_canonical_json(snapshot.reports_json).values()]
    if (attempt.provider.value, session) in previous:
        raise ExecutionGuardError("delivery stage reused an author or preceding stage session")
    raw = payload.get("structured_output") or payload.get("content")
    response = json.loads(raw)
    if type(response) is not dict:
        raise ValidationError("role response must be an object")
    return response, session


def verified_tests(ledger: EventStore, snapshot: DeliverySnapshot, role: Role, identifier: str) -> dict[str, Any]:
    record = evidence(ledger, identifier)
    data = dict(exact_keys(parse_canonical_json(record.payload_json), {"phase", "candidate_digest", "tests", "passed"}, "delivery tests"))
    reports = parse_canonical_json(snapshot.reports_json)
    index = ROLES.index(role)
    owner = snapshot.active_attempt_id if index == 0 else reports[ROLES[index - 1].value]["attempt_id"]
    if (record.kind, record.attempt_id, data["phase"], data["candidate_digest"]) != (
            "required_tests", owner, f"delivery:{role.value}", digest_text(snapshot.manifest_json)):
        raise ValidationError("delivery tests are stale or do not belong to the required stage")
    if type(data["tests"]) is not list or len(data["tests"]) != len(snapshot.spec.tests):
        raise ValidationError("delivery tests do not cover all required commands")
    records = ledger.evidence(ledger.get_attempt(owner).task_id)
    for test, outcome in zip(snapshot.spec.tests, data["tests"]):
        if outcome["name"] != test.name or outcome["argv"] != list(test.argv) or outcome["cleanup_complete"] is not True:
            raise ValidationError("delivery test command or cleanup is unproven")
        if type(outcome["returncode"]) is not int or outcome["execution_status"] not in {"succeeded", "nonzero"}:
            raise ExecutionGuardError("test infrastructure did not produce an ordinary executable result")
        passed = outcome["execution_status"] == "succeeded" and outcome["returncode"] == 0
        if outcome["passed"] is not passed:
            raise ValidationError("test pass claim contradicts actual exit")
        result = canonical_json({"phase": data["phase"], "candidate_digest": data["candidate_digest"], "outcome": outcome})
        intent = canonical_json({"phase": data["phase"], "candidate_digest": data["candidate_digest"],
                                "test": test.to_dict(), "started_at": outcome["started_at"],
                                "environment": {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": "", "PYTHONHOME": "", "PYTHONNOUSERSITE": "1"}})
        if not any(item.attempt_id == owner and item.kind == "test_result" and item.payload_json == result for item in records):
            raise ExecutionGuardError("aggregate verification is missing actual individual test evidence")
        if not any(item.attempt_id == owner and item.kind == "test_launch_intent" and item.payload_json == intent for item in records):
            raise ExecutionGuardError("aggregate verification is missing actual test launch intent")
    if data["passed"] is not all(item["passed"] for item in data["tests"]):
        raise ValidationError("aggregate test pass flag contradicts its outcomes")
    return data


def findings(snapshot: DeliverySnapshot, value: object) -> list[dict[str, Any]]:
    if type(value) is not list or len(value) > 32:
        raise ValidationError("findings must be a bounded array")
    result = []
    known = requirements(snapshot.spec.requirements_json, snapshot.spec.tests)
    paths = snapshot.spec.work_spec().owned_paths
    manifest = parse_canonical_json(snapshot.manifest_json)
    for item in value:
        data = dict(exact_keys(item, {"finding_id", "requirement_id", "severity", "relative_path", "line", "description"}, "finding"))
        command_id(data["finding_id"])
        if data["requirement_id"] not in known or data["severity"] not in {"blocking", "note"} or data["relative_path"] not in paths:
            raise ValidationError("finding must cite an accepted requirement and integrated source path")
        if data["relative_path"] not in manifest:
            raise ValidationError("finding source does not exist in candidate")
        bounded_int(data["line"], "finding line", 1, 100000)
        text(data["description"], "finding description", 2048)
        if any(previous["finding_id"] == data["finding_id"] for previous in result):
            raise ValidationError("finding IDs must be distinct")
        result.append(data)
    return result


def resolutions(snapshot: DeliverySnapshot, value: object) -> None:
    inherited = parse_canonical_json(snapshot.inherited_findings_json)
    if type(value) is not list or len(value) != len(inherited):
        raise ValidationError("every inherited blocking finding requires fresh candidate-bound resolution")
    prior = parse_canonical_json(snapshot.prior_manifest_json) if snapshot.prior_manifest_json is not None else {}
    current = parse_canonical_json(snapshot.manifest_json)
    resolved = set()
    for item in value:
        data = exact_keys(item, {"finding_id", "relative_path", "sha256", "description"}, "finding resolution")
        finding = next((f for f in inherited if f["finding_id"] == data["finding_id"]), None)
        if finding is None or data["finding_id"] in resolved or finding["relative_path"] != data["relative_path"]:
            raise ValidationError("resolution must identify one actually inherited finding and its source")
        relative = data["relative_path"]
        if current.get(relative, {}).get("sha256") != data["sha256"] or prior.get(relative) == current.get(relative):
            raise ValidationError("resolution has no changed verified source bytes")
        text(data["description"], "resolution explanation", 2048)
        resolved.add(data["finding_id"])


def gate(ledger: EventStore, snapshot: DeliverySnapshot, role: Role, provider_id: str, tests_id: str) -> dict[str, Any]:
    response, session = provider_response(ledger, snapshot, role, provider_id)
    checked = verified_tests(ledger, snapshot, role, tests_id)
    found = []
    if role is not Role.INTEGRATOR:
        exact_keys(response, {"candidate_digest", "evidence_digest", "verdict", "checked_requirements", "findings", "resolutions", "summary"}, "acceptance response")
        if response["candidate_digest"] != digest_text(snapshot.manifest_json) or response["evidence_digest"] != evidence(ledger, tests_id).digest_sha256:
            raise ValidationError("approval cites a stale candidate or forged test evidence")
        if response["verdict"] not in {"PASS", "REJECT"} or type(response["checked_requirements"]) is not list:
            raise ValidationError("approval verdict or checked requirements are invalid")
        if sorted(response["checked_requirements"]) != sorted(requirements(snapshot.spec.requirements_json, snapshot.spec.tests)):
            raise ValidationError("approval must check every acceptance requirement exactly once")
        text(response["summary"], "approval summary", 4096)
        found = findings(snapshot, response["findings"])
        if response["verdict"] == "PASS":
            resolutions(snapshot, response["resolutions"])
    passed = checked["passed"] and not any(item["severity"] == "blocking" for item in found)
    passed = passed and (role is Role.INTEGRATOR or response["verdict"] == "PASS")
    if not checked["passed"]:
        requirement_id = next(iter(requirements(snapshot.spec.requirements_json, snapshot.spec.tests)))
        found.append({"finding_id": f"test-failure-{snapshot.spec.delivery_id}", "requirement_id": requirement_id,
                      "severity": "blocking", "relative_path": snapshot.spec.work_spec().owned_paths[0], "line": 1,
                      "description": "Controller-run required tests failed; provider approval cannot override executable results."})
    return {"role": role.value, "provider": snapshot.spec.provider(role).value, "session_id": session,
            "attempt_id": snapshot.active_attempt_id, "candidate_digest": digest_text(snapshot.manifest_json),
            "provider_evidence_id": provider_id, "tests_evidence_id": tests_id, "response": response,
            "findings": found, "passed": passed}
