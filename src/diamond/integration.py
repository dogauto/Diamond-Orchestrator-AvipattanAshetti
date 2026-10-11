import hashlib
from pathlib import Path
from typing import Any

from .adapters import OutputMode
from .domain import Provider, Role, TaskStatus
from .errors import ConflictError, ExecutionGuardError, ValidationError
from .evidence import AttemptStatus, EvidenceRecord, digest_text
from .packets import ArtifactRef, _digest, _relative_path
from .store import EventStore
from .validation import bounded_int, canonical_json, exact_keys, parse_canonical_json, task_id, text, utc_timestamp
from .workflow_contracts import WorkflowSnapshot, WorkflowStatus
from .workflow_store import WorkflowStore
from .workspace import workspace_manifest


_BUDGET = 32768
_COMPONENT_KEYS = {"workflow_id", "revision", "candidate_digest", "verification_evidence_id", "changes", "authors"}
_CHANGE_KEYS = {"relative_path", "base_sha256", "content", "sha256", "size_bytes"}
_AUTHOR_KEYS = {"attempt_id", "task_id", "provider", "role", "session_id"}


def _object(value: object, keys: set[str], name: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValidationError(f"{name} must be a JSON object")
    return dict(exact_keys(value, keys, name))


def _array(value: object, name: str, minimum: int, maximum: int) -> list[Any]:
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise ValidationError(f"{name} must be a JSON array of {minimum}..{maximum} entries")
    return value


def _utf8(value: object, name: str, maximum: int) -> bytes:
    if type(value) is not str or len(value) > maximum or "\x00" in value:
        raise ValidationError(f"{name} must be bounded UTF-8 text without NUL")
    try:
        return value.encode("utf-8")
    except UnicodeError as exc:
        raise ValidationError(f"{name} must be UTF-8") from exc


def _string(value: object, name: str, maximum: int) -> str:
    _utf8(value, name, maximum)
    text(value, name, maximum)
    return value


def _path(value: object, *, visible: bool = True) -> str:
    relative = _string(value, "relative path", 4096)
    _relative_path(relative, "relative path")
    path = Path(relative)
    if not path.parts or str(path) != relative or visible and any(part.startswith(".") for part in path.parts):
        raise ValidationError("source paths must be canonical, visible relative paths")
    return relative


def _manifest(value: str) -> dict[str, Any]:
    entries = parse_canonical_json(_string(value, "manifest JSON", 1_048_576))
    if type(entries) is not dict or len(entries) > 2048:
        raise ValidationError("workspace manifest must be a bounded canonical object")
    total = 0
    for relative, entry in entries.items():
        _path(relative, visible=False)
        if type(entry) is not dict or type(entry.get("kind")) is not str or entry["kind"] not in {"file", "directory"}:
            raise ValidationError("manifest entry must describe a file or directory")
        if entry["kind"] == "file":
            _object(entry, {"kind", "sha256", "size_bytes"}, "manifest file")
            _digest(entry["sha256"], "manifest file digest")
            total += bounded_int(entry["size_bytes"], "manifest file size", 0, 32 * 1024 * 1024)
        else:
            _object(entry, {"kind"}, "manifest directory")
        for parent in Path(relative).parents:
            if str(parent) != "." and entries.get(str(parent)) != {"kind": "directory"}:
                raise ValidationError("manifest file parents must be recorded directories")
    if total > 32 * 1024 * 1024:
        raise ValidationError("workspace manifest exceeds 32 MiB")
    return entries


def _base(entries: dict[str, Any], relative: str) -> str | None:
    existing = entries.get(relative)
    if existing is not None and existing["kind"] != "file":
        raise ValidationError("source change cannot overwrite an old directory")
    if any(entries.get(str(parent), {}).get("kind") == "file" for parent in Path(relative).parents):
        raise ValidationError("source change cannot replace a file ancestor")
    return existing["sha256"] if existing else None


def _workspace(root: Path, expected: str) -> None:
    try:
        actual = workspace_manifest(root)
    except (OSError, RuntimeError) as exc:
        raise ValidationError("workspace must remain an existing canonical directory") from exc
    if actual != expected:
        raise ConflictError("workspace manifest changed since acceptance or verification")


def _artifacts(value: object, manifest: dict[str, Any], owned: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    result = {}
    for item in _array(value, "published artifacts", 1, len(owned)):
        data = _object(item, {"name", "relative_path", "digest_sha256", "size_bytes", "kind"}, "source artifact")
        artifact = ArtifactRef(**data)
        relative = _path(artifact.relative_path)
        if relative not in owned or relative in result or artifact.name != relative or artifact.kind != "source":
            raise ValidationError("published artifact must identify a distinct owned source file")
        if manifest.get(relative) != {"kind": "file", "sha256": artifact.digest_sha256, "size_bytes": artifact.size_bytes}:
            raise ConflictError("published artifact does not match the candidate manifest")
        result[relative] = data
    return result


def _one(records: tuple[EvidenceRecord, ...], kind: str) -> EvidenceRecord:
    selected = [record for record in records if record.kind == kind]
    if len(selected) != 1:
        raise ExecutionGuardError(f"component requires exactly one controller {kind} evidence record")
    return selected[0]


def _author(ledger: EventStore, snapshot: WorkflowSnapshot, identity: dict[str, Any], role: Role) -> tuple[dict[str, Any], tuple[EvidenceRecord, ...]]:
    attempt = ledger.replay_attempt(task_id(identity["attempt_id"]))
    task = ledger.replay(task_id(identity["task_id"]))
    spec = snapshot.spec
    provider = spec.builder_provider if role is Role.BUILDER else spec.debugger_provider
    if task != ledger.get(task.spec.task_id) or task.status is not TaskStatus.CREATED or task.revision != attempt.task_revision:
        raise ConflictError("author task authorization differs from its immutable creation")
    if (attempt.task_id, attempt.role, attempt.provider, attempt.workspace_root) != (task.spec.task_id, role, provider, spec.workspace_root):
        raise ValidationError("workflow author identity, role, provider or workspace is unauthorized")
    if (task.spec.role, task.spec.provider, task.spec.objective, task.spec.required_tests, task.spec.owned_paths,
            task.spec.allowed_tools, task.spec.timeout_seconds) != (role, provider, spec.objective,
            tuple(test.name for test in spec.tests), (), (attempt.executable,), spec.timeout_seconds):
        raise ValidationError("workflow author does not match the actual created task specification")
    if (attempt.status is not AttemptStatus.SUCCEEDED or type(attempt.returncode) is not int or attempt.returncode != 0
            or attempt.result_status != "valid" or attempt.started_at is None or attempt.completed_at is None):
        raise ExecutionGuardError("published source author has no successful immutable provider attempt")
    records = tuple(record for record in ledger.evidence(task.spec.task_id) if record.attempt_id == attempt.attempt_id)
    result = parse_canonical_json(_one(records, "provider_result").payload_json)
    _object(result, {"schema_version", "argv", "cwd", "provider", "requested_model", "cli_version", "started_at", "completed_at",
        "output_mode", "returncode", "execution_status", "result_status", "stdout", "stderr", "stdout_bytes", "stderr_bytes",
        "stdout_sha256", "stderr_sha256", "stdout_saved_sha256", "stderr_saved_sha256", "content", "structured_output",
        "reported_model", "session_id", "artifact_refs", "artifacts_verified", "artifact_error", "stdout_truncated",
        "stderr_truncated", "capture_limited", "cleanup_complete", "redacted_fields", "detail"}, "provider evidence")
    for field, expected in {"schema_version": 1, "provider": provider.value, "cwd": spec.workspace_root,
            "requested_model": attempt.requested_model, "cli_version": attempt.cli_version, "started_at": attempt.started_at,
            "completed_at": attempt.completed_at, "session_id": attempt.provider_session_id, "reported_model": attempt.reported_model,
            "result_status": "valid", "execution_status": "succeeded", "returncode": 0, "stdout_bytes": attempt.stdout_bytes,
            "stderr_bytes": attempt.stderr_bytes, "stdout_sha256": attempt.stdout_sha256, "stderr_sha256": attempt.stderr_sha256}.items():
        if result[field] != expected or type(result[field]) is not type(expected):
            raise ValidationError("provider evidence differs from immutable author identity or output digest")
    if result["cleanup_complete"] is not True or result["artifacts_verified"] is not True or result["artifact_error"] is not None:
        raise ExecutionGuardError("provider cleanup and output validation must be proven")
    argv = _array(result["argv"], "provider argv", 1, 256)
    for argument in argv:
        _utf8(argument, "provider argument", 4096)
    if argv[0] != attempt.executable or type(result["output_mode"]) is not str or result["output_mode"] not in {mode.value for mode in OutputMode}:
        raise ValidationError("provider evidence must retain its actual executable and output mode")
    _array(result["artifact_refs"], "provider artifact refs", 0, 0)
    for field in ("stdout_truncated", "stderr_truncated", "capture_limited"):
        if type(result[field]) is not bool:
            raise ValidationError("provider capture flags must be booleans")
    if result["capture_limited"]:
        raise ExecutionGuardError("provider output capture was incomplete")
    for field in _array(result["redacted_fields"], "provider redacted fields", 0, len(result)):
        if type(field) is not str or field not in result:
            raise ValidationError("provider redaction metadata must identify recorded fields")
    for field in ("content", "structured_output", "detail"):
        if result[field] is not None:
            _utf8(result[field], "retained provider text", 32768 if field != "detail" else 4096)
    for stream in ("stdout", "stderr"):
        if hashlib.sha256(_utf8(result[stream], "retained provider output", 32768)).hexdigest() != result[f"{stream}_saved_sha256"]:
            raise ValidationError("retained provider output digest is invalid")
        _digest(result[f"{stream}_sha256"], "provider output digest")
    session = attempt.provider_session_id
    if session is not None:
        _string(session, "author session", 4096)
    return {"attempt_id": attempt.attempt_id, "task_id": task.spec.task_id, "provider": provider.value,
            "role": role.value, "session_id": session}, records


def _verified(snapshot: WorkflowSnapshot, records: tuple[EvidenceRecord, ...], active_artifacts: list[Any], role: Role) -> str:
    verification = _one(records, "workflow_verification")
    data = _object(parse_canonical_json(verification.payload_json), {"candidate_digest", "artifacts", "passed",
        "missing_artifacts", "tests_digest", "failure_packet"}, "workflow verification")
    candidate = digest_text(snapshot.manifest_json)
    if data["candidate_digest"] != candidate or data["passed"] is not True or data["missing_artifacts"] != [] or data["failure_packet"] is not None:
        raise ExecutionGuardError("workflow verification must pass for the exact final candidate")
    _artifacts(data["artifacts"], _manifest(snapshot.manifest_json), snapshot.spec.owned_paths)
    if canonical_json(data["artifacts"]) != canonical_json(active_artifacts):
        raise ValidationError("workflow verification does not retain the active publication artifacts")
    report = _one(records, "required_tests")
    tests = _object(parse_canonical_json(report.payload_json), {"phase", "candidate_digest", "tests", "passed"}, "required tests")
    phase = "post-build" if role is Role.BUILDER else "post-fix"
    if tests["phase"] != phase or tests["candidate_digest"] != candidate or tests["passed"] is not True or data["tests_digest"] != report.digest_sha256:
        raise ExecutionGuardError("final required tests are not bound to verified candidate and tests digest")
    outcomes = _array(tests["tests"], "required test outcomes", len(snapshot.spec.tests), len(snapshot.spec.tests))
    results = [record for record in records if record.kind == "test_result"]
    launches = [record for record in records if record.kind == "test_launch_intent"]
    if len(results) != len(outcomes) or len(launches) != len(outcomes):
        raise ExecutionGuardError("required tests must retain every individual controller outcome and launch")
    for test, value in zip(snapshot.spec.tests, outcomes):
        outcome = _object(value, {"name", "argv", "started_at", "completed_at", "execution_status", "returncode",
            "cleanup_complete", "stdout", "stderr", "stdout_sha256", "stderr_sha256", "passed"}, "test outcome")
        if (outcome["name"] != test.name or outcome["argv"] != list(test.argv) or outcome["execution_status"] != "succeeded"
                or type(outcome["returncode"]) is not int or outcome["returncode"] != 0
                or outcome["cleanup_complete"] is not True or outcome["passed"] is not True):
            raise ExecutionGuardError("every required test must actually succeed with proven cleanup")
        utc_timestamp(outcome["started_at"])
        utc_timestamp(outcome["completed_at"])
        if outcome["completed_at"] < outcome["started_at"] or report.created_at < outcome["completed_at"] or verification.created_at < report.created_at:
            raise ValidationError("test and verification evidence chronology is invalid")
        for stream in ("stdout", "stderr"):
            _utf8(outcome[stream], "retained test output", 8192)
            _digest(outcome[f"{stream}_sha256"], "test output digest")
        expected_result = {"candidate_digest": candidate, "phase": phase, "outcome": outcome}
        expected_launch = {"candidate_digest": candidate, "phase": phase, "test": test.to_dict(), "started_at": outcome["started_at"],
                           "environment": {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(snapshot.spec.workspace_root), "PYTHONHOME": "", "PYTHONNOUSERSITE": "1"}}
        if sum(item.payload_json == canonical_json(expected_result) for item in results) != 1 or sum(
                item.payload_json == canonical_json(expected_launch) for item in launches) != 1:
            raise ExecutionGuardError("aggregate tests do not match actual individual test evidence")
    return verification.evidence_id


def _history(ledger: EventStore, snapshot: WorkflowSnapshot) -> tuple[list[Any], dict[str, Any], str]:
    with ledger._connection(write=False) as connection:
        rows = connection.execute("SELECT workflow_events.*,workflow_commands.request_json FROM workflow_events "
            "JOIN workflow_commands USING(command_id) WHERE workflow_id=? ORDER BY revision", (snapshot.spec.workflow_id,)).fetchall()
    if len(rows) != snapshot.revision:
        raise ConflictError("workflow history changed during component capture")
    manifest = _manifest(snapshot.spec.initial_manifest_json)
    authors, latest = [], {}
    identity, records, publication = None, (), None
    role, verification_id = Role.BUILDER, None
    for row in rows:
        payload = parse_canonical_json(row["payload_json"])
        event = row["event_type"]
        if event == "CREATED":
            data = _object(payload, {"spec"}, "workflow creation")
            if data["spec"] != snapshot.spec.to_dict():
                raise ValidationError("component must use the actual created workflow specification")
        elif event == "STARTED":
            identity = _object(payload, {"task_id", "attempt_id"}, "workflow start")
            role = Role.DEBUGGER if authors else Role.BUILDER
            author, records = _author(ledger, snapshot, identity, role)
            if any(item["attempt_id"] == author["attempt_id"] or item["task_id"] == author["task_id"] for item in authors):
                raise ValidationError("workflow author identities cannot be reused")
            publication = None
        elif event == "FILES_APPLIED":
            if identity is None or publication is not None:
                raise ValidationError("publication requires one actual preceding active workflow start")
            data = _object(payload, {"manifest_json", "artifacts"}, "workflow publication")
            request = _object(parse_canonical_json(row["request_json"]), {"operation", "workflow_id", "expected_revision", "response_digest"}, "publication command")
            if (request["operation"] != "publish_files" or request["workflow_id"] != snapshot.spec.workflow_id
                    or type(request["expected_revision"]) is not int or request["expected_revision"] != row["revision"] - 1):
                raise ValidationError("source publication must retain its actual controller authorization")
            _digest(request["response_digest"], "published response digest")
            after = _manifest(data["manifest_json"])
            artifacts = _artifacts(data["artifacts"], after, snapshot.spec.owned_paths)
            parents = {str(parent) for path in artifacts for parent in Path(path).parents if str(parent) != "."}
            for relative in set(manifest) | set(after):
                if manifest.get(relative) == after.get(relative):
                    continue
                if relative in artifacts:
                    _base(manifest, relative)
                elif relative not in parents or relative in manifest or after.get(relative) != {"kind": "directory"}:
                    raise ValidationError("publication changed or deleted an unauthorized workspace entry")
            authors.append(author)
            latest.update(artifacts)
            manifest, publication = after, data["artifacts"]
        elif event == "FINISHED":
            data = _object(payload, {"passed", "manifest_json", "failure", "detail"}, "workflow finish")
            if publication is None or data["manifest_json"] != canonical_json(manifest):
                raise ValidationError("workflow finish has no authorized published candidate")
            if row["revision"] == snapshot.revision:
                if identity != {"task_id": snapshot.active_task_id, "attempt_id": snapshot.active_attempt_id} or data["passed"] is not True:
                    raise ValidationError("verified workflow must identify its actual last active author")
                verification_id = _verified(snapshot, records, publication, role)
            identity = None
        else:
            raise ExecutionGuardError("component history contains an unverified workflow event")
    if verification_id is None or canonical_json(manifest) != snapshot.manifest_json:
        raise ExecutionGuardError("component has no final controller verification")
    _artifacts(list(latest.values()), manifest, snapshot.spec.owned_paths)
    return authors, latest, verification_id


def capture_components(ledger: EventStore, references: tuple[tuple[str, int], ...], target: Path, base_manifest_json: str) -> str:
    if not isinstance(ledger, EventStore) or not isinstance(target, Path) or not target.is_absolute():
        raise ValidationError("component capture requires an EventStore and absolute target Path")
    if type(references) is not tuple or not 1 <= len(references) <= 8:
        raise ValidationError("component references must be an immutable tuple of 1..8 references")
    identifiers = set()
    for reference in references:
        if type(reference) is not tuple or len(reference) != 2:
            raise ValidationError("component reference must be a workflow ID/revision tuple")
        identifier, revision = reference
        task_id(identifier)
        bounded_int(revision, "component revision", 1, 2**63 - 1)
        if identifier in identifiers:
            raise ValidationError("component workflows must be distinct")
        identifiers.add(identifier)
    base = _manifest(base_manifest_json)
    _workspace(target, base_manifest_json)
    workflows, snapshots, components = WorkflowStore(ledger), [], []
    for identifier, revision in references:
        snapshot = workflows.replay(identifier)
        if snapshot.revision != revision:
            raise ConflictError("component workflow revision is stale")
        if snapshot.status is not WorkflowStatus.VERIFIED or snapshot.failure is not None:
            raise ExecutionGuardError("components require a controller-verified workflow")
        initial, final = _manifest(snapshot.spec.initial_manifest_json), _manifest(snapshot.manifest_json)
        authors, publications, verification_id = _history(ledger, snapshot)
        changes = []
        for relative in sorted(snapshot.spec.owned_paths):
            before = _base(initial, relative)
            existing = final.get(relative)
            if existing is None or existing["kind"] != "file":
                raise ValidationError("verified source must retain every owned file; deletion is unsupported")
            if before == existing["sha256"]:
                continue
            if relative not in publications:
                raise ValidationError("source change has no actual workflow publication")
            if _base(base, relative) != before:
                raise ConflictError("component source base digest differs from the target base")
            changes.append({"relative_path": relative, "base_sha256": before, "content": "",
                            "sha256": existing["sha256"], "size_bytes": existing["size_bytes"]})
        if not changes:
            raise ValidationError("each component must contain at least one changed owned source file")
        components.append({"workflow_id": identifier, "revision": revision, "candidate_digest": digest_text(snapshot.manifest_json),
            "verification_evidence_id": verification_id, "changes": changes, "authors": authors})
        snapshots.append(snapshot)
    if len(canonical_json(components).encode("utf-8")) + sum(change["size_bytes"] for item in components for change in item["changes"]) > _BUDGET:
        raise ValidationError("serialized component source exceeds the 32 KiB integration-context budget")
    for snapshot, component in zip(snapshots, components):
        root = Path(snapshot.spec.workspace_root)
        _workspace(root, snapshot.manifest_json)
        for change in component["changes"]:
            path = root / change["relative_path"]
            try:
                if path.resolve(strict=True) != path:
                    raise ValidationError("source path is not canonical")
                with path.open("rb") as source:
                    contents = source.read(change["size_bytes"] + 1)
                if len(contents) != change["size_bytes"] or hashlib.sha256(contents).hexdigest() != change["sha256"]:
                    raise ConflictError("source bytes changed after workflow verification")
                change["content"] = contents.decode("utf-8")
            except (OSError, UnicodeError, RuntimeError) as exc:
                raise ValidationError("component source must remain a canonical UTF-8 file") from exc
        _workspace(root, snapshot.manifest_json)
    result = canonical_json(components)
    _components(result, base_manifest_json)
    for snapshot in snapshots:
        _workspace(Path(snapshot.spec.workspace_root), snapshot.manifest_json)
        if workflows.replay(snapshot.spec.workflow_id) != snapshot:
            raise ConflictError("workflow authorization changed during component capture")
    _workspace(target, base_manifest_json)
    return result


def _components(value: str, base_manifest_json: str) -> dict[str, dict[str, Any]]:
    value = _string(value, "components JSON", _BUDGET)
    if len(value.encode("utf-8")) > _BUDGET:
        raise ValidationError("serialized components exceed the 32 KiB integration-context budget")
    base = _manifest(base_manifest_json)
    result, attempts, tasks, evidence_ids, paths = {}, set(), set(), set(), set()
    for item in _array(parse_canonical_json(value), "components", 1, 8):
        component = _object(item, _COMPONENT_KEYS, "component")
        identifier = task_id(component["workflow_id"])
        bounded_int(component["revision"], "component revision", 1, 2**63 - 1)
        _digest(component["candidate_digest"], "component candidate digest")
        evidence_id = task_id(component["verification_evidence_id"])
        if identifier in result or evidence_id in evidence_ids:
            raise ValidationError("component workflow and verification identities must be distinct")
        evidence_ids.add(evidence_id)
        for index, item in enumerate(_array(component["authors"], "component authors", 1, 101)):
            author = _object(item, _AUTHOR_KEYS, "component author")
            attempt, task = task_id(author["attempt_id"]), task_id(author["task_id"])
            if attempt in attempts or task in tasks or type(author["provider"]) is not str or author["provider"] not in {p.value for p in Provider}:
                raise ValidationError("author identity or provider is invalid or duplicated")
            if author["role"] != ("builder" if index == 0 else "debugger"):
                raise ValidationError("author lineage must retain its Builder and subsequent Debuggers")
            if author["session_id"] is not None:
                _string(author["session_id"], "author session", 4096)
            attempts.add(attempt)
            tasks.add(task)
        changes = {}
        for item in _array(component["changes"], "component changes", 1, 32):
            change = _object(item, _CHANGE_KEYS, "component change")
            relative = _path(change["relative_path"])
            if relative in changes:
                raise ValidationError("component changes contain a duplicate path")
            if change["base_sha256"] is not None:
                _digest(change["base_sha256"], "source base digest")
            if _base(base, relative) != change["base_sha256"]:
                raise ConflictError("immutable component base differs from the supplied target base")
            encoded = _utf8(change["content"], "component source", _BUDGET)
            size = bounded_int(change["size_bytes"], "component source size", 0, _BUDGET)
            digest = _digest(change["sha256"], "component source digest")
            if len(encoded) != size or hashlib.sha256(encoded).hexdigest() != digest or change["base_sha256"] == digest:
                raise ValidationError("immutable component source size/digest is invalid or unchanged")
            changes[relative] = change
            paths.add(relative)
        result[identifier] = changes
    if any(str(parent) in paths for relative in paths for parent in Path(relative).parents):
        raise ValidationError("component source paths must not overlap file ancestors")
    return result


def integration_response(components_json: str, base_manifest_json: str, response: object) -> dict[str, object]:
    components = _components(components_json, base_manifest_json)
    data = _object(response, {"base_digest", "decisions", "summary"}, "Integrator response")
    _digest(data["base_digest"], "integration base digest")
    if data["base_digest"] != digest_text(base_manifest_json):
        raise ConflictError("Integrator response is bound to a stale target base")
    summary = _string(data["summary"], "integration summary", 4096)
    paths = {relative for changes in components.values() for relative in changes}
    choices = {}
    for item in _array(data["decisions"], "integration decisions", len(paths), len(paths)):
        decision = _object(item, {"relative_path", "workflow_id", "reason"}, "integration decision")
        relative, identifier = _path(decision["relative_path"]), task_id(decision["workflow_id"])
        _string(decision["reason"], "integration decision reason", 2048)
        if relative in choices or identifier not in components or relative not in components[identifier]:
            raise ValidationError("integration decision must choose one known component owning that distinct path")
        change = components[identifier][relative]
        choices[relative] = {"relative_path": relative, "base_sha256": change["base_sha256"], "content": change["content"]}
    if set(choices) != paths:
        raise ValidationError("integration decisions must explicitly cover every changed source path")
    return {"files": [choices[relative] for relative in sorted(choices)], "summary": summary}
