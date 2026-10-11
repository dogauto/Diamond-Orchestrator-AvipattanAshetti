import sqlite3
from dataclasses import replace
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from .delivery_contracts import DeliverySnapshot, DeliverySpec, DeliveryStatus, PASSED, READY, ROLES, RUNNING, next_role
from .delivery_gates import evidence, gate, provider_response
from .domain import Role
from .errors import CommandConflictError, ConflictError, ExecutionGuardError, NotFoundError, StorageError, ValidationError
from .evidence import EvidenceRecord, digest_text, redact_text, safe_excerpt
from .integration import capture_components, integration_response
from .store import EventStore
from .test_inputs import capture_test_inputs, verify_test_inputs
from .validation import bounded_int, canonical_json, command_id, parse_canonical_json, task_id, utc_now
from .workspace import Publication, PublicationRollbackError, workspace_manifest


def reduce_event(current: DeliverySnapshot | None, kind: str, payload: dict, timestamp: str) -> DeliverySnapshot:
    if current is None and kind == "CREATED":
        spec = DeliverySpec.from_dict(payload["spec"])
        return DeliverySnapshot(spec, DeliveryStatus.CREATED, 1, spec.base_manifest_json, payload["base_sources_json"], payload["test_inputs_json"], "{}",
            payload["inherited_findings_json"], payload["prior_manifest_json"], "[]", None, None, timestamp)
    if current is None:
        raise ValidationError("delivery must begin with creation")
    changes = {"revision": current.revision + 1, "updated_at": timestamp}
    if kind == "STARTED" and current.status in READY:
        return replace(current, **changes, status=RUNNING[READY.index(current.status)], detail="",
                       active_task_id=payload["task_id"], active_attempt_id=payload["attempt_id"])
    if kind == "FILES_APPLIED" and current.status is DeliveryStatus.INTEGRATING:
        return replace(current, **changes, manifest_json=payload["manifest_json"], decisions_json=payload["decisions_json"])
    if kind == "FINISHED" and current.status in RUNNING:
        report = payload["report"]
        index = RUNNING.index(current.status)
        if report["role"] != ROLES[index].value or report["candidate_digest"] != digest_text(current.manifest_json) or type(report["passed"]) is not bool:
            raise ValidationError("delivery finish does not belong to the current stage and candidate")
        reports = parse_canonical_json(current.reports_json)
        reports[report["role"]] = report
        return replace(current, **changes, reports_json=canonical_json(reports),
            status=PASSED[index] if report["passed"] else DeliveryStatus.REJECTED,
            detail="Scoped stage evidence accepted" if report["passed"] else "Required checks or blocking findings rejected candidate")
    if kind in {"BLOCKED", "STALE"} and current.status not in {DeliveryStatus.STALE, DeliveryStatus.BLOCKED}:
        return replace(current, **changes, status=DeliveryStatus(kind), detail=payload["reason"])
    raise ExecutionGuardError("invalid delivery state transition")


class DeliveryStore:
    def __init__(self, ledger: EventStore) -> None:
        if not isinstance(ledger, EventStore):
            raise ValidationError("delivery store requires EventStore")
        self.ledger = ledger

    @staticmethod
    def _decode(value: str) -> DeliverySnapshot:
        try:
            return DeliverySnapshot.from_dict(parse_canonical_json(value))
        except (ValidationError, TypeError, ValueError, KeyError) as exc:
            raise StorageError("invalid stored delivery snapshot") from exc

    def _read(self, connection: sqlite3.Connection, identifier: str) -> DeliverySnapshot:
        row = connection.execute("SELECT * FROM deliveries WHERE delivery_id=?", (identifier,)).fetchone()
        if row is None:
            raise NotFoundError("delivery does not exist")
        snapshot = self._decode(row["snapshot_json"])
        if (snapshot.spec.delivery_id, snapshot.revision, snapshot.status.value) != (row["delivery_id"], row["revision"], row["status"]):
            raise StorageError("delivery projection differs from its snapshot")
        return snapshot

    def _commit(self, connection: sqlite3.Connection, current: DeliverySnapshot | None, kind: str,
                payload: dict, identifier: str, key: str, request: str) -> DeliverySnapshot:
        value = canonical_json(payload)
        if redact_text(value) != value:
            raise ValidationError("delivery event contains credential-like text")
        timestamp = utc_now()
        target = reduce_event(current, kind, payload, timestamp)
        encoded = canonical_json(target.to_dict())
        if current is None:
            connection.execute("INSERT INTO deliveries VALUES (?,?,?,?)", (identifier, target.revision, target.status.value, encoded))
        else:
            connection.execute("UPDATE deliveries SET revision=?,status=?,snapshot_json=? WHERE delivery_id=? AND revision=?",
                               (target.revision, target.status.value, encoded, identifier, current.revision))
        connection.execute("INSERT INTO delivery_events VALUES (?,?,?,?,?,?)", (identifier, target.revision, kind, timestamp, key, value))
        connection.execute("INSERT INTO delivery_commands VALUES (?,?,?)", (key, request, encoded))
        return target

    def _prior(self, connection: sqlite3.Connection, spec: DeliverySpec) -> tuple[str, str | None]:
        related = [self._decode(row[0]) for row in connection.execute("SELECT snapshot_json FROM deliveries JOIN delivery_events USING(delivery_id) "
                   "WHERE delivery_events.revision=1 ORDER BY delivery_events.timestamp,deliveries.delivery_id")]
        related = [item for item in related if item.spec.workspace_root == spec.workspace_root]
        latest = related[-1] if related else None
        if latest is not None and latest.status not in {DeliveryStatus.ACCEPTED, DeliveryStatus.REJECTED, DeliveryStatus.STALE}:
            raise ConflictError("target already has an active or quarantined delivery")
        prior = self._read(connection, spec.supersedes) if spec.supersedes is not None else None
        if prior is not None:
            if prior != latest or prior.status not in {DeliveryStatus.REJECTED, DeliveryStatus.STALE}:
                raise ExecutionGuardError("revision must supersede the latest known rejected/stale delivery")
            if spec.requirements_json != prior.spec.requirements_json or spec.tests != prior.spec.tests:
                raise ValidationError("finding resolution cannot weaken trusted acceptance requirements or tests")
        elif latest is not None and latest.status is not DeliveryStatus.ACCEPTED and (latest.status is DeliveryStatus.REJECTED or parse_canonical_json(latest.inherited_findings_json)
                                    or any(f["severity"] == "blocking" for r in parse_canonical_json(latest.reports_json).values() for f in r["findings"])):
            raise ExecutionGuardError("unresolved findings must be inherited through explicit supersedes")
        if prior is None:
            return "[]", None
        inherited = {f["finding_id"]: f for f in parse_canonical_json(prior.inherited_findings_json)}
        for report in parse_canonical_json(prior.reports_json).values():
            inherited.update({f["finding_id"]: f for f in report["findings"] if f["severity"] == "blocking"})
        return canonical_json(list(inherited.values())), prior.manifest_json

    def create(self, spec: DeliverySpec, key: str) -> DeliverySnapshot:
        if not isinstance(spec, DeliverySpec):
            raise ValidationError("delivery creation requires a validated specification")
        command_id(key)
        request = canonical_json({"operation": "create_delivery", "spec": spec.to_dict()})
        with self.ledger._connection(write=True) as connection:
            old = connection.execute("SELECT * FROM delivery_commands WHERE command_id=?", (key,)).fetchone()
            if old is not None:
                if old["request_json"] != request:
                    raise CommandConflictError("delivery creation key was used for different input")
                return self._decode(old["snapshot_json"])
            if self.ledger.database.is_relative_to(Path(spec.workspace_root)):
                raise ValidationError("delivery ledger must be outside its editable target")
            from .occupancy import protect_workspace_writers, protect_delivery_roots
            protect_workspace_writers(connection, Path(spec.workspace_root))
            protect_delivery_roots(connection, (Path(spec.workspace_root),))
            refs = tuple((item["workflow_id"], item["revision"]) for item in parse_canonical_json(spec.components_json))
            if capture_components(self.ledger, refs, Path(spec.workspace_root), spec.base_manifest_json) != spec.components_json:
                raise ValidationError("components differ from actual verified workflow evidence")
            inherited, prior_manifest = self._prior(connection, spec)
            base = parse_canonical_json(spec.base_manifest_json)
            sources = [{"relative_path": path, "content": (Path(spec.workspace_root) / path).read_bytes().decode("utf-8")
                        if path in base else None} for path in spec.work_spec().owned_paths]
            base_sources = canonical_json(sources)
            test_inputs = capture_test_inputs(spec.work_spec())
            if len(base_sources) > 16384:
                raise ValidationError("base source context exceeds 16 KiB; use a smaller integration slice")
            if workspace_manifest(Path(spec.workspace_root)) != spec.base_manifest_json:
                raise ConflictError("base changed during candidate acceptance")
            return self._commit(connection, None, "CREATED", {"spec": spec.to_dict(),
                "base_sources_json": base_sources, "test_inputs_json": test_inputs,
                "inherited_findings_json": inherited, "prior_manifest_json": prior_manifest}, spec.delivery_id, key, request)

    def _authorized(self, connection: sqlite3.Connection, expected: DeliverySnapshot, key: str) -> None:
        command_id(key)
        if connection.execute("SELECT 1 FROM delivery_commands WHERE command_id=?", (key,)).fetchone():
            raise ConflictError("delivery command already consumed; refusing duplicate execution")
        if self._read(connection, expected.spec.delivery_id) != expected:
            raise ConflictError("delivery authorization changed; reload its revision")

    def start(self, identifier: str, revision: int) -> DeliverySnapshot:
        task_id(identifier)
        bounded_int(revision, "expected delivery revision", 1, 2**63 - 1)
        with self.ledger._connection(write=True) as connection:
            current = self._read(connection, identifier)
            if current.revision != revision:
                raise ConflictError("delivery revision is stale")
            next_role(current.status)
            root = Path(current.spec.workspace_root)
            if workspace_manifest(root) != current.manifest_json:
                raise ConflictError("delivery candidate changed")
            from .occupancy import delivery_occupancy
            delivery_occupancy(connection, current)
            task = uuid5(NAMESPACE_URL, f"diamond:delivery:{identifier}:{revision}").hex
            attempt = uuid5(NAMESPACE_URL, f"diamond:delivery-attempt:{task}").hex
            key = f"delivery:{identifier}:start:{revision}"
            self._authorized(connection, current, key)
            payload = {"task_id": task, "attempt_id": attempt}
            return self._commit(connection, current, "STARTED", payload, identifier, key, canonical_json(payload))

    def publish(self, expected: DeliverySnapshot, provider_id: str) -> DeliverySnapshot:
        key = f"delivery:{expected.spec.delivery_id}:apply:{expected.revision}"
        try:
            with Publication(Path(expected.spec.workspace_root)) as publication, self.ledger._connection(write=True) as connection:
                self._authorized(connection, expected, key)
                response, _ = provider_response(self.ledger, expected, Role.INTEGRATOR, provider_id)
                replacements = integration_response(expected.spec.components_json, expected.spec.base_manifest_json, response)
                verify_test_inputs(expected.test_inputs_json)
                if expected.status is not DeliveryStatus.INTEGRATING:
                    raise ExecutionGuardError("only Integrator publishes accepted source selections")
                manifest, artifacts = publication.apply(expected.spec.work_spec(), expected.manifest_json, replacements)
                payload = {"manifest_json": manifest, "decisions_json": canonical_json(response["decisions"]),
                           "artifacts": [item.to_dict() for item in artifacts], "provider_evidence_id": provider_id}
                return self._commit(connection, expected, "FILES_APPLIED", payload, expected.spec.delivery_id, key, canonical_json(payload))
        except PublicationRollbackError as exc:
            try:
                self.invalidate(expected, "Publication rollback incomplete; inspect workspace before reconciliation")
            except (StorageError, ConflictError) as quarantine_error:
                exc.add_note(f"Could not persist quarantine; retain active ownership: {quarantine_error}")
            raise

    def finish(self, expected: DeliverySnapshot, provider_id: str, tests_id: str) -> DeliverySnapshot:
        if expected.status not in RUNNING:
            raise ExecutionGuardError("delivery has no running stage")
        role = ROLES[RUNNING.index(expected.status)]
        key = f"delivery:{expected.spec.delivery_id}:finish:{expected.revision}"
        with self.ledger._connection(write=True) as connection:
            self._authorized(connection, expected, key)
            report = gate(self.ledger, expected, role, provider_id, tests_id)
            verify_test_inputs(expected.test_inputs_json)
            if workspace_manifest(Path(expected.spec.workspace_root)) != expected.manifest_json:
                raise ConflictError("candidate changed during verification")
            if role is Role.INTEGRATOR and parse_canonical_json(expected.decisions_json) == []:
                raise ExecutionGuardError("integration decisions and publication evidence are missing")
            if role is Role.RELEASE and set(parse_canonical_json(expected.reports_json)) != {r.value for r in ROLES[:3]}:
                raise ExecutionGuardError("release is missing prerequisite stage evidence")
            encoded = canonical_json(report)
            record = EvidenceRecord(uuid4().hex, expected.active_attempt_id, "delivery_gate", utc_now(), encoded, digest_text(encoded))
            self.ledger._insert_evidence(connection, record, f"{key}:evidence", canonical_json(record.to_dict()))
            return self._commit(connection, expected, "FINISHED", {"report": report, "gate_evidence_id": record.evidence_id},
                                expected.spec.delivery_id, key, encoded)

    def invalidate(self, expected: DeliverySnapshot, reason: str, *, stale: bool = False) -> DeliverySnapshot:
        kind = "STALE" if stale and expected.status not in RUNNING else "BLOCKED"
        key = f"delivery:{expected.spec.delivery_id}:invalidate:{expected.revision}"
        with self.ledger._connection(write=True) as connection:
            self._authorized(connection, expected, key)
            payload = {"reason": safe_excerpt(reason, 4096)}
            return self._commit(connection, expected, kind, payload, expected.spec.delivery_id, key, canonical_json(payload))

    def validate(self, identifier: str, config_digest: str) -> DeliverySnapshot:
        current = self.replay(identifier)
        if current.status in {DeliveryStatus.BLOCKED, DeliveryStatus.STALE}:
            return current
        try:
            verify_test_inputs(current.test_inputs_json)
            if current.spec.config_digest != config_digest or workspace_manifest(Path(current.spec.workspace_root)) != current.manifest_json:
                raise ConflictError("candidate or accepted configuration changed; prior evidence is invalid")
        except (ConflictError, ValidationError, OSError) as exc:
            return self.invalidate(current, str(exc), stale=True)
        return current

    def certificate(self, identifier: str, config_digest: str) -> dict:
        current = self.validate(identifier, config_digest)
        if current.status is not DeliveryStatus.ACCEPTED:
            raise ExecutionGuardError("delivery has no current scoped acceptance")
        reports = parse_canonical_json(current.reports_json)
        if set(reports) != {r.value for r in ROLES} or any(not r["passed"] or r["candidate_digest"] != digest_text(current.manifest_json) for r in reports.values()):
            raise ExecutionGuardError("delivery gate evidence is incomplete or stale")
        return {"scope": "bounded_candidate_acceptance", "production_authorization": False,
                "delivery_id": identifier, "candidate_digest": digest_text(current.manifest_json),
                "manifest_json": current.manifest_json, "test_inputs_json": current.test_inputs_json, "gate_reports": reports}

    def get(self, identifier: str) -> DeliverySnapshot:
        task_id(identifier)
        if not self.ledger._database_present():
            raise NotFoundError("delivery ledger does not exist")
        with self.ledger._connection(write=False) as connection:
            return self._read(connection, identifier)

    def list(self) -> tuple[DeliverySnapshot, ...]:
        if not self.ledger._database_present():
            return ()
        with self.ledger._connection(write=False) as connection:
            return tuple(self._read(connection, row[0]) for row in connection.execute("SELECT delivery_id FROM deliveries ORDER BY delivery_id"))

    def replay(self, identifier: str) -> DeliverySnapshot:
        task_id(identifier)
        with self.ledger._connection(write=False) as connection:
            current = None
            try:
                for revision, row in enumerate(connection.execute("SELECT * FROM delivery_events WHERE delivery_id=? ORDER BY revision", (identifier,)), 1):
                    if row["revision"] != revision:
                        raise StorageError("delivery history contains a revision gap")
                    payload = parse_canonical_json(row["payload_json"])
                    if row["event_type"] == "FINISHED":
                        report = payload["report"]
                        checked = gate(self.ledger, current, Role(report["role"]), report["provider_evidence_id"], report["tests_evidence_id"])
                        if checked != report or evidence(self.ledger, payload["gate_evidence_id"]).payload_json != canonical_json(report):
                            raise StorageError("delivery event differs from immutable gate evidence")
                    current = reduce_event(current, row["event_type"], payload, row["timestamp"])
            except (ValidationError, ValueError, TypeError, KeyError) as exc:
                raise StorageError("invalid delivery event history") from exc
            if current is None or current != self._read(connection, identifier):
                raise StorageError("delivery replay differs from its projection")
            return current
