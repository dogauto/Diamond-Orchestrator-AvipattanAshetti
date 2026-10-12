"""Durable, append-only accounting for the bounded profile pilot."""

from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

from .agent_profiles import checked_digest, normalize_tags
from .errors import (CommandConflictError, ConflictError, ExecutionGuardError,
                     NotFoundError, StorageError, ValidationError)
from .evidence import digest_text, redact_text
from .store import EventStore
from .validation import (bounded_int, canonical_json, command_id as validate_command_id,
                         parse_canonical_json, task_id, text, utc_now, utc_timestamp)


TRIAL_STATUSES = frozenset(("REGISTERED", "CANCELLED"))
OUTCOME_STATUSES = frozenset(("SUCCEEDED", "FAILED", "UNKNOWN", "CANCELLED"))


@dataclass(frozen=True)
class TrialRegistration:
    trial_id: str
    design_json: str
    design_digest: str
    catalog_digest: str
    profile_ids: tuple[str, ...]
    max_assignments: int
    max_provider_calls: int
    max_wall_clock_seconds: int
    max_cost_microunits: int
    outcome_maturation_hours: int
    created_at: str
    command_id: str

    def __post_init__(self) -> None:
        validate_command_id(self.trial_id)
        parse_canonical_json(self.design_json)
        checked_digest(self.design_digest)
        checked_digest(self.catalog_digest)
        if type(self.profile_ids) is not tuple or len(self.profile_ids) != 2:
            raise ValidationError("trial registration requires exactly two profile IDs")
        for profile_id in self.profile_ids:
            text(profile_id, "profile ID", 128)
        for value, name in ((self.max_assignments, "max_assignments"),
                            (self.max_provider_calls, "max_provider_calls"),
                            (self.max_wall_clock_seconds, "max_wall_clock_seconds"),
                            (self.max_cost_microunits, "max_cost_microunits"),
                            (self.outcome_maturation_hours, "outcome_maturation_hours")):
            bounded_int(value, name, 1, 10**15 if name == "max_cost_microunits" else 1_000_000)
        utc_timestamp(self.created_at)
        validate_command_id(self.command_id)

    def to_dict(self) -> dict[str, object]:
        return {"trial_id": self.trial_id, "design": parse_canonical_json(self.design_json),
                "trial_design_digest": self.design_digest, "catalog_digest": self.catalog_digest,
                "profile_ids": list(self.profile_ids), "max_assignments": self.max_assignments,
                "max_provider_calls": self.max_provider_calls,
                "max_wall_clock_seconds": self.max_wall_clock_seconds,
                "max_cost_microunits": self.max_cost_microunits,
                "outcome_maturation_hours": self.outcome_maturation_hours,
                "created_at": self.created_at, "command_id": self.command_id}


@dataclass(frozen=True)
class TrialAssignment:
    assignment_id: str
    trial_id: str
    workflow_id: str
    workflow_revision: int
    workflow_digest: str
    task_tags: tuple[str, ...]
    lineage_key: str
    arm: str
    profile_id: str | None
    profile_version: str | None
    profile_digest: str | None
    selection_digest: str
    reserved_provider_calls: int
    reserved_wall_clock_seconds: int
    created_at: str
    command_id: str
    assignment_digest: str

    def __post_init__(self) -> None:
        task_id(self.assignment_id)
        validate_command_id(self.trial_id)
        task_id(self.workflow_id)
        bounded_int(self.workflow_revision, "workflow revision", 1, 2**63 - 1)
        checked_digest(self.workflow_digest)
        normalize_tags(self.task_tags)
        text(self.lineage_key, "trial lineage key", 256)
        if self.arm != "control" and (not isinstance(self.arm, str) or not self.arm.startswith("builder-")):
            raise ValidationError("trial arm must be control or a registered builder profile")
        if self.arm == "control":
            if any(value is not None for value in (self.profile_id, self.profile_version, self.profile_digest)):
                raise ValidationError("control assignments must not retain profile identity")
        elif self.profile_id != self.arm or self.profile_version is None or self.profile_digest is None:
            raise ValidationError("profile assignment must retain the selected profile identity")
        if self.profile_id is not None:
            text(self.profile_id, "profile ID", 128)
            text(self.profile_version, "profile version", 64)
            checked_digest(self.profile_digest)
        checked_digest(self.selection_digest)
        bounded_int(self.reserved_provider_calls, "reserved provider calls", 1, 1_000_000)
        bounded_int(self.reserved_wall_clock_seconds, "reserved wall clock seconds", 1, 1_000_000)
        utc_timestamp(self.created_at)
        validate_command_id(self.command_id)
        checked_digest(self.assignment_digest)

    def to_dict(self) -> dict[str, object]:
        return {"assignment_id": self.assignment_id, "trial_id": self.trial_id,
                "workflow_id": self.workflow_id, "workflow_revision": self.workflow_revision,
                "workflow_digest": self.workflow_digest, "task_tags": list(self.task_tags),
                "lineage_key": self.lineage_key, "arm": self.arm, "profile_id": self.profile_id,
                "profile_version": self.profile_version, "profile_digest": self.profile_digest,
                "selection_digest": self.selection_digest,
                "reserved_provider_calls": self.reserved_provider_calls,
                "reserved_wall_clock_seconds": self.reserved_wall_clock_seconds,
                "created_at": self.created_at, "command_id": self.command_id,
                "assignment_digest": self.assignment_digest}


@dataclass(frozen=True)
class TrialOutcome:
    outcome_id: str
    assignment_id: str
    attempt_number: int
    outcome_status: str
    provider_calls: int
    wall_clock_seconds: int
    cost_microunits: int
    unknown_reason: str | None
    observed_at: str
    digest_sha256: str
    command_id: str

    def __post_init__(self) -> None:
        task_id(self.outcome_id)
        task_id(self.assignment_id)
        bounded_int(self.attempt_number, "trial attempt number", 1, 1_000_000)
        if self.outcome_status not in OUTCOME_STATUSES:
            raise ValidationError("unsupported trial outcome status")
        bounded_int(self.provider_calls, "provider calls", 0, 1_000_000)
        bounded_int(self.wall_clock_seconds, "wall clock seconds", 0, 1_000_000)
        bounded_int(self.cost_microunits, "cost microunits", 0, 10**15)
        if self.outcome_status == "UNKNOWN":
            text(self.unknown_reason, "unknown outcome reason", 4096)
        elif self.unknown_reason is not None:
            raise ValidationError("only unknown outcomes may carry an unknown reason")
        utc_timestamp(self.observed_at)
        checked_digest(self.digest_sha256)
        validate_command_id(self.command_id)

    def to_dict(self) -> dict[str, object]:
        return {"outcome_id": self.outcome_id, "assignment_id": self.assignment_id,
                "attempt_number": self.attempt_number, "outcome_status": self.outcome_status,
                "provider_calls": self.provider_calls, "wall_clock_seconds": self.wall_clock_seconds,
                "cost_microunits": self.cost_microunits, "unknown_reason": self.unknown_reason,
                "observed_at": self.observed_at, "digest_sha256": self.digest_sha256,
                "command_id": self.command_id}


class TrialStore:
    def __init__(self, ledger: EventStore) -> None:
        if not isinstance(ledger, EventStore):
            raise ValidationError("trial storage requires an EventStore")
        self.ledger = ledger

    @staticmethod
    def _registration(row) -> TrialRegistration:
        try:
            profile_ids = parse_canonical_json(row["profile_ids_json"])
            if type(profile_ids) is not list:
                raise ValidationError("persisted trial profile IDs must be an array")
            return TrialRegistration(row["trial_id"], row["design_json"], row["design_digest"],
                                      row["catalog_digest"], tuple(profile_ids), row["max_assignments"],
                                      row["max_provider_calls"], row["max_wall_clock_seconds"],
                                      row["max_cost_microunits"],
                                      row["outcome_maturation_hours"], row["created_at"], row["command_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise StorageError("invalid persisted profile trial registration") from exc

    @staticmethod
    def _assignment(row) -> TrialAssignment:
        try:
            tags = parse_canonical_json(row["task_tags_json"])
            if type(tags) is not list:
                raise ValidationError("persisted trial task tags must be an array")
            assignment = TrialAssignment(row["assignment_id"], row["trial_id"], row["workflow_id"],
                row["workflow_revision"], row["workflow_digest"], tuple(tags), row["lineage_key"],
                row["arm"], row["profile_id"], row["profile_version"], row["profile_digest"],
                row["selection_digest"], row["reserved_provider_calls"], row["reserved_wall_clock_seconds"],
                row["created_at"], row["command_id"], row["assignment_digest"])
            if digest_text(row["assignment_json"]) != row["assignment_digest"]:
                raise ValidationError("persisted trial assignment digest mismatch")
            return assignment
        except (KeyError, TypeError, ValueError) as exc:
            raise StorageError("invalid persisted profile trial assignment") from exc

    @staticmethod
    def _outcome(row) -> TrialOutcome:
        try:
            outcome = TrialOutcome(row["outcome_id"], row["assignment_id"], row["attempt_number"],
                                   row["outcome_status"], row["provider_calls"], row["wall_clock_seconds"],
                                   row["cost_microunits"], row["unknown_reason"], row["observed_at"],
                                   row["digest_sha256"], row["command_id"])
            if digest_text(row["payload_json"]) != row["digest_sha256"]:
                raise ValidationError("persisted trial outcome digest mismatch")
            return outcome
        except (KeyError, TypeError, ValueError) as exc:
            raise StorageError("invalid persisted profile trial outcome") from exc

    @staticmethod
    def _status(connection, trial_id: str) -> str:
        if connection.execute("SELECT 1 FROM profile_trial_events WHERE trial_id=? AND event_type='CANCELLED'",
                              (trial_id,)).fetchone():
            return "CANCELLED"
        return "REGISTERED"

    def register(self, design: dict[str, object], design_digest: str, command: str) -> TrialRegistration:
        if not isinstance(design, dict):
            raise ValidationError("trial registration requires a validated design object")
        validate_command_id(command)
        trial_id = design.get("trial_id")
        validate_command_id(trial_id)
        checked_digest(design_digest)
        if digest_text(canonical_json(design)) != design_digest:
            raise ValidationError("trial design digest does not match its canonical design")
        if (design.get("role"), design.get("provider")) != ("builder", "claude"):
            raise ValidationError("registered M2 trials must use the fixed Claude Builder route")
        profile_ids = design.get("profile_ids")
        if type(profile_ids) is not list or len(profile_ids) != 2 or any(not isinstance(value, str) for value in profile_ids):
            raise ValidationError("trial registration requires two profile IDs")
        request = canonical_json({"operation": "trial_register", "design": design, "design_digest": design_digest})
        with self.ledger._connection(write=True) as connection:
            existing = connection.execute("SELECT * FROM profile_trials WHERE command_id=?", (command,)).fetchone()
            if existing is not None:
                if existing["request_json"] != request:
                    raise CommandConflictError(f"command {command} already committed different trial input")
                return self._registration(existing)
            if connection.execute("SELECT 1 FROM profile_trials WHERE trial_id=?", (trial_id,)).fetchone():
                raise ConflictError(f"trial {trial_id} already exists; use its original command ID")
            timestamp = utc_now()
            connection.execute("""INSERT INTO profile_trials
                (trial_id,schema_version,design_json,design_digest,catalog_digest,role,provider,profile_ids_json,
                 max_assignments,max_provider_calls,max_wall_clock_seconds,max_cost_microunits,outcome_maturation_hours,
                 created_at,command_id,request_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (trial_id, 1, canonical_json(design), design_digest, design["catalog_digest"], design["role"],
                 design["provider"], canonical_json(profile_ids), design["max_assignments"],
                 design["max_provider_calls"], design["max_wall_clock_seconds"],
                 design["max_cost_microunits"],
                 design["outcome_maturation_hours"], timestamp, command, request))
            return TrialRegistration(trial_id, canonical_json(design), design_digest, design["catalog_digest"],
                tuple(profile_ids), design["max_assignments"], design["max_provider_calls"],
                design["max_wall_clock_seconds"], design["max_cost_microunits"],
                design["outcome_maturation_hours"], timestamp, command)

    def get(self, trial_id: str) -> TrialRegistration:
        validate_command_id(trial_id)
        if not self.ledger._database_present():
            raise NotFoundError(f"trial {trial_id} does not exist")
        with self.ledger._connection(write=False) as connection:
            row = connection.execute("SELECT * FROM profile_trials WHERE trial_id=?", (trial_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"trial {trial_id} does not exist")
            return self._registration(row)

    def list(self) -> tuple[TrialRegistration, ...]:
        if not self.ledger._database_present():
            return ()
        with self.ledger._connection(write=False) as connection:
            return tuple(self._registration(row) for row in connection.execute(
                "SELECT * FROM profile_trials ORDER BY created_at, trial_id"))

    def cancel(self, trial_id: str, reason: str, command: str) -> dict[str, object]:
        validate_command_id(trial_id)
        validate_command_id(command)
        reason = redact_text(reason, 4096)
        text(reason, "trial cancellation reason", 4096)
        request = canonical_json({"operation": "trial_cancel", "trial_id": trial_id, "reason": reason})
        with self.ledger._connection(write=True) as connection:
            registration = connection.execute("SELECT * FROM profile_trials WHERE trial_id=?", (trial_id,)).fetchone()
            if registration is None:
                raise NotFoundError(f"trial {trial_id} does not exist")
            existing = connection.execute("SELECT * FROM profile_trial_events WHERE command_id=?", (command,)).fetchone()
            if existing is not None:
                if (existing["trial_id"], existing["payload_json"]) != (trial_id, canonical_json({"reason": reason})):
                    raise CommandConflictError(f"command {command} already committed different cancellation input")
                return {"trial_id": trial_id, "status": "CANCELLED", "reason": reason}
            if self._status(connection, trial_id) == "CANCELLED":
                raise ConflictError(f"trial {trial_id} is already cancelled")
            timestamp = utc_now()
            payload = canonical_json({"reason": reason})
            connection.execute("INSERT INTO profile_trial_events VALUES (?,?,?,?,?,?)",
                               (trial_id, 1, "CANCELLED", timestamp, command, payload))
            return {"trial_id": trial_id, "status": "CANCELLED", "reason": reason}

    def assign(self, assignment: TrialAssignment, assignment_json: dict[str, object]) -> TrialAssignment:
        if not isinstance(assignment, TrialAssignment) or not isinstance(assignment_json, dict):
            raise ValidationError("trial assignment requires validated identity and snapshot")
        snapshot_json = canonical_json(assignment_json)
        if digest_text(snapshot_json) != assignment.assignment_digest:
            raise ValidationError("trial assignment snapshot digest does not match its identity")
        request = canonical_json({"operation": "trial_assign", "assignment": assignment_json})
        with self.ledger._connection(write=True) as connection:
            existing = connection.execute("SELECT * FROM profile_trial_assignments WHERE command_id=?",
                                         (assignment.command_id,)).fetchone()
            if existing is not None:
                if existing["assignment_json"] != canonical_json(assignment_json):
                    raise CommandConflictError(f"command {assignment.command_id} already committed different assignment input")
                return self._assignment(existing)
            registration = connection.execute("SELECT * FROM profile_trials WHERE trial_id=?",
                                              (assignment.trial_id,)).fetchone()
            if registration is None:
                raise NotFoundError(f"trial {assignment.trial_id} does not exist")
            if (assignment_json.get("trial_id"), assignment_json.get("workflow_id"), assignment_json.get("arm"),
                    assignment_json.get("profile_id"), assignment_json.get("selection_digest")) != (
                    assignment.trial_id, assignment.workflow_id, assignment.arm, assignment.profile_id,
                    assignment.selection_digest):
                raise ValidationError("trial assignment snapshot identity differs from its columns")
            if any(assignment_json.get(field) != expected for field, expected in (
                    ("assignment_id", assignment.assignment_id),
                    ("created_at", assignment.created_at),
                    ("workflow_revision", assignment.workflow_revision),
                    ("workflow_digest", assignment.workflow_digest),
                    ("task_tags", list(assignment.task_tags)),
                    ("lineage_key", assignment.lineage_key),
                    ("profile_version", assignment.profile_version),
                    ("profile_digest", assignment.profile_digest),
                    ("reserved_provider_calls", assignment.reserved_provider_calls),
                    ("reserved_wall_clock_seconds", assignment.reserved_wall_clock_seconds))):
                raise ValidationError("trial assignment snapshot differs from its immutable identity")
            if (assignment_json.get("trial_design_digest"), assignment_json.get("catalog_digest")) != (
                    registration["design_digest"], registration["catalog_digest"]):
                raise ValidationError("trial assignment uses a stale registration or profile catalog")
            if self._status(connection, assignment.trial_id) != "REGISTERED":
                raise ExecutionGuardError("cancelled trials cannot receive assignments")
            count = connection.execute("SELECT COUNT(*) FROM profile_trial_assignments WHERE trial_id=?",
                                       (assignment.trial_id,)).fetchone()[0]
            if count >= registration["max_assignments"]:
                raise ExecutionGuardError("trial assignment budget is exhausted")
            reserved = connection.execute("""SELECT COALESCE(SUM(reserved_provider_calls),0),
                COALESCE(SUM(reserved_wall_clock_seconds),0) FROM profile_trial_assignments WHERE trial_id=?""",
                (assignment.trial_id,)).fetchone()
            if reserved[0] + assignment.reserved_provider_calls > registration["max_provider_calls"]:
                raise ExecutionGuardError("trial provider-call reservation exceeds its aggregate budget")
            if reserved[1] + assignment.reserved_wall_clock_seconds > registration["max_wall_clock_seconds"]:
                raise ExecutionGuardError("trial wall-clock reservation exceeds its aggregate budget")
            timestamp = assignment.created_at
            connection.execute("""INSERT INTO profile_trial_assignments
                (assignment_id,schema_version,trial_id,workflow_id,workflow_revision,workflow_digest,task_tags_json,
                 lineage_key,arm,profile_id,profile_version,profile_digest,selection_digest,reserved_provider_calls,
                 reserved_wall_clock_seconds,created_at,command_id,assignment_json,assignment_digest)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (assignment.assignment_id, 1, assignment.trial_id, assignment.workflow_id, assignment.workflow_revision,
                 assignment.workflow_digest, canonical_json(list(assignment.task_tags)), assignment.lineage_key,
                 assignment.arm, assignment.profile_id, assignment.profile_version, assignment.profile_digest,
                 assignment.selection_digest, assignment.reserved_provider_calls, assignment.reserved_wall_clock_seconds,
                 timestamp, assignment.command_id, snapshot_json, assignment.assignment_digest))
            return assignment

    def get_assignment(self, assignment_id: str) -> TrialAssignment:
        task_id(assignment_id)
        if not self.ledger._database_present():
            raise NotFoundError(f"assignment {assignment_id} does not exist")
        with self.ledger._connection(write=False) as connection:
            row = connection.execute("SELECT * FROM profile_trial_assignments WHERE assignment_id=?",
                                     (assignment_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"assignment {assignment_id} does not exist")
            return self._assignment(row)

    def assignments(self, trial_id: str) -> tuple[TrialAssignment, ...]:
        validate_command_id(trial_id)
        if not self.ledger._database_present():
            return ()
        with self.ledger._connection(write=False) as connection:
            return tuple(self._assignment(row) for row in connection.execute(
                "SELECT * FROM profile_trial_assignments WHERE trial_id=? ORDER BY created_at, assignment_id",
                (trial_id,)))

    def outcomes(self, assignment_id: str) -> tuple[TrialOutcome, ...]:
        task_id(assignment_id)
        if not self.ledger._database_present():
            return ()
        with self.ledger._connection(write=False) as connection:
            return tuple(self._outcome(row) for row in connection.execute(
                "SELECT * FROM profile_trial_outcomes WHERE assignment_id=? ORDER BY attempt_number",
                (assignment_id,)))

    def record_outcome(self, assignment_id: str, status: str, provider_calls: int,
                       wall_clock_seconds: int, cost_microunits: int, unknown_reason: str | None,
                       command: str) -> TrialOutcome:
        task_id(assignment_id)
        validate_command_id(command)
        if status not in OUTCOME_STATUSES:
            raise ValidationError("unsupported trial outcome status")
        bounded_int(provider_calls, "provider calls", 0, 1_000_000)
        bounded_int(wall_clock_seconds, "wall clock seconds", 0, 1_000_000)
        bounded_int(cost_microunits, "cost microunits", 0, 10**15)
        if status == "UNKNOWN":
            unknown_reason = redact_text(unknown_reason, 4096)
            text(unknown_reason, "unknown outcome reason", 4096)
        elif unknown_reason is not None:
            raise ValidationError("only unknown outcomes may carry an unknown reason")
        if status == "CANCELLED" and (provider_calls != 0 or wall_clock_seconds != 0 or cost_microunits != 0):
            raise ValidationError("cancelled trial outcomes cannot claim provider usage")
        request = canonical_json({"operation": "trial_outcome", "assignment_id": assignment_id,
                                  "outcome_status": status, "provider_calls": provider_calls,
                                  "wall_clock_seconds": wall_clock_seconds, "cost_microunits": cost_microunits,
                                  "unknown_reason": unknown_reason})
        with self.ledger._connection(write=True) as connection:
            existing = connection.execute("SELECT * FROM profile_trial_outcomes WHERE command_id=?", (command,)).fetchone()
            if existing is not None:
                if existing["request_json"] != request:
                    raise CommandConflictError(f"command {command} already committed different outcome input")
                return self._outcome(existing)
            assignment = connection.execute("SELECT * FROM profile_trial_assignments WHERE assignment_id=?",
                                            (assignment_id,)).fetchone()
            if assignment is None:
                raise NotFoundError(f"assignment {assignment_id} does not exist")
            trial_id = assignment["trial_id"]
            if self._status(connection, trial_id) == "CANCELLED" and status != "CANCELLED":
                raise ExecutionGuardError("cancelled trials cannot record provider outcomes")
            terminal = connection.execute("""SELECT 1 FROM profile_trial_outcomes
                WHERE assignment_id=? AND outcome_status IN ('SUCCEEDED','CANCELLED') LIMIT 1""",
                (assignment_id,)).fetchone()
            if terminal is not None:
                raise ExecutionGuardError("trial assignment already has a terminal outcome")
            previous = connection.execute("""SELECT COALESCE(SUM(provider_calls),0),
                COALESCE(SUM(wall_clock_seconds),0), COALESCE(SUM(cost_microunits),0)
                FROM profile_trial_outcomes WHERE assignment_id=?""",
                (assignment_id,)).fetchone()
            if previous[0] + provider_calls > assignment["reserved_provider_calls"]:
                raise ExecutionGuardError("trial outcome exceeds the assignment provider-call reservation")
            if previous[1] + wall_clock_seconds > assignment["reserved_wall_clock_seconds"]:
                raise ExecutionGuardError("trial outcome exceeds the assignment wall-clock reservation")
            total_cost = connection.execute("""SELECT COALESCE(SUM(cost_microunits),0)
                FROM profile_trial_outcomes WHERE assignment_id IN
                (SELECT assignment_id FROM profile_trial_assignments WHERE trial_id=?)""",
                (trial_id,)).fetchone()[0]
            trial = connection.execute("SELECT max_cost_microunits FROM profile_trials WHERE trial_id=?",
                                       (trial_id,)).fetchone()
            if total_cost + cost_microunits > trial[0]:
                raise ExecutionGuardError("trial outcome exceeds the aggregate cost budget")
            attempt_number = connection.execute(
                "SELECT COALESCE(MAX(attempt_number),0)+1 FROM profile_trial_outcomes WHERE assignment_id=?",
                (assignment_id,)).fetchone()[0]
            outcome_id = uuid5(NAMESPACE_URL, f"diamond:trial-outcome:{assignment_id}:{attempt_number}").hex
            observed_at = utc_now()
            payload = {"outcome_id": outcome_id, "assignment_id": assignment_id, "attempt_number": attempt_number,
                       "outcome_status": status, "provider_calls": provider_calls,
                       "wall_clock_seconds": wall_clock_seconds, "cost_microunits": cost_microunits,
                       "unknown_reason": unknown_reason, "observed_at": observed_at}
            payload_json = canonical_json(payload)
            digest = digest_text(payload_json)
            connection.execute("""INSERT INTO profile_trial_outcomes
                (outcome_id,schema_version,assignment_id,attempt_number,outcome_status,provider_calls,wall_clock_seconds,
                 cost_microunits,unknown_reason,observed_at,payload_json,digest_sha256,command_id,request_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (outcome_id, 1, assignment_id, attempt_number, status, provider_calls, wall_clock_seconds,
                 cost_microunits, unknown_reason, observed_at, payload_json, digest, command, request))
            return TrialOutcome(outcome_id, assignment_id, attempt_number, status, provider_calls,
                                wall_clock_seconds, cost_microunits, unknown_reason, observed_at, digest, command)

    def status(self, trial_id: str) -> dict[str, object]:
        registration = self.get(trial_id)
        with self.ledger._connection(write=False) as connection:
            assignments = tuple(self._assignment(row) for row in connection.execute(
                "SELECT * FROM profile_trial_assignments WHERE trial_id=? ORDER BY created_at, assignment_id",
                (trial_id,)))
            outcomes = tuple(self._outcome(row) for row in connection.execute(
                """SELECT profile_trial_outcomes.* FROM profile_trial_outcomes
                JOIN profile_trial_assignments ON profile_trial_assignments.assignment_id=profile_trial_outcomes.assignment_id
                WHERE profile_trial_assignments.trial_id=? ORDER BY profile_trial_outcomes.observed_at, profile_trial_outcomes.outcome_id""",
                (trial_id,)))
            used_calls = sum(item.provider_calls for item in outcomes)
            used_seconds = sum(item.wall_clock_seconds for item in outcomes)
            used_cost = sum(item.cost_microunits for item in outcomes)
            reserved_calls = sum(item.reserved_provider_calls for item in assignments)
            reserved_seconds = sum(item.reserved_wall_clock_seconds for item in assignments)
            return {"trial": registration.to_dict(), "status": self._status(connection, trial_id),
                    "assignments": [item.to_dict() for item in assignments],
                    "outcomes": [item.to_dict() for item in outcomes],
                    "accounting": {"assignments": len(assignments), "max_assignments": registration.max_assignments,
                                   "reserved_provider_calls": reserved_calls,
                                   "max_provider_calls": registration.max_provider_calls,
                                   "used_provider_calls": used_calls,
                                   "remaining_reserved_provider_calls": registration.max_provider_calls - reserved_calls,
                                   "reserved_wall_clock_seconds": reserved_seconds,
                                   "max_wall_clock_seconds": registration.max_wall_clock_seconds,
                                   "used_wall_clock_seconds": used_seconds,
                                   "remaining_reserved_wall_clock_seconds": registration.max_wall_clock_seconds - reserved_seconds,
                                   "used_cost_microunits": used_cost,
                                   "max_cost_microunits": registration.max_cost_microunits,
                                   "remaining_cost_microunits": registration.max_cost_microunits - used_cost},
                    "execution": {"provider_launch": False, "live_execution_authorized": False}}
