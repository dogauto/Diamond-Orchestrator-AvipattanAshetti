from dataclasses import replace
from pathlib import Path
import sqlite3
from uuid import NAMESPACE_URL, uuid5

from .errors import CommandConflictError, ConflictError, ExecutionGuardError, NotFoundError, StorageError, ValidationError
from .evidence import digest_text, redact_text
from .store import EventStore
from .packets import ArtifactRef, _digest
from .validation import bounded_int, canonical_json, command_id as validate_command_id, parse_canonical_json, task_id, utc_now
from .workflow_contracts import FailurePacket, WorkflowSnapshot, WorkflowSpec, WorkflowStatus


def reduce_event(current: WorkflowSnapshot | None, event_type: str, payload: object, timestamp: str) -> WorkflowSnapshot:
    if not isinstance(payload, dict):
        raise ValidationError("workflow event payload must be an object")
    if event_type == "CREATED" and current is None:
        spec = WorkflowSpec.from_dict(payload["spec"])
        return WorkflowSnapshot(spec, WorkflowStatus.CREATED, 1, 0, spec.initial_manifest_json, None, None, None, timestamp)
    if current is None:
        raise ValidationError("workflow must begin with creation")
    base = {"revision": current.revision + 1, "updated_at": timestamp}
    if event_type == "STARTED":
        if current.status not in {WorkflowStatus.CREATED, WorkflowStatus.NEEDS_CORRECTION}:
            raise ExecutionGuardError("workflow is not ready for an explicit step")
        correction = current.status is WorkflowStatus.NEEDS_CORRECTION
        if correction and current.corrections_used >= current.spec.correction_round_limit:
            raise ExecutionGuardError("workflow correction budget is exhausted")
        return replace(current, **base, status=WorkflowStatus.DEBUGGING if correction else WorkflowStatus.BUILDING,
                       corrections_used=current.corrections_used + int(correction), active_task_id=payload["task_id"],
                       active_attempt_id=payload["attempt_id"], detail="")
    if event_type == "FINISHED":
        if current.status not in {WorkflowStatus.BUILDING, WorkflowStatus.DEBUGGING}:
            raise ExecutionGuardError("workflow has no running step to finish")
        if type(payload["passed"]) is not bool:
            raise ValidationError("workflow passed field must be boolean")
        failure = FailurePacket.from_dict(payload["failure"]) if payload["failure"] is not None else None
        if payload["passed"]:
            if failure is not None:
                raise ValidationError("verified workflow must not carry a failure packet")
            status = WorkflowStatus.VERIFIED
        else:
            if failure is None or failure.attempt_id != current.active_attempt_id or failure.round_number != current.corrections_used:
                raise ValidationError("workflow failure packet must identify its actual failed attempt and round")
            status = WorkflowStatus.NEEDS_CORRECTION if current.corrections_used < current.spec.correction_round_limit else WorkflowStatus.EXHAUSTED
        return replace(current, **base, status=status, failure=failure, manifest_json=payload["manifest_json"], detail=payload["detail"])
    if event_type == "FILES_APPLIED" and current.status in {WorkflowStatus.BUILDING, WorkflowStatus.DEBUGGING}:
        return replace(current, **base, manifest_json=payload["manifest_json"])
    if event_type == "BLOCKED" and current.status in {WorkflowStatus.CREATED, WorkflowStatus.NEEDS_CORRECTION,
                                                     WorkflowStatus.BUILDING, WorkflowStatus.DEBUGGING}:
        if not isinstance(payload.get("reason"), str) or not payload["reason"].strip():
            raise ValidationError("blocked workflow requires a reason")
        return replace(current, **base, status=WorkflowStatus.BLOCKED, detail=payload["reason"])
    if event_type == "RECONCILED":
        if current.status is not WorkflowStatus.BLOCKED:
            raise ExecutionGuardError("only a blocked workflow can be reconciled")
        if payload.get("attempt_id") != current.active_attempt_id:
            raise ValidationError("reconciliation must identify the active attempt")
        if payload.get("attempt_status") not in {"FAILED", "CANCELLED", "SUCCEEDED"}:
            raise ValidationError("reconciliation requires a terminal attempt status")
        _digest(payload.get("evidence_digest"), "reconciliation evidence digest")
        reason = payload.get("reason")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 4096:
            raise ValidationError("reconciliation requires a bounded reason")
        recovered_status = (WorkflowStatus.EXHAUSTED if current.failure is not None and
                            current.corrections_used >= current.spec.correction_round_limit else
                            WorkflowStatus.NEEDS_CORRECTION if current.failure is not None else WorkflowStatus.CREATED)
        return replace(current, **base, status=recovered_status, active_task_id=None, active_attempt_id=None, detail=reason)
    raise ValidationError("invalid workflow event transition")


class WorkflowStore:
    def __init__(self, ledger: EventStore) -> None:
        if not isinstance(ledger, EventStore):
            raise ValidationError("workflow storage requires an EventStore")
        self.ledger = ledger

    @staticmethod
    def _decode(payload: str) -> WorkflowSnapshot:
        try:
            return WorkflowSnapshot.from_dict(parse_canonical_json(payload))
        except (ValidationError, TypeError, ValueError, KeyError) as exc:
            raise StorageError("invalid persisted workflow snapshot") from exc

    def _read(self, connection: sqlite3.Connection, workflow_id: str) -> WorkflowSnapshot:
        row = connection.execute("SELECT * FROM workflows WHERE workflow_id=?", (workflow_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"workflow {workflow_id} does not exist")
        snapshot = self._decode(row["snapshot_json"])
        if (snapshot.spec.workflow_id, snapshot.revision, snapshot.status.value) != (row["workflow_id"], row["revision"], row["status"]):
            raise StorageError("workflow projection differs from its snapshot")
        return snapshot

    def _commit(self, connection: sqlite3.Connection, current: WorkflowSnapshot | None, workflow_id: str, event_type: str,
                payload: dict[str, object], command_id: str, request: str) -> WorkflowSnapshot:
        value = canonical_json(payload)
        if redact_text(value) != value:
            raise ValidationError("workflow event contains credential-like text")
        timestamp = utc_now()
        snapshot = reduce_event(current, event_type, payload, timestamp)
        snapshot_json = canonical_json(snapshot.to_dict())
        if current is None:
            connection.execute("INSERT INTO workflows VALUES (?,?,?,?)", (workflow_id, snapshot.revision, snapshot.status.value, snapshot_json))
        else:
            cursor = connection.execute("UPDATE workflows SET revision=?,status=?,snapshot_json=? WHERE workflow_id=? AND revision=?",
                (snapshot.revision, snapshot.status.value, snapshot_json, workflow_id, current.revision))
            if cursor.rowcount != 1:
                raise ConflictError("workflow changed concurrently")
        connection.execute("INSERT INTO workflow_events VALUES (?,?,?,?,?,?)",
                           (workflow_id, snapshot.revision, event_type, timestamp, command_id, value))
        connection.execute("INSERT INTO workflow_commands VALUES (?,?,?)", (command_id, request, snapshot_json))
        return snapshot

    def mutate(self, workflow_id: str, event_type: str, payload: dict[str, object], expected_revision: int,
               command_id: str, *, one_shot: bool = False) -> WorkflowSnapshot:
        task_id(workflow_id)
        validate_command_id(command_id)
        bounded_int(expected_revision, "workflow expected revision", 0, 2**63 - 1)
        request = canonical_json({"workflow_id": workflow_id, "event_type": event_type, "payload": payload,
                                  "expected_revision": expected_revision})
        with self.ledger._connection(write=True) as connection:
            previous = connection.execute("SELECT * FROM workflow_commands WHERE command_id=?", (command_id,)).fetchone()
            if previous is not None:
                if previous["request_json"] != request:
                    raise CommandConflictError("workflow command ID was already used for different input")
                if one_shot:
                    raise ConflictError("workflow execution step already consumed; reconcile before continuing")
                return self._decode(previous["snapshot_json"])
            current = self._read(connection, workflow_id) if event_type != "CREATED" else None
            if current is None and expected_revision != 0 or current is not None and current.revision != expected_revision:
                raise ConflictError("workflow revision is stale")
            if event_type == "STARTED":
                from .occupancy import protect_delivery_roots, protect_owned_paths, worker_occupancy
                protect_delivery_roots(connection, tuple(Path(current.spec.workspace_root) / path for path in current.spec.owned_paths))
                if worker_occupancy(connection)[0] >= current.spec.max_workers:
                    raise ConflictError("workflow worker capacity is occupied")
                protect_owned_paths(connection, tuple(Path(current.spec.workspace_root) / path for path in current.spec.owned_paths))
            return self._commit(connection, current, workflow_id, event_type, payload, command_id, request)

    def create(self, spec: WorkflowSpec, command_id: str) -> WorkflowSnapshot:
        if not isinstance(spec, WorkflowSpec):
            raise ValidationError("workflow creation requires a validated specification")
        return self.mutate(spec.workflow_id, "CREATED", {"spec": spec.to_dict()}, 0, command_id)

    def reconcile(self, workflow_id: str, expected_revision: int, reason: str, command_id: str) -> WorkflowSnapshot:
        from .workspace import verify_workspace
        current = self.get(workflow_id)
        if current.status is not WorkflowStatus.BLOCKED:
            raise ExecutionGuardError("workflow is not blocked and does not need reconciliation")
        if current.active_task_id is None or current.active_attempt_id is None:
            raise StorageError("blocked workflow has no active recovery identity")
        verify_workspace(current.spec, current.manifest_json)
        text_reason = redact_text(reason, 4096)
        attempt = self.ledger.get_attempt(current.active_attempt_id)
        if attempt.task_id != current.active_task_id:
            raise StorageError("workflow recovery attempt does not match its active task")
        if attempt.status.value not in {"FAILED", "CANCELLED", "SUCCEEDED"}:
            raise ExecutionGuardError("external attempt is not terminal; retain the workflow blocked")
        receipts = [item for item in self.ledger.evidence(attempt.task_id)
                    if item.attempt_id == attempt.attempt_id and item.kind == "provider_result"]
        if not receipts:
            raise ExecutionGuardError("workflow recovery requires persisted provider-result evidence")
        receipt = receipts[-1]
        if receipt.to_dict()["payload"].get("cleanup_complete") is not True:
            raise ExecutionGuardError("workflow recovery requires proven provider cleanup")
        payload = {"attempt_id": attempt.attempt_id, "attempt_status": attempt.status.value,
                   "evidence_digest": receipt.digest_sha256, "reason": text_reason}
        return self.mutate(workflow_id, "RECONCILED", payload, expected_revision, command_id)

    def start(self, workflow_id: str, expected_revision: int, command_id: str) -> WorkflowSnapshot:
        task_identifier = uuid5(NAMESPACE_URL, f"diamond:workflow:{workflow_id}:revision:{expected_revision}").hex
        attempt_identifier = uuid5(NAMESPACE_URL, f"diamond:workflow-attempt:{task_identifier}").hex
        return self.mutate(workflow_id, "STARTED", {"task_id": task_identifier, "attempt_id": attempt_identifier},
                           expected_revision, command_id, one_shot=True)

    def publish(self, expected: WorkflowSnapshot, response: object) -> tuple[WorkflowSnapshot, tuple[ArtifactRef, ...]]:
        from .workspace import Publication, PublicationRollbackError
        workflow_id = expected.spec.workflow_id
        command_id = f"workflow:{workflow_id}:apply:{expected.revision}"
        try:
            with Publication(Path(expected.spec.workspace_root)) as publication, self.ledger._connection(write=True) as connection:
                current = self._read(connection, workflow_id)
                if current != expected or current.status not in {WorkflowStatus.BUILDING, WorkflowStatus.DEBUGGING}:
                    raise ConflictError("workflow authorization changed before publishing files")
                from .occupancy import protect_delivery_roots
                protect_delivery_roots(connection, tuple(Path(current.spec.workspace_root) / path for path in current.spec.owned_paths))
                manifest, artifacts = publication.apply(current.spec, current.manifest_json, response)
                payload = {"manifest_json": manifest, "artifacts": [artifact.to_dict() for artifact in artifacts]}
                request = canonical_json({"operation": "publish_files", "workflow_id": workflow_id,
                                          "expected_revision": expected.revision, "response_digest": digest_text(canonical_json(response))})
                return self._commit(connection, current, workflow_id, "FILES_APPLIED", payload, command_id, request), artifacts
        except PublicationRollbackError as exc:
            try:
                self.mutate(workflow_id, "BLOCKED", {"reason": "Publication rollback incomplete; inspect workspace before reconciliation"},
                            expected.revision, f"{command_id}:rollback-block")
            except (StorageError, ConflictError) as quarantine_error:
                exc.add_note(f"Could not persist quarantine; retain active ownership: {quarantine_error}")
            raise

    def get(self, workflow_id: str) -> WorkflowSnapshot:
        task_id(workflow_id)
        if not self.ledger._database_present():
            raise NotFoundError("workflow ledger does not exist")
        with self.ledger._connection(write=False) as connection:
            return self._read(connection, workflow_id)

    def list(self) -> tuple[WorkflowSnapshot, ...]:
        if not self.ledger._database_present():
            return ()
        with self.ledger._connection(write=False) as connection:
            return tuple(self._read(connection, row[0]) for row in connection.execute("SELECT workflow_id FROM workflows ORDER BY workflow_id"))

    def replay(self, workflow_id: str) -> WorkflowSnapshot:
        task_id(workflow_id)
        with self.ledger._connection(write=False) as connection:
            current = None
            try:
                for index, row in enumerate(connection.execute("SELECT * FROM workflow_events WHERE workflow_id=? ORDER BY revision", (workflow_id,)), 1):
                    if row["revision"] != index:
                        raise StorageError("workflow event history contains a revision gap")
                    current = reduce_event(current, row["event_type"], parse_canonical_json(row["payload_json"]), row["timestamp"])
            except (ValidationError, TypeError, ValueError, KeyError) as exc:
                raise StorageError("workflow replay has an invalid event") from exc
            if current is None or current != self._read(connection, workflow_id):
                raise StorageError("workflow replay does not match its projection")
            return current

    def events(self, workflow_id: str) -> tuple[dict[str, object], ...]:
        task_id(workflow_id)
        with self.ledger._connection(write=False) as connection:
            rows = connection.execute(
                "SELECT revision,event_type,timestamp,command_id,payload_json "
                "FROM workflow_events WHERE workflow_id=? ORDER BY revision",
                (workflow_id,)).fetchall()
            return tuple({
                "workflow_id": workflow_id,
                "revision": row["revision"],
                "event_type": row["event_type"],
                "timestamp": row["timestamp"],
                "command_id": row["command_id"],
                "payload": parse_canonical_json(row["payload_json"]),
            } for row in rows)
