import base64
import fcntl
import hashlib
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from stat import S_IMODE, S_ISREG

from .delivery_store import DeliveryStore
from .errors import ConflictError, ExecutionGuardError, StorageError, ValidationError
from .evidence import digest_text
from .improvement_store import ImprovementStore, source_manifest
from .occupancy import protect_delivery_roots, protect_workspace_writers
from .self_improve import validate_owned_paths
from .test_inputs import verify_test_inputs
from .validation import canonical_json, parse_canonical_json
from .workflow_contracts import WorkflowStatus
from .workflow_store import WorkflowStore
from .workspace import workspace_manifest


@contextmanager
def project_lock(project: Path):
    if project.resolve(strict=True) != project:
        raise ValidationError("publication requires a canonical project root")
    descriptor = os.open(project / ".diamond-publication.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not S_ISREG(os.fstat(descriptor).st_mode):
            raise ConflictError("publication lock must be a regular file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ConflictError("another self-improvement operation owns this project") from exc
        yield
    finally:
        os.close(descriptor)


def file_state(path: Path) -> tuple[bytes, int]:
    if path.is_symlink() or path.parent.resolve(strict=True) != path.parent or not path.is_file():
        raise ConflictError("publication file is missing or changed through a symlink")
    info = path.stat()
    if not S_ISREG(info.st_mode):
        raise ConflictError("publication requires regular files")
    return path.read_bytes(), S_IMODE(info.st_mode)


def _replace(path: Path, expected: tuple[bytes, int], replacement: tuple[bytes, int]) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".diamond-improve-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(replacement[0])
            handle.flush()
            os.fchmod(handle.fileno(), replacement[1])
            os.fsync(handle.fileno())
        if file_state(path) != expected:
            raise ConflictError("source changed during publication; preserve intervening edit")
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def _pack(state: tuple[bytes, int]) -> dict:
    content, mode = state
    return {"content_base64": base64.b64encode(content).decode("ascii"), "mode": mode,
            "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)}


def _unpack(data: dict) -> tuple[bytes, int]:
    try:
        content = base64.b64decode(data["content_base64"], validate=True)
        if hashlib.sha256(content).hexdigest() != data["sha256"] or len(content) != data["size_bytes"]:
            raise ValueError("digest mismatch")
        if type(data["mode"]) is not int or not 0 <= data["mode"] <= 0o7777:
            raise ValueError("invalid file mode")
        return content, data["mode"]
    except (KeyError, ValueError, TypeError) as exc:
        raise StorageError("invalid publication file record") from exc


def _identity(project, checkpoint, ledger, workflow_id):
    saved = ImprovementStore(ledger).checkpoint(workflow_id, project)
    workflow = WorkflowStore(ledger).replay(workflow_id)
    if checkpoint.resolve(strict=True) != checkpoint or Path(workflow.spec.workspace_root) != checkpoint / "workspace":
        raise ConflictError("checkpoint does not match its recorded workflow")
    validate_owned_paths(project, workflow.spec.owned_paths)
    return saved, workflow


def publish(project, checkpoint, ledger, workflow_id, delivery_id, config_digest):
    store = ImprovementStore(ledger)
    with project_lock(project):
        saved, workflow = _identity(project, checkpoint, ledger, workflow_id)
        store.require_active(workflow_id)
        previous = store.publication(workflow_id)
        if previous is not None:
            if previous["delivery_id"] != delivery_id or previous["status"] != "PUBLISHED":
                raise ConflictError("publication already attempted; reconcile it before creating new work")
            for item in previous["intent"]["files"]:
                if file_state(project / item["path"]) != _unpack(item["candidate"]):
                    raise ConflictError("published source has changed; no source was written")
            return previous
        if workflow.status is not WorkflowStatus.VERIFIED:
            raise ExecutionGuardError("publication requires a verified component")
        deliveries = DeliveryStore(ledger)
        certificate = deliveries.certificate(delivery_id, config_digest)
        delivery = deliveries.replay(delivery_id)
        if Path(delivery.spec.workspace_root) != checkpoint / "review":
            raise ExecutionGuardError("delivery must review this improvement's isolated target")
        components = parse_canonical_json(delivery.spec.components_json)
        if len(components) != 1 or (components[0]["workflow_id"], components[0]["revision"]) != (workflow_id, workflow.revision):
            raise ExecutionGuardError("delivery acceptance is not for this exact workflow revision")
        if saved["config_digest"] != config_digest or workflow.spec.config_digest != config_digest:
            raise ConflictError("configuration changed since improvement acceptance")
        if source_manifest(project) != saved["base_manifest_json"]:
            raise ConflictError("live source, dependencies, tests or policy changed since acceptance")
        if workspace_manifest(checkpoint / "workspace") != workflow.manifest_json:
            raise ConflictError("verified component changed after verification")
        if delivery.spec.tests != workflow.spec.tests:
            raise ExecutionGuardError("delivery must retain the original trusted verification commands")
        base = parse_canonical_json(workflow.spec.initial_manifest_json)
        accepted = parse_canonical_json(certificate["manifest_json"])
        if any(base.get(path) != accepted.get(path) for path in base.keys() | accepted.keys()
               if path not in workflow.spec.owned_paths):
            raise ConflictError("review target changed outside owned source")
        files = []
        for path in workflow.spec.owned_paths:
            original, candidate = file_state(project / path), file_state(checkpoint / "review" / path)
            if hashlib.sha256(candidate[0]).hexdigest() != accepted[path]["sha256"]:
                raise ConflictError("candidate bytes differ from independent acceptance")
            # Source-only improvement preserves the live file's permissions.
            files.append({"path": path, "original": _pack(original), "candidate": _pack((candidate[0], original[1]))})
        intent = {"schema_version": 1, "workflow_revision": workflow.revision, "delivery_revision": delivery.revision,
                  "candidate_digest": certificate["candidate_digest"], "config_digest": config_digest, "files": files}
        encoded = canonical_json(intent)
        with ledger._connection(write=True) as connection:
            protect_workspace_writers(connection, project)
            protect_delivery_roots(connection, (project,))
            if deliveries._read(connection, delivery_id) != delivery:
                raise ConflictError("delivery authorization changed")
            connection.execute("INSERT INTO improvement_publications VALUES (?,?,?,?,?)",
                               (workflow_id, str(project), delivery_id, encoded, digest_text(encoded)))
            store.event(connection, workflow_id, "PUBLISHING")
        # Intent commits before the first replace; any interruption retains the reservation.
        expected = parse_canonical_json(saved["base_manifest_json"])
        for item in files:
            store.require_active(workflow_id)
            if source_manifest(project) != canonical_json(expected):
                raise ConflictError("live inputs changed during publication; reconcile the retained intent")
            if deliveries.certificate(delivery_id, config_digest) != certificate:
                raise ConflictError("delivery acceptance changed during publication")
            verify_test_inputs(certificate["test_inputs_json"])
            _replace(project / item["path"], _unpack(item["original"]), _unpack(item["candidate"]))
            expected[item["path"]] = {"kind": "file", **{key: item["candidate"][key] for key in ("sha256", "size_bytes", "mode")}}
        if source_manifest(project) != canonical_json(expected):
            raise ConflictError("source changed before publication receipt; reconciliation required")
        store.require_active(workflow_id)
        store.transition(workflow_id, "PUBLISHED")
        return store.publication(workflow_id)


def rollback(project, checkpoint, ledger, workflow_id):
    store = ImprovementStore(ledger)
    with project_lock(project):
        saved, workflow = _identity(project, checkpoint, ledger, workflow_id)
        record = store.publication(workflow_id)
        if record is None:
            if source_manifest(project) != saved["base_manifest_json"]:
                raise ConflictError("no publication owns the current edits; refusing rollback")
            return {"status": "NOT_PUBLISHED", "rolled_back": []}
        if record["project_root"] != str(project):
            raise ConflictError("publication belongs to another project")
        if record["status"] == "ROLLED_BACK":
            return {"status": "ROLLED_BACK", "rolled_back": []}
        if record["status"] != "ROLLING_BACK":
            store.transition(workflow_id, "ROLLING_BACK")
        files = record["intent"]["files"]
        if tuple(item["path"] for item in files) != workflow.spec.owned_paths:
            raise StorageError("publication ownership differs from its workflow")
        try:
            # Preflight all files before restoring any of them.
            for item in files:
                if file_state(project / item["path"]) not in (_unpack(item["original"]), _unpack(item["candidate"])):
                    raise ConflictError("rollback would erase intervening edits; preserved source and journal")
            for item in reversed(files):
                original, candidate = _unpack(item["original"]), _unpack(item["candidate"])
                if file_state(project / item["path"]) == candidate:
                    _replace(project / item["path"], candidate, original)
            if any(file_state(project / item["path"]) != _unpack(item["original"]) for item in files):
                raise ConflictError("source changed before rollback receipt")
        except (ConflictError, OSError, ValidationError):
            store.transition(workflow_id, "BLOCKED", "Rollback conflict; preserve current edits and reconcile explicitly")
            raise
        store.transition(workflow_id, "ROLLED_BACK")
        return {"status": "ROLLED_BACK", "rolled_back": [item["path"] for item in files]}
