import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from stat import S_ISREG
from typing import Iterator
from uuid import uuid4

from .dispatch import DispatchClaim, acquire_start, reserve
from .domain import FOUNDATION_STATES, LedgerEvent, TaskSnapshot, TaskSpec, TaskStatus
from .evidence import AttemptStatus, EvidenceRecord, ExecutionAttempt, digest_text, redact_text, validate_attempt_update
from .errors import (CommandConflictError, ConflictError, ExecutionGuardError,
                     NotFoundError, StorageError, ValidationError)
from .paths import resolve_storage_path, storage_stat
from .packets import TaskPacket
from .processes import ProcessIdentity, observe
from .replay import next_snapshot, replay_events
from .schema import SCHEMA_VERSION, backup_ledger, check_schema, migrate_schema
from .validation import (bounded_int, canonical_json, command_id as validate_command_id,
                         parse_canonical_json, task_id as validate_task_id, text, utc_now)


class EventStore:
    def __init__(self, database: Path, busy_timeout_ms: int = 5000) -> None:
        if not isinstance(database, Path) or not database.is_absolute():
            raise ValidationError("database must be an absolute pathlib.Path")
        bounded_int(busy_timeout_ms, "busy_timeout_ms", 1, 30_000)
        text(str(database), "database path", 4096)
        storage_stat(database.parent)
        self.database = resolve_storage_path(database)
        self.busy_timeout_ms = busy_timeout_ms

    def _database_present(self) -> bool:
        info = storage_stat(self.database)
        if info is None:
            return False
        if not S_ISREG(info.st_mode):
            raise StorageError(f"database is not a regular file: {self.database}")
        return True

    @contextmanager
    def _connection(self, write: bool, migrate: bool = False) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            storage_stat(self.database.parent)
            if resolve_storage_path(self.database) != self.database:
                raise ValidationError("storage path changed through a symlink; resolve the project/state path again")
            if write:
                self.database.parent.mkdir(parents=True, exist_ok=True)
                if resolve_storage_path(self.database) != self.database:
                    raise ValidationError("storage path changed through a symlink during initialization")
                connection = sqlite3.connect(self.database, isolation_level=None, timeout=self.busy_timeout_ms / 1000)
            else:
                connection = sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True,
                                             isolation_level=None, timeout=self.busy_timeout_ms / 1000)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            if write:
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN IMMEDIATE")
            else:
                connection.execute("PRAGMA query_only=ON")
                connection.execute("BEGIN")
            if migrate:
                migrate_schema(connection)
            check_schema(connection, initialize=write and not migrate)
            yield connection
            if write:
                connection.commit()
        except (sqlite3.Error, OSError) as exc:
            raise StorageError(f"ledger operation failed: {exc}; check the state path, permissions, and competing writers") from exc
        finally:
            if connection is not None:
                try:
                    if connection.in_transaction:
                        connection.rollback()
                finally:
                    connection.close()

    @staticmethod
    def _decode_snapshot(payload: str) -> TaskSnapshot:
        try:
            return TaskSnapshot.from_dict(parse_canonical_json(payload))
        except ValidationError as exc:
            raise StorageError(f"invalid persisted task snapshot: {exc}") from exc

    def _read_task(self, connection: sqlite3.Connection, task_id: str) -> TaskSnapshot:
        row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"task {task_id} does not exist")
        snapshot = self._decode_snapshot(row["snapshot_json"])
        if snapshot.spec.task_id != row["task_id"] or snapshot.revision != row["revision"] or snapshot.status.value != row["status"]:
            raise StorageError("task projection columns disagree with its immutable snapshot")
        return snapshot

    def _deduplicated(self, connection: sqlite3.Connection, command_id: str, request: str) -> TaskSnapshot | None:
        row = connection.execute("SELECT request_json, snapshot_json FROM commands WHERE command_id=?", (command_id,)).fetchone()
        if row is None:
            return None
        if row["request_json"] != request:
            raise CommandConflictError(f"command {command_id} already committed different input; use a new command ID")
        return self._decode_snapshot(row["snapshot_json"])

    @staticmethod
    def _record(connection: sqlite3.Connection, snapshot: TaskSnapshot, event_type: str,
                command_id: str, request: str, payload: str) -> None:
        event = LedgerEvent(uuid4().hex, snapshot.spec.task_id, snapshot.revision,
                            event_type, snapshot.updated_at, command_id, payload)
        connection.execute("""INSERT INTO events
            (event_id,schema_version,task_id,revision,event_type,timestamp,actor,command_id,payload_json)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (event.event_id, event.schema_version, event.task_id, event.revision, event.event_type,
             event.timestamp, event.actor, event.command_id, event.payload_json))
        connection.execute("INSERT INTO commands VALUES (?,?,?)",
                           (command_id, request, canonical_json(snapshot.to_dict())))

    def create(self, spec: TaskSpec, command_id: str) -> TaskSnapshot:
        if not isinstance(spec, TaskSpec):
            raise ValidationError("create requires a validated TaskSpec")
        validate_command_id(command_id)
        request = canonical_json({"operation": "create", "spec": spec.to_dict()})
        payload = canonical_json({"schema_version": 1, "spec": spec.to_dict()})
        with self._connection(write=True) as connection:
            previous = self._deduplicated(connection, command_id, request)
            if previous is not None:
                return previous
            if connection.execute("SELECT 1 FROM tasks WHERE task_id=?", (spec.task_id,)).fetchone():
                raise ConflictError(f"task {spec.task_id} already exists; use its original command ID or a new task ID")
            timestamp = utc_now()
            snapshot = TaskSnapshot(spec, TaskStatus.CREATED, 1, timestamp, timestamp)
            connection.execute("INSERT INTO tasks VALUES (?,?,?,?)",
                               (spec.task_id, snapshot.revision, snapshot.status.value, canonical_json(snapshot.to_dict())))
            self._record(connection, snapshot, "TASK_CREATED", command_id, request, payload)
            return snapshot

    def transition(self, task_id: str, target: TaskStatus, expected_revision: int,
                   command_id: str, reason: str | None = None) -> TaskSnapshot:
        validate_task_id(task_id)
        validate_command_id(command_id)
        bounded_int(expected_revision, "expected_revision", 1, 2**63 - 1)
        if not isinstance(target, TaskStatus):
            raise ValidationError("target must be a TaskStatus enum")
        if target not in FOUNDATION_STATES:
            raise ExecutionGuardError("execution states are unavailable until packet, dispatch, and evidence gates exist")
        if reason is not None:
            text(reason, "transition reason")
        request = canonical_json({"operation": "transition", "task_id": task_id,
                                  "target": target.value, "expected_revision": expected_revision, "reason": reason})
        if not self._database_present():
            raise NotFoundError(f"task {task_id} does not exist; no ledger has been created")
        with self._connection(write=True) as connection:
            previous = self._deduplicated(connection, command_id, request)
            if previous is not None:
                return previous
            current = self._read_task(connection, task_id)
            if current.revision != expected_revision:
                raise ConflictError(f"expected revision {expected_revision}, found {current.revision}; reload the task")
            snapshot = next_snapshot(current, target, reason, utc_now())
            cursor = connection.execute("""UPDATE tasks SET revision=?,status=?,snapshot_json=?
                WHERE task_id=? AND revision=?""", (snapshot.revision, snapshot.status.value,
                canonical_json(snapshot.to_dict()), task_id, expected_revision))
            if cursor.rowcount != 1:
                raise ConflictError("task changed concurrently; reload its revision")
            payload = canonical_json({"schema_version": 1, "from_status": current.status.value,
                                      "to_status": target.value, "reason": reason,
                                      "resume_status": snapshot.resume_status.value if snapshot.resume_status else None})
            self._record(connection, snapshot, "TASK_TRANSITIONED", command_id, request, payload)
            return snapshot

    def get(self, task_id: str) -> TaskSnapshot:
        validate_task_id(task_id)
        if not self._database_present():
            raise NotFoundError(f"task {task_id} does not exist; no ledger has been created")
        with self._connection(write=False) as connection:
            return self._read_task(connection, task_id)

    def list(self) -> tuple[TaskSnapshot, ...]:
        if not self._database_present():
            return ()
        with self._connection(write=False) as connection:
            ids = connection.execute("SELECT task_id FROM tasks ORDER BY task_id").fetchall()
            return tuple(self._read_task(connection, row[0]) for row in ids)

    def events(self, task_id: str) -> tuple[LedgerEvent, ...]:
        validate_task_id(task_id)
        if not self._database_present():
            raise NotFoundError(f"task {task_id} does not exist; no ledger has been created")
        with self._connection(write=False) as connection:
            self._read_task(connection, task_id)
            rows = connection.execute("SELECT * FROM events WHERE task_id=? ORDER BY revision", (task_id,)).fetchall()
            try:
                return tuple(LedgerEvent(**dict(row)) for row in rows)
            except (ValidationError, TypeError) as exc:
                raise StorageError(f"invalid persisted event: {exc}") from exc

    def replay(self, task_id: str) -> TaskSnapshot:
        return replay_events(self.events(task_id))

    @staticmethod
    def _attempt_values(attempt: ExecutionAttempt) -> tuple[object, ...]:
        return (attempt.attempt_id, attempt.schema_version, attempt.task_id, attempt.task_revision,
                attempt.role.value, attempt.provider.value, attempt.executable, attempt.workspace_root,
                attempt.status.value, attempt.requested_model, attempt.cli_version,
                attempt.provider_session_id, attempt.reported_model, attempt.created_at,
                attempt.started_at, attempt.completed_at, attempt.returncode, attempt.signal,
                attempt.stdout_bytes, attempt.stderr_bytes, attempt.stdout_sha256,
                attempt.stderr_sha256, attempt.result_status, attempt.detail, attempt.revision)

    @staticmethod
    def _decode_attempt(row: sqlite3.Row) -> ExecutionAttempt:
        try:
            return ExecutionAttempt.from_dict(dict(row))
        except ValidationError as exc:
            raise StorageError(f"invalid persisted execution attempt: {exc}") from exc

    @staticmethod
    def _decode_evidence(row: sqlite3.Row) -> EvidenceRecord:
        try:
            return EvidenceRecord(row["evidence_id"], row["attempt_id"], row["kind"], row["created_at"],
                                  row["payload_json"], row["digest_sha256"], row["schema_version"])
        except ValidationError as exc:
            raise StorageError(f"invalid persisted execution evidence: {exc}") from exc

    @staticmethod
    def _decode_claim(row: sqlite3.Row) -> DispatchClaim:
        try:
            if digest_text(row["packet_json"]) != row["packet_digest"]:
                raise ValidationError("claim packet digest does not match persisted packet")
            return DispatchClaim(row["claim_id"], row["task_id"], row["task_revision"], row["attempt_id"],
                                 row["packet_digest"], row["owner"], row["created_at"], row["schema_version"])
        except ValidationError as exc:
            raise StorageError(f"invalid persisted dispatch claim: {exc}") from exc

    def migrate(self) -> int:
        """Explicitly migrate a supported existing ledger to the current schema."""
        if not self._database_present():
            raise NotFoundError("no database exists to migrate")
        with self._connection(write=True, migrate=True):
            return SCHEMA_VERSION

    def begin_attempt(self, attempt: ExecutionAttempt, command_id: str) -> ExecutionAttempt:
        if not isinstance(attempt, ExecutionAttempt):
            raise ValidationError("begin_attempt requires a validated ExecutionAttempt")
        validate_command_id(command_id)
        if attempt.status is not AttemptStatus.INTENDED:
            raise ValidationError("a new execution attempt must begin in INTENDED status")
        request = canonical_json({"operation": "attempt_begin", "attempt": attempt.to_dict()})
        with self._connection(write=True) as connection:
            previous = connection.execute("SELECT request_json, attempt_json FROM attempt_commands WHERE command_id=?",
                                          (command_id,)).fetchone()
            if previous is not None:
                if previous["request_json"] != request:
                    raise CommandConflictError(f"command {command_id} already committed different input; use a new command ID")
                return ExecutionAttempt.from_dict(parse_canonical_json(previous["attempt_json"]))
            current = self._read_task(connection, attempt.task_id)
            if current.revision != attempt.task_revision:
                raise ConflictError(f"attempt task revision {attempt.task_revision} is stale; found {current.revision}")
            if connection.execute("SELECT 1 FROM attempts WHERE attempt_id=?", (attempt.attempt_id,)).fetchone():
                raise ConflictError(f"attempt {attempt.attempt_id} already exists; use its original command ID")
            self._insert_attempt(connection, attempt, command_id, request)
            return attempt

    def reserve_dispatch(self, packet: TaskPacket, attempt: ExecutionAttempt, owner: str,
                         command_id: str, max_workers: int = 2) -> tuple[DispatchClaim, ExecutionAttempt]:
        return reserve(self, packet, attempt, owner, command_id, max_workers)

    def acquire_start(self, claim: DispatchClaim, command_id: str) -> ExecutionAttempt:
        return acquire_start(self, claim, command_id)

    def backup(self, destination: Path) -> Path:
        if not self._database_present():
            raise NotFoundError("no ledger exists to back up")
        with self._connection(write=False) as connection:
            return backup_ledger(connection, destination)

    def replay_attempt(self, attempt_id: str) -> ExecutionAttempt:
        validate_task_id(attempt_id)
        with self._connection(write=False) as connection:
            current = self._read_attempt(connection, attempt_id)
            history = {}
            for row in connection.execute("SELECT attempt_json FROM attempt_commands WHERE attempt_id=?", (attempt_id,)):
                item = ExecutionAttempt.from_dict(parse_canonical_json(row[0]))
                if item.attempt_id != attempt_id or item.revision in history and history[item.revision] != item:
                    raise StorageError("attempt command history has conflicting snapshots")
                history[item.revision] = item
            if sorted(history) != list(range(1, current.revision + 1)) or history[current.revision] != current:
                raise StorageError("attempt command replay differs from its projection")
            return history[current.revision]

    def _read_attempt(self, connection: sqlite3.Connection, attempt_id: str) -> ExecutionAttempt:
        row = connection.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"attempt {attempt_id} does not exist")
        return self._decode_attempt(row)

    def _insert_attempt(self, connection: sqlite3.Connection, attempt: ExecutionAttempt,
                        command_id: str, request: str) -> None:
        if redact_text(canonical_json(attempt.to_dict())) != canonical_json(attempt.to_dict()):
            raise ValidationError("attempt contains unredacted credential-like text")
        connection.execute("""INSERT INTO attempts
            (attempt_id,schema_version,task_id,task_revision,role,provider,executable,workspace_root,status,
             requested_model,cli_version,provider_session_id,reported_model,created_at,started_at,completed_at,
             returncode,signal,stdout_bytes,stderr_bytes,stdout_sha256,stderr_sha256,result_status,detail,revision)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", self._attempt_values(attempt))
        connection.execute("INSERT INTO attempt_commands VALUES (?,?,?,?)",
                           (command_id, attempt.attempt_id, request, canonical_json(attempt.to_dict())))

    def _write_attempt(self, connection: sqlite3.Connection, attempt: ExecutionAttempt,
                       expected_revision: int, command_id: str, request: str) -> None:
        current = self._read_attempt(connection, attempt.attempt_id)
        if current.revision != expected_revision:
            raise ConflictError(f"expected attempt revision {expected_revision}, found {current.revision}")
        if attempt.revision != current.revision + 1:
            raise ValidationError("attempt revision must advance by exactly one")
        if redact_text(canonical_json(attempt.to_dict())) != canonical_json(attempt.to_dict()):
            raise ValidationError("attempt contains unredacted credential-like text")
        validate_attempt_update(current, attempt)
        cursor = connection.execute("""UPDATE attempts SET schema_version=?,task_id=?,task_revision=?,role=?,provider=?,
            executable=?,workspace_root=?,status=?,requested_model=?,cli_version=?,provider_session_id=?,reported_model=?,
            created_at=?,started_at=?,completed_at=?,returncode=?,signal=?,stdout_bytes=?,stderr_bytes=?,stdout_sha256=?,
            stderr_sha256=?,result_status=?,detail=?,revision=? WHERE attempt_id=? AND revision=?""",
            (*self._attempt_values(attempt)[1:], attempt.attempt_id, expected_revision))
        if cursor.rowcount != 1:
            raise ConflictError("attempt changed concurrently; reload its revision")
        connection.execute("INSERT INTO attempt_commands VALUES (?,?,?,?)",
                           (command_id, attempt.attempt_id, request, canonical_json(attempt.to_dict())))

    def update_attempt(self, attempt: ExecutionAttempt, expected_revision: int,
                       command_id: str) -> ExecutionAttempt:
        if not isinstance(attempt, ExecutionAttempt):
            raise ValidationError("update_attempt requires a validated ExecutionAttempt")
        validate_command_id(command_id)
        bounded_int(expected_revision, "attempt expected_revision", 1, 2**63 - 1)
        if attempt.revision != expected_revision + 1:
            raise ValidationError("updated attempt revision must be expected_revision + 1")
        request = canonical_json({"operation": "attempt_update", "attempt_id": attempt.attempt_id,
                                  "expected_revision": expected_revision, "attempt": attempt.to_dict()})
        with self._connection(write=True) as connection:
            previous = connection.execute("SELECT request_json, attempt_json FROM attempt_commands WHERE command_id=?",
                                          (command_id,)).fetchone()
            if previous is not None:
                if previous["request_json"] != request:
                    raise CommandConflictError(f"command {command_id} already committed different input; use a new command ID")
                return ExecutionAttempt.from_dict(parse_canonical_json(previous["attempt_json"]))
            if attempt.status is AttemptStatus.STARTED and connection.execute(
                    "SELECT 1 FROM dispatch_claims WHERE attempt_id=?", (attempt.attempt_id,)).fetchone():
                raise ExecutionGuardError("claimed attempts must start through acquire_start")
            self._write_attempt(connection, attempt, expected_revision, command_id, request)
            return attempt

    def get_attempt(self, attempt_id: str) -> ExecutionAttempt:
        validate_task_id(attempt_id)
        if not self._database_present():
            raise NotFoundError(f"attempt {attempt_id} does not exist; no ledger has been created")
        with self._connection(write=False) as connection:
            row = connection.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"attempt {attempt_id} does not exist")
            return self._decode_attempt(row)

    def reconcile_attempt(self, attempt_id: str, command_id: str, reason: str) -> ExecutionAttempt:
        """Quarantine an unproven launch after restart instead of relaunching it."""
        validate_task_id(attempt_id)
        validate_command_id(command_id)
        validate_command_id(f"{command_id}:observation")
        text(reason, "reconciliation reason", 4096)
        reason = redact_text(reason)
        request = canonical_json({"operation": "attempt_reconcile", "attempt_id": attempt_id, "reason": reason})
        with self._connection(write=True) as connection:
            previous = connection.execute("SELECT request_json,attempt_json FROM attempt_commands WHERE command_id=?",
                                          (command_id,)).fetchone()
            if previous is not None:
                if previous["request_json"] != request:
                    raise CommandConflictError(f"command {command_id} already committed different input")
                return ExecutionAttempt.from_dict(parse_canonical_json(previous["attempt_json"]))
            current = self._read_attempt(connection, attempt_id)
            rows = connection.execute("SELECT * FROM attempt_evidence WHERE attempt_id=? AND kind='process_started'",
                                      (attempt_id,)).fetchall()
            identity = None
            if rows:
                payload = self._decode_evidence(rows[-1]).to_dict()["payload"]
                if payload.get("identity") is not None:
                    identity = ProcessIdentity(**payload["identity"])
            timestamp = utc_now()
            payload = canonical_json({"reason": reason, **observe(identity)})
            evidence = EvidenceRecord(uuid4().hex, attempt_id, "recovery_observation", timestamp, payload, digest_text(payload))
            if current.status in {AttemptStatus.INTENDED, AttemptStatus.STARTED}:
                target = replace(current, status=AttemptStatus.AMBIGUOUS, completed_at=timestamp,
                                 detail=reason, revision=current.revision + 1)
                self._write_attempt(connection, target, current.revision, command_id, request)
            else:
                target = current
                connection.execute("INSERT INTO attempt_commands VALUES (?,?,?,?)",
                                   (command_id, attempt_id, request, canonical_json(target.to_dict())))
            self._insert_evidence(connection, evidence, f"{command_id}:observation",
                                  canonical_json({"operation": "recovery_observation", "digest": evidence.digest_sha256}))
            return target

    def complete_attempt(self, attempt: ExecutionAttempt, expected_revision: int,
                         evidence: EvidenceRecord, command_id: str) -> ExecutionAttempt:
        if not isinstance(attempt, ExecutionAttempt) or not isinstance(evidence, EvidenceRecord):
            raise ValidationError("completion requires validated attempt and evidence")
        validate_command_id(command_id)
        validate_command_id(f"{command_id}:evidence")
        bounded_int(expected_revision, "completion expected_revision", 1, 2**63 - 1)
        if evidence.attempt_id != attempt.attempt_id or evidence.kind != "provider_result":
            raise ValidationError("completion evidence must belong to this attempt")
        if attempt.status is AttemptStatus.CANCELLED and evidence.to_dict()["payload"].get("cleanup_complete") is not True:
            raise ValidationError("cancellation requires proven cleanup in its execution evidence")
        request = canonical_json({"operation": "attempt_complete", "attempt": attempt.to_dict(),
                                  "evidence": evidence.to_dict(), "expected_revision": expected_revision})
        with self._connection(write=True) as connection:
            previous = connection.execute("SELECT request_json,attempt_json FROM attempt_commands WHERE command_id=?",
                                          (command_id,)).fetchone()
            if previous is not None:
                if previous["request_json"] != request:
                    raise CommandConflictError(f"command {command_id} already committed different input")
                return ExecutionAttempt.from_dict(parse_canonical_json(previous["attempt_json"]))
            current = self._read_attempt(connection, attempt.attempt_id)
            if current.revision != expected_revision:
                if current.status is not AttemptStatus.AMBIGUOUS or current.started_at != attempt.started_at:
                    raise ConflictError("completion is stale; retain the returned evidence as a late result")
                attempt = replace(attempt, revision=current.revision + 1)
            task = self._read_task(connection, attempt.task_id)
            if task.revision != attempt.task_revision or task.status is not TaskStatus.CREATED:
                if attempt.status is not AttemptStatus.AMBIGUOUS:
                    attempt = replace(attempt, status=AttemptStatus.FAILED,
                                      detail="late result retained; task authorization changed during execution")
            self._write_attempt(connection, attempt, current.revision, command_id, request)
            self._insert_evidence(connection, evidence, f"{command_id}:evidence",
                                  canonical_json({"operation": "evidence_record", "evidence": evidence.to_dict()}))
            return attempt

    @staticmethod
    def _insert_evidence(connection: sqlite3.Connection, evidence: EvidenceRecord, command_id: str, request: str) -> None:
        validate_command_id(command_id)
        connection.execute("INSERT INTO attempt_evidence VALUES (?,?,?,?,?,?,?,?,?)",
                           (evidence.evidence_id, evidence.schema_version, evidence.attempt_id, evidence.kind,
                            evidence.created_at, evidence.payload_json, evidence.digest_sha256, command_id, request))

    def record_evidence(self, evidence: EvidenceRecord, command_id: str) -> EvidenceRecord:
        if not isinstance(evidence, EvidenceRecord):
            raise ValidationError("record_evidence requires a validated EvidenceRecord")
        validate_command_id(command_id)
        request = canonical_json({"operation": "evidence_record", "evidence": evidence.to_dict()})
        with self._connection(write=True) as connection:
            previous = connection.execute("SELECT request_json, evidence_id FROM attempt_evidence WHERE command_id=?",
                                          (command_id,)).fetchone()
            if previous is not None:
                if previous["request_json"] != request:
                    raise CommandConflictError(f"command {command_id} already committed different input; use a new command ID")
                row = connection.execute("SELECT * FROM attempt_evidence WHERE evidence_id=?",
                                         (previous["evidence_id"],)).fetchone()
                if row is None:
                    raise StorageError("evidence command points to missing evidence")
                return self._decode_evidence(row)
            if connection.execute("SELECT 1 FROM attempts WHERE attempt_id=?", (evidence.attempt_id,)).fetchone() is None:
                raise NotFoundError(f"attempt {evidence.attempt_id} does not exist")
            self._insert_evidence(connection, evidence, command_id, request)
            return evidence

    def attempts(self, task_id: str) -> tuple[ExecutionAttempt, ...]:
        validate_task_id(task_id)
        if not self._database_present():
            return ()
        with self._connection(write=False) as connection:
            self._read_task(connection, task_id)
            rows = connection.execute("SELECT * FROM attempts WHERE task_id=? ORDER BY created_at, attempt_id",
                                      (task_id,)).fetchall()
            return tuple(self._decode_attempt(row) for row in rows)

    def evidence(self, task_id: str) -> tuple[EvidenceRecord, ...]:
        validate_task_id(task_id)
        if not self._database_present():
            return ()
        with self._connection(write=False) as connection:
            self._read_task(connection, task_id)
            rows = connection.execute("""SELECT attempt_evidence.* FROM attempt_evidence
                JOIN attempts ON attempts.attempt_id=attempt_evidence.attempt_id
                WHERE attempts.task_id=? ORDER BY attempt_evidence.created_at, attempt_evidence.evidence_id""",
                                      (task_id,)).fetchall()
            return tuple(self._decode_evidence(row) for row in rows)

    def dispatch_claims(self, task_id: str) -> tuple[DispatchClaim, ...]:
        validate_task_id(task_id)
        if not self._database_present():
            return ()
        with self._connection(write=False) as connection:
            self._read_task(connection, task_id)
            rows = connection.execute("SELECT * FROM dispatch_claims WHERE task_id=? ORDER BY task_revision",
                                      (task_id,)).fetchall()
            return tuple(self._decode_claim(row) for row in rows)
