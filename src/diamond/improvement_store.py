import hashlib
from pathlib import Path
from stat import S_IMODE, S_ISREG

from .errors import ConflictError, ExecutionGuardError, StorageError, ValidationError
from .evidence import digest_text
from .store import EventStore
from .validation import canonical_json, parse_canonical_json, utc_now


SOURCE_ROOTS = ("src", "tests", "config", "scripts", "pyproject.toml", "setup.cfg", "setup.py")


def source_manifest(root: Path) -> str:
    """Inventory source, policy and verification inputs, including file modes."""
    if root.resolve(strict=True) != root:
        raise ValidationError("self-improvement root must be canonical")
    entries, total = {}, 0
    for name in SOURCE_ROOTS:
        base = root / name
        for path in (base, *sorted(base.rglob("*"))):
            relative = path.relative_to(root)
            if "__pycache__" in relative.parts or path.suffix == ".pyc":
                continue
            if path.is_symlink():
                raise ValidationError(f"self-improvement input cannot be a symlink: {relative}")
            if not path.exists():
                continue
            info = path.stat()
            if path.is_dir():
                entry = {"kind": "directory"}
            elif S_ISREG(info.st_mode):
                total += info.st_size
                if total > 32 * 1024 * 1024:
                    raise ValidationError("self-improvement source exceeds 32 MiB")
                content = path.read_bytes()
                entry = {"kind": "file", "sha256": hashlib.sha256(content).hexdigest(),
                         "size_bytes": len(content), "mode": S_IMODE(info.st_mode)}
            else:
                raise ValidationError("self-improvement requires regular inputs")
            entries[str(relative)] = entry
            if len(entries) > 2048:
                raise ValidationError("self-improvement source exceeds 2048 entries")
    return canonical_json(entries)


class ImprovementStore:
    def __init__(self, ledger: EventStore):
        self.ledger = ledger

    def checkpoint(self, workflow_id: str, project: Path) -> dict:
        with self.ledger._connection(write=False) as connection:
            row = connection.execute("SELECT * FROM improvement_checkpoints WHERE workflow_id=?", (workflow_id,)).fetchone()
        if row is None:
            raise ExecutionGuardError("checkpoint has no retained source identity; create a new improvement")
        if row["project_root"] != str(project):
            raise ConflictError("self-improvement belongs to another project")
        return dict(row)

    def register(self, workflow_id: str, project: Path, manifest: str, config_digest: str) -> None:
        with self.ledger._connection(write=True) as connection:
            connection.execute("INSERT INTO improvement_checkpoints VALUES (?,?,?,?)",
                               (workflow_id, str(project), manifest, config_digest))

    def publication(self, workflow_id: str) -> dict | None:
        with self.ledger._connection(write=False) as connection:
            row = connection.execute("SELECT * FROM improvement_publications WHERE workflow_id=?", (workflow_id,)).fetchone()
            if row is None:
                return None
            events = [dict(item) for item in connection.execute(
                "SELECT * FROM improvement_publication_events WHERE workflow_id=? ORDER BY revision", (workflow_id,))]
        if digest_text(row["intent_json"]) != row["digest_sha256"]:
            raise StorageError("publication journal digest mismatch")
        allowed = {None: {"PUBLISHING"}, "PUBLISHING": {"PUBLISHED", "ROLLING_BACK", "BLOCKED"},
                   "PUBLISHED": {"ROLLING_BACK"}, "ROLLING_BACK": {"ROLLED_BACK", "BLOCKED"},
                   "BLOCKED": {"ROLLING_BACK"}, "ROLLED_BACK": set()}
        state = None
        for revision, event in enumerate(events, 1):
            if event["revision"] != revision or event["status"] not in allowed[state]:
                raise StorageError("invalid publication event history")
            state = event["status"]
        if state is None:
            raise StorageError("publication intent has no durable status")
        return {"workflow_id": workflow_id, "delivery_id": row["delivery_id"], "project_root": row["project_root"],
                "intent": parse_canonical_json(row["intent_json"]), "status": state, "events": events}

    @staticmethod
    def event(connection, workflow_id: str, status: str, detail: str = "") -> None:
        revision = connection.execute("SELECT coalesce(max(revision),0)+1 FROM improvement_publication_events WHERE workflow_id=?",
                                      (workflow_id,)).fetchone()[0]
        connection.execute("INSERT INTO improvement_publication_events VALUES (?,?,?,?,?)",
                           (workflow_id, revision, status, utc_now(), detail))

    def transition(self, workflow_id: str, status: str, detail: str = "") -> None:
        with self.ledger._connection(write=True) as connection:
            self.event(connection, workflow_id, status, detail)
