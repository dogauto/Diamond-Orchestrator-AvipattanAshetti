from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .domain import TaskStatus
from .evidence import AttemptStatus, ExecutionAttempt, digest_text, redact_text
from .errors import CommandConflictError, ConflictError, ExecutionGuardError, StorageError, ValidationError
from .packets import TaskPacket, _digest
from .validation import bounded_int, canonical_json, command_id as validate_command_id, exact_keys, task_id, text, utc_now, utc_timestamp
from .validation import parse_canonical_json

if TYPE_CHECKING:
    from .store import EventStore


@dataclass(frozen=True)
class DispatchClaim:
    claim_id: str
    task_id: str
    task_revision: int
    attempt_id: str
    packet_digest: str
    owner: str
    created_at: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        task_id(self.claim_id)
        task_id(self.task_id)
        task_id(self.attempt_id)
        bounded_int(self.task_revision, "claim task_revision", 1, 2**63 - 1)
        bounded_int(self.schema_version, "claim schema_version", 1, 1)
        _digest(self.packet_digest, "claim packet digest")
        text(self.owner, "claim owner", 256)
        utc_timestamp(self.created_at)

    def to_dict(self) -> dict[str, Any]:
        return {"claim_id": self.claim_id, "task_id": self.task_id,
                "task_revision": self.task_revision, "attempt_id": self.attempt_id,
                "packet_digest": self.packet_digest, "owner": self.owner,
                "created_at": self.created_at, "schema_version": self.schema_version}

    @classmethod
    def from_dict(cls, value: object) -> "DispatchClaim":
        data = dict(exact_keys(value, {"claim_id", "task_id", "task_revision", "attempt_id",
                                       "packet_digest", "owner", "created_at", "schema_version"},
                               "DispatchClaim"))
        try:
            return cls(**data)
        except TypeError as exc:
            raise ValidationError("invalid DispatchClaim fields") from exc


def reserve(store: "EventStore", packet: TaskPacket, attempt: ExecutionAttempt, owner: str,
            command_id: str, max_workers: int) -> tuple[DispatchClaim, ExecutionAttempt]:
    if not isinstance(packet, TaskPacket) or not isinstance(attempt, ExecutionAttempt):
        raise ValidationError("reserve_dispatch requires a validated packet and attempt")
    validate_command_id(command_id)
    text(owner, "dispatch owner", 256)
    bounded_int(max_workers, "dispatch max_workers", 1, 64)
    packet.verify_workspace()
    if attempt.status is not AttemptStatus.INTENDED or attempt.revision != 1:
        raise ValidationError("dispatch reservation requires a new INTENDED attempt")
    if (packet.task_id, packet.task_revision, packet.attempt_id, packet.role, packet.provider,
            packet.requested_model, packet.workspace_root) != \
            (attempt.task_id, attempt.task_revision, attempt.attempt_id, attempt.role, attempt.provider,
             attempt.requested_model, attempt.workspace_root):
        raise ValidationError("dispatch packet and attempt launch identity do not match")
    packet_json = packet.canonical_json()
    if redact_text(packet_json) != packet_json:
        raise ValidationError("dispatch packet contains credential-like text; remove it before dispatch")
    request = canonical_json({"operation": "dispatch_claim", "packet_digest": packet.packet_digest(),
                              "attempt": attempt.to_dict(), "owner": owner, "max_workers": max_workers})
    intent_command = f"{command_id}:intent"
    validate_command_id(intent_command)
    with store._connection(write=True) as connection:
        previous = connection.execute("SELECT * FROM dispatch_claims WHERE command_id=?", (command_id,)).fetchone()
        if previous is not None:
            if previous["request_json"] != request:
                raise CommandConflictError(f"command {command_id} already committed different input")
            intent = connection.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt.attempt_id,)).fetchone()
            if intent is None:
                raise StorageError("dispatch command points to missing attempt")
            return store._decode_claim(previous), store._decode_attempt(intent)
        current = store._read_task(connection, packet.task_id)
        if current.revision != packet.task_revision:
            raise ConflictError("dispatch packet task revision is stale; reload the task")
        if current.status is not TaskStatus.CREATED:
            raise ExecutionGuardError("only CREATED tasks can reserve this bounded dispatch")
        if current.spec.dependencies:
            raise ExecutionGuardError("dependent-task acceptance must be integrated before this task dispatches")
        if (current.spec.role, current.spec.provider, current.spec.objective) != (packet.role, packet.provider, packet.objective):
            raise ValidationError("dispatch packet differs from its persisted task specification")
        for field in ("owned_paths", "allowed_tools", "required_tests", "acceptance_conditions"):
            if getattr(current.spec, field) is not None and getattr(current.spec, field) != getattr(packet, field):
                raise ValidationError(f"dispatch packet changed task {field}")
        if connection.execute("SELECT 1 FROM dispatch_claims WHERE task_id=? AND task_revision=?",
                              (packet.task_id, packet.task_revision)).fetchone():
            raise ConflictError("task revision already has a dispatch claim; refusing a second launch")
        active = "('INTENDED','STARTED','AMBIGUOUS')"
        from .occupancy import protect_delivery_roots, protect_owned_paths, worker_occupancy
        protect_delivery_roots(connection, tuple(Path(packet.workspace_root) / value for value in packet.owned_paths))
        if connection.execute(f"SELECT 1 FROM attempts WHERE task_id=? AND status IN {active}", (packet.task_id,)).fetchone():
            raise ConflictError("task has an unresolved attempt; reconcile before reserving another revision")
        workers, covered = worker_occupancy(connection)
        if workers + int(packet.attempt_id not in covered) > max_workers:
            raise ConflictError("controller worker capacity is occupied; keep the task queued")
        protect_owned_paths(connection, tuple(Path(packet.workspace_root) / value for value in packet.owned_paths),
                            packet.task_id, packet.attempt_id)
        if connection.execute("SELECT 1 FROM attempts WHERE attempt_id=?", (attempt.attempt_id,)).fetchone():
            raise ConflictError("attempt already exists; use its original dispatch command")
        store._insert_attempt(connection, attempt, intent_command,
                              canonical_json({"operation": "attempt_begin", "attempt": attempt.to_dict()}))
        claim = DispatchClaim(attempt.attempt_id, packet.task_id, packet.task_revision, packet.attempt_id,
                              packet.packet_digest(), owner, utc_now())
        connection.execute("""INSERT INTO dispatch_claims
            (claim_id,schema_version,task_id,task_revision,attempt_id,packet_digest,packet_json,owner,created_at,command_id,request_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                           (claim.claim_id, claim.schema_version, claim.task_id, claim.task_revision,
                            claim.attempt_id, claim.packet_digest, packet_json, claim.owner, claim.created_at, command_id, request))
        return claim, attempt


def acquire_start(store: "EventStore", claim: DispatchClaim, command_id: str) -> ExecutionAttempt:
    """A replay of this command never grants permission to launch a subprocess."""
    if not isinstance(claim, DispatchClaim):
        raise ValidationError("acquire_start requires a validated dispatch claim")
    validate_command_id(command_id)
    with store._connection(write=True) as connection:
        row = connection.execute("SELECT * FROM dispatch_claims WHERE claim_id=?", (claim.claim_id,)).fetchone()
        if row is None or store._decode_claim(row) != claim:
            raise ConflictError("dispatch claim is missing or changed")
        if connection.execute("SELECT 1 FROM attempt_commands WHERE command_id=?", (command_id,)).fetchone():
            raise ConflictError("launch command already consumed; refusing blind relaunch")
        current = store._read_attempt(connection, claim.attempt_id)
        from .occupancy import protect_delivery_roots, protect_owned_paths
        packet = parse_canonical_json(row["packet_json"])
        protect_delivery_roots(connection, tuple(Path(packet["workspace_root"]) / value for value in packet["owned_paths"]))
        protect_owned_paths(connection, tuple(Path(packet["workspace_root"]) / value for value in packet["owned_paths"]),
                            claim.task_id, claim.attempt_id)
        task = store._read_task(connection, claim.task_id)
        if task.revision != claim.task_revision or task.status is not TaskStatus.CREATED:
            raise ConflictError("task authorization changed after reservation; launch refused")
        if current.status is not AttemptStatus.INTENDED or current.revision != 1:
            raise ConflictError("attempt already advanced; refusing blind relaunch")
        started = replace(current, status=AttemptStatus.STARTED, started_at=utc_now(), revision=2)
        store._write_attempt(connection, started, current.revision, command_id,
                             canonical_json({"operation": "launch_acquired", "claim_id": claim.claim_id}))
        return started
