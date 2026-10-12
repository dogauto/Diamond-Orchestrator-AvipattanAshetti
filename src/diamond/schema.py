import sqlite3
import re
import os
import tempfile
from pathlib import Path
from contextlib import closing

from .errors import StorageError, ValidationError
from .improvement_schema import STATEMENTS as IMPROVEMENT_STATEMENTS

SCHEMA_VERSION = 9
SCHEMA_STATEMENTS = (
    """CREATE TABLE tasks (
        task_id TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK(revision >= 1),
        status TEXT NOT NULL CHECK(status IN ('CREATED','BLOCKED','CANCELLED')),
        snapshot_json TEXT NOT NULL)""",
    """CREATE TABLE events (
        event_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL CHECK(schema_version = 1),
        task_id TEXT NOT NULL REFERENCES tasks(task_id),
        revision INTEGER NOT NULL CHECK(revision >= 1), event_type TEXT NOT NULL
            CHECK(event_type IN ('TASK_CREATED','TASK_TRANSITIONED')),
        timestamp TEXT NOT NULL, actor TEXT NOT NULL CHECK(actor = 'controller'),
        command_id TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL,
        UNIQUE(task_id, revision)) WITHOUT ROWID""",
    """CREATE TABLE commands (
        command_id TEXT PRIMARY KEY REFERENCES events(command_id),
        request_json TEXT NOT NULL, snapshot_json TEXT NOT NULL) WITHOUT ROWID""",
    """CREATE TRIGGER events_no_update BEFORE UPDATE ON events
        BEGIN SELECT RAISE(ABORT, 'events are append-only'); END""",
    """CREATE TRIGGER events_no_delete BEFORE DELETE ON events
        BEGIN SELECT RAISE(ABORT, 'events are append-only'); END""",
    """CREATE TRIGGER commands_no_update BEFORE UPDATE ON commands
        BEGIN SELECT RAISE(ABORT, 'commands are immutable'); END""",
    """CREATE TRIGGER commands_no_delete BEFORE DELETE ON commands
        BEGIN SELECT RAISE(ABORT, 'commands are immutable'); END""",
    """CREATE TRIGGER events_no_replace BEFORE INSERT ON events WHEN EXISTS (
        SELECT 1 FROM events WHERE event_id=NEW.event_id OR command_id=NEW.command_id
            OR (task_id=NEW.task_id AND revision=NEW.revision))
        BEGIN SELECT RAISE(ABORT, 'events cannot be replaced'); END""",
    """CREATE TRIGGER commands_no_replace BEFORE INSERT ON commands WHEN EXISTS (
        SELECT 1 FROM commands WHERE command_id=NEW.command_id)
        BEGIN SELECT RAISE(ABORT, 'commands cannot be replaced'); END""",
    """CREATE TABLE attempts (
        attempt_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL CHECK(schema_version = 1),
        task_id TEXT NOT NULL REFERENCES tasks(task_id), task_revision INTEGER NOT NULL CHECK(task_revision >= 1),
        role TEXT NOT NULL, provider TEXT NOT NULL, executable TEXT NOT NULL,
        workspace_root TEXT NOT NULL, status TEXT NOT NULL,
        requested_model TEXT, cli_version TEXT, provider_session_id TEXT, reported_model TEXT,
        created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
        returncode INTEGER, signal INTEGER, stdout_bytes INTEGER NOT NULL CHECK(stdout_bytes >= 0),
        stderr_bytes INTEGER NOT NULL CHECK(stderr_bytes >= 0), stdout_sha256 TEXT, stderr_sha256 TEXT,
        result_status TEXT, detail TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision >= 1),
        UNIQUE(task_id, attempt_id)) WITHOUT ROWID""",
    """CREATE TABLE attempt_commands (
        command_id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
        request_json TEXT NOT NULL, attempt_json TEXT NOT NULL) WITHOUT ROWID""",
    """CREATE TABLE attempt_evidence (
        evidence_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL CHECK(schema_version = 1),
        attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id), kind TEXT NOT NULL,
        created_at TEXT NOT NULL, payload_json TEXT NOT NULL, digest_sha256 TEXT NOT NULL,
        command_id TEXT NOT NULL UNIQUE, request_json TEXT NOT NULL,
        UNIQUE(attempt_id, evidence_id)) WITHOUT ROWID""",
    """CREATE TRIGGER attempt_commands_no_update BEFORE UPDATE ON attempt_commands
        BEGIN SELECT RAISE(ABORT, 'attempt commands are immutable'); END""",
    """CREATE TRIGGER attempt_commands_no_delete BEFORE DELETE ON attempt_commands
        BEGIN SELECT RAISE(ABORT, 'attempt commands are immutable'); END""",
    """CREATE TRIGGER attempt_commands_no_replace BEFORE INSERT ON attempt_commands WHEN EXISTS (
        SELECT 1 FROM attempt_commands WHERE command_id=NEW.command_id)
        BEGIN SELECT RAISE(ABORT, 'attempt commands cannot be replaced'); END""",
    """CREATE TRIGGER attempt_evidence_no_update BEFORE UPDATE ON attempt_evidence
        BEGIN SELECT RAISE(ABORT, 'attempt evidence is immutable'); END""",
    """CREATE TRIGGER attempt_evidence_no_delete BEFORE DELETE ON attempt_evidence
        BEGIN SELECT RAISE(ABORT, 'attempt evidence is immutable'); END""",
    """CREATE TRIGGER attempt_evidence_no_replace BEFORE INSERT ON attempt_evidence WHEN EXISTS (
        SELECT 1 FROM attempt_evidence WHERE evidence_id=NEW.evidence_id OR command_id=NEW.command_id)
        BEGIN SELECT RAISE(ABORT, 'attempt evidence cannot be replaced'); END""",
    """CREATE TABLE dispatch_claims (
        claim_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL CHECK(schema_version = 1),
        task_id TEXT NOT NULL REFERENCES tasks(task_id), task_revision INTEGER NOT NULL CHECK(task_revision >= 1),
        attempt_id TEXT NOT NULL UNIQUE REFERENCES attempts(attempt_id), packet_digest TEXT NOT NULL,
        packet_json TEXT NOT NULL, owner TEXT NOT NULL, created_at TEXT NOT NULL,
        command_id TEXT NOT NULL UNIQUE, request_json TEXT NOT NULL,
        UNIQUE(task_id, task_revision)) WITHOUT ROWID""",
    """CREATE TRIGGER dispatch_claims_no_update BEFORE UPDATE ON dispatch_claims
        BEGIN SELECT RAISE(ABORT, 'dispatch claims are immutable'); END""",
    """CREATE TRIGGER dispatch_claims_no_delete BEFORE DELETE ON dispatch_claims
        BEGIN SELECT RAISE(ABORT, 'dispatch claims are immutable'); END""",
    """CREATE TRIGGER dispatch_claims_no_replace BEFORE INSERT ON dispatch_claims WHEN EXISTS (
        SELECT 1 FROM dispatch_claims WHERE claim_id=NEW.claim_id OR command_id=NEW.command_id
            OR attempt_id=NEW.attempt_id OR (task_id=NEW.task_id AND task_revision=NEW.task_revision))
        BEGIN SELECT RAISE(ABORT, 'dispatch claims cannot be replaced'); END""",
    """CREATE TABLE workflows (
        workflow_id TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK(revision >= 1),
        status TEXT NOT NULL, snapshot_json TEXT NOT NULL) WITHOUT ROWID""",
    """CREATE TABLE workflow_events (
        workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id), revision INTEGER NOT NULL CHECK(revision >= 1),
        event_type TEXT NOT NULL, timestamp TEXT NOT NULL, command_id TEXT NOT NULL UNIQUE,
        payload_json TEXT NOT NULL, PRIMARY KEY(workflow_id,revision)) WITHOUT ROWID""",
    """CREATE TABLE workflow_commands (
        command_id TEXT PRIMARY KEY REFERENCES workflow_events(command_id), request_json TEXT NOT NULL,
        snapshot_json TEXT NOT NULL) WITHOUT ROWID""",
    """CREATE TRIGGER workflow_events_no_update BEFORE UPDATE ON workflow_events
        BEGIN SELECT RAISE(ABORT, 'workflow events are immutable'); END""",
    """CREATE TRIGGER workflow_events_no_delete BEFORE DELETE ON workflow_events
        BEGIN SELECT RAISE(ABORT, 'workflow events are immutable'); END""",
    """CREATE TRIGGER workflow_events_no_replace BEFORE INSERT ON workflow_events WHEN EXISTS (
        SELECT 1 FROM workflow_events WHERE command_id=NEW.command_id OR (workflow_id=NEW.workflow_id AND revision=NEW.revision))
        BEGIN SELECT RAISE(ABORT, 'workflow events cannot be replaced'); END""",
    """CREATE TRIGGER workflow_commands_no_update BEFORE UPDATE ON workflow_commands
        BEGIN SELECT RAISE(ABORT, 'workflow commands are immutable'); END""",
    """CREATE TRIGGER workflow_commands_no_delete BEFORE DELETE ON workflow_commands
        BEGIN SELECT RAISE(ABORT, 'workflow commands are immutable'); END""",
    """CREATE TRIGGER workflow_commands_no_replace BEFORE INSERT ON workflow_commands WHEN EXISTS (
        SELECT 1 FROM workflow_commands WHERE command_id=NEW.command_id)
        BEGIN SELECT RAISE(ABORT, 'workflow commands cannot be replaced'); END""",
    """CREATE TABLE deliveries (
        delivery_id TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK(revision >= 1),
        status TEXT NOT NULL, snapshot_json TEXT NOT NULL) WITHOUT ROWID""",
    """CREATE TABLE delivery_events (
        delivery_id TEXT NOT NULL REFERENCES deliveries(delivery_id), revision INTEGER NOT NULL CHECK(revision >= 1),
        event_type TEXT NOT NULL, timestamp TEXT NOT NULL, command_id TEXT NOT NULL UNIQUE,
        payload_json TEXT NOT NULL, PRIMARY KEY(delivery_id,revision)) WITHOUT ROWID""",
    """CREATE TABLE delivery_commands (
        command_id TEXT PRIMARY KEY REFERENCES delivery_events(command_id), request_json TEXT NOT NULL,
        snapshot_json TEXT NOT NULL) WITHOUT ROWID""",
    """CREATE TRIGGER delivery_events_no_update BEFORE UPDATE ON delivery_events
        BEGIN SELECT RAISE(ABORT, 'delivery events are immutable'); END""",
    """CREATE TRIGGER delivery_events_no_delete BEFORE DELETE ON delivery_events
        BEGIN SELECT RAISE(ABORT, 'delivery events are immutable'); END""",
    """CREATE TRIGGER delivery_events_no_replace BEFORE INSERT ON delivery_events WHEN EXISTS (
        SELECT 1 FROM delivery_events WHERE command_id=NEW.command_id OR (delivery_id=NEW.delivery_id AND revision=NEW.revision))
        BEGIN SELECT RAISE(ABORT, 'delivery events cannot be replaced'); END""",
    """CREATE TRIGGER delivery_commands_no_update BEFORE UPDATE ON delivery_commands
        BEGIN SELECT RAISE(ABORT, 'delivery commands are immutable'); END""",
    """CREATE TRIGGER delivery_commands_no_delete BEFORE DELETE ON delivery_commands
        BEGIN SELECT RAISE(ABORT, 'delivery commands are immutable'); END""",
    """CREATE TRIGGER delivery_commands_no_replace BEFORE INSERT ON delivery_commands WHEN EXISTS (
        SELECT 1 FROM delivery_commands WHERE command_id=NEW.command_id)
        BEGIN SELECT RAISE(ABORT, 'delivery commands cannot be replaced'); END""",
    """CREATE TABLE provider_health (
        provider TEXT PRIMARY KEY,
        last_probe_ts REAL NOT NULL,
        rate_limit_json TEXT,
        consecutive_failures INT DEFAULT 0,
        last_error TEXT,
        is_quarantined INT DEFAULT 0,
        quarantine_until REAL,
        last_successful_dispatch_ts REAL) WITHOUT ROWID""",
    """CREATE TRIGGER provider_health_no_update BEFORE UPDATE ON provider_health
        BEGIN SELECT RAISE(ABORT, 'provider_health is managed by controller'); END""",
    """CREATE TRIGGER provider_health_no_delete BEFORE DELETE ON provider_health
        BEGIN SELECT RAISE(ABORT, 'provider_health is managed by controller'); END""",
) + IMPROVEMENT_STATEMENTS + (
    """CREATE TABLE profile_trials (
        trial_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL CHECK(schema_version = 1),
        design_json TEXT NOT NULL, design_digest TEXT NOT NULL, catalog_digest TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role = 'builder'), provider TEXT NOT NULL CHECK(provider = 'claude'),
        profile_ids_json TEXT NOT NULL, max_assignments INTEGER NOT NULL CHECK(max_assignments >= 1),
        max_provider_calls INTEGER NOT NULL CHECK(max_provider_calls >= 1),
        max_wall_clock_seconds INTEGER NOT NULL CHECK(max_wall_clock_seconds >= 1),
        max_cost_microunits INTEGER NOT NULL CHECK(max_cost_microunits >= 1),
        outcome_maturation_hours INTEGER NOT NULL CHECK(outcome_maturation_hours >= 1),
        created_at TEXT NOT NULL, command_id TEXT NOT NULL UNIQUE,
        request_json TEXT NOT NULL) WITHOUT ROWID""",
    """CREATE TABLE profile_trial_events (
        trial_id TEXT NOT NULL REFERENCES profile_trials(trial_id), revision INTEGER NOT NULL CHECK(revision >= 1),
        event_type TEXT NOT NULL CHECK(event_type IN ('CANCELLED')),
        timestamp TEXT NOT NULL, command_id TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL,
        PRIMARY KEY(trial_id, revision)) WITHOUT ROWID""",
    """CREATE TABLE profile_trial_assignments (
        assignment_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL CHECK(schema_version = 1),
        trial_id TEXT NOT NULL REFERENCES profile_trials(trial_id), workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id),
        workflow_revision INTEGER NOT NULL CHECK(workflow_revision >= 1), workflow_digest TEXT NOT NULL,
        task_tags_json TEXT NOT NULL, lineage_key TEXT NOT NULL,
        arm TEXT NOT NULL CHECK(arm = 'control' OR arm LIKE 'builder-%'), profile_id TEXT,
        profile_version TEXT, profile_digest TEXT, selection_digest TEXT NOT NULL,
        reserved_provider_calls INTEGER NOT NULL CHECK(reserved_provider_calls >= 1),
        reserved_wall_clock_seconds INTEGER NOT NULL CHECK(reserved_wall_clock_seconds >= 1),
        created_at TEXT NOT NULL, command_id TEXT NOT NULL UNIQUE,
        assignment_json TEXT NOT NULL, assignment_digest TEXT NOT NULL,
        UNIQUE(trial_id, lineage_key, arm)) WITHOUT ROWID""",
    """CREATE TABLE profile_trial_outcomes (
        outcome_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL CHECK(schema_version = 1),
        assignment_id TEXT NOT NULL REFERENCES profile_trial_assignments(assignment_id),
        attempt_number INTEGER NOT NULL CHECK(attempt_number >= 1),
        outcome_status TEXT NOT NULL CHECK(outcome_status IN ('SUCCEEDED','FAILED','UNKNOWN','CANCELLED')),
        provider_calls INTEGER NOT NULL CHECK(provider_calls >= 0),
        wall_clock_seconds INTEGER NOT NULL CHECK(wall_clock_seconds >= 0),
        cost_microunits INTEGER NOT NULL CHECK(cost_microunits >= 0),
        unknown_reason TEXT, observed_at TEXT NOT NULL, payload_json TEXT NOT NULL,
        digest_sha256 TEXT NOT NULL, command_id TEXT NOT NULL UNIQUE, request_json TEXT NOT NULL,
        UNIQUE(assignment_id, attempt_number)) WITHOUT ROWID""",
    """CREATE TRIGGER profile_trials_no_update BEFORE UPDATE ON profile_trials
        BEGIN SELECT RAISE(ABORT, 'profile trial registrations are immutable'); END""",
    """CREATE TRIGGER profile_trials_no_delete BEFORE DELETE ON profile_trials
        BEGIN SELECT RAISE(ABORT, 'profile trial registrations are immutable'); END""",
    """CREATE TRIGGER profile_trials_no_replace BEFORE INSERT ON profile_trials WHEN EXISTS (
        SELECT 1 FROM profile_trials WHERE trial_id=NEW.trial_id OR command_id=NEW.command_id)
        BEGIN SELECT RAISE(ABORT, 'profile trial registrations cannot be replaced'); END""",
    """CREATE TRIGGER profile_trial_events_no_update BEFORE UPDATE ON profile_trial_events
        BEGIN SELECT RAISE(ABORT, 'profile trial events are immutable'); END""",
    """CREATE TRIGGER profile_trial_events_no_delete BEFORE DELETE ON profile_trial_events
        BEGIN SELECT RAISE(ABORT, 'profile trial events are immutable'); END""",
    """CREATE TRIGGER profile_trial_events_no_replace BEFORE INSERT ON profile_trial_events WHEN EXISTS (
        SELECT 1 FROM profile_trial_events WHERE command_id=NEW.command_id OR
            (trial_id=NEW.trial_id AND revision=NEW.revision))
        BEGIN SELECT RAISE(ABORT, 'profile trial events cannot be replaced'); END""",
    """CREATE TRIGGER profile_trial_assignments_no_update BEFORE UPDATE ON profile_trial_assignments
        BEGIN SELECT RAISE(ABORT, 'profile trial assignments are immutable'); END""",
    """CREATE TRIGGER profile_trial_assignments_no_delete BEFORE DELETE ON profile_trial_assignments
        BEGIN SELECT RAISE(ABORT, 'profile trial assignments are immutable'); END""",
    """CREATE TRIGGER profile_trial_assignments_no_replace BEFORE INSERT ON profile_trial_assignments WHEN EXISTS (
        SELECT 1 FROM profile_trial_assignments WHERE assignment_id=NEW.assignment_id OR command_id=NEW.command_id OR
            (trial_id=NEW.trial_id AND lineage_key=NEW.lineage_key AND arm=NEW.arm))
        BEGIN SELECT RAISE(ABORT, 'profile trial assignments cannot be replaced'); END""",
    """CREATE TRIGGER profile_trial_outcomes_no_update BEFORE UPDATE ON profile_trial_outcomes
        BEGIN SELECT RAISE(ABORT, 'profile trial outcomes are immutable'); END""",
    """CREATE TRIGGER profile_trial_outcomes_no_delete BEFORE DELETE ON profile_trial_outcomes
        BEGIN SELECT RAISE(ABORT, 'profile trial outcomes are immutable'); END""",
    """CREATE TRIGGER profile_trial_outcomes_no_replace BEFORE INSERT ON profile_trial_outcomes WHEN EXISTS (
        SELECT 1 FROM profile_trial_outcomes WHERE outcome_id=NEW.outcome_id OR command_id=NEW.command_id OR
            (assignment_id=NEW.assignment_id AND attempt_number=NEW.attempt_number))
        BEGIN SELECT RAISE(ABORT, 'profile trial outcomes cannot be replaced'); END""",
    """CREATE INDEX profile_trial_assignments_trial ON profile_trial_assignments(trial_id, created_at, assignment_id)""",
    """CREATE INDEX profile_trial_outcomes_assignment ON profile_trial_outcomes(assignment_id, attempt_number)""",
)

_EVIDENCE_START = 9
_DISPATCH_START = 18
_WORKFLOW_START = 22
_DELIVERY_START = 31
_MIGRATION_6_TO_7_START = 40
MIGRATION_2_TO_3 = SCHEMA_STATEMENTS[_EVIDENCE_START:_DISPATCH_START]
MIGRATION_3_TO_4 = SCHEMA_STATEMENTS[_DISPATCH_START:_WORKFLOW_START]
MIGRATION_4_TO_5 = SCHEMA_STATEMENTS[_WORKFLOW_START:_DELIVERY_START]
MIGRATION_5_TO_6 = SCHEMA_STATEMENTS[_DELIVERY_START:_MIGRATION_6_TO_7_START]
_MIGRATION_7_TO_8_START = 43
MIGRATION_6_TO_7 = SCHEMA_STATEMENTS[_MIGRATION_6_TO_7_START:_MIGRATION_7_TO_8_START]
_TRIAL_START = _MIGRATION_7_TO_8_START + len(IMPROVEMENT_STATEMENTS)
MIGRATION_7_TO_8 = SCHEMA_STATEMENTS[_MIGRATION_7_TO_8_START:_TRIAL_START]
MIGRATION_8_TO_9 = SCHEMA_STATEMENTS[_TRIAL_START:]


def _check_layout(connection: sqlite3.Connection, statements: tuple[str, ...]) -> None:
    rows = {row[0]: row[1] for row in connection.execute("SELECT name,sql FROM sqlite_master")}
    for statement in statements:
        name = statement.split()[2]
        actual = rows.get(name)
        expected = re.sub(r"\s+", " ", statement.strip()).lower()
        if actual is None or re.sub(r"\s+", " ", actual.strip()).lower() != expected:
            raise StorageError(f"database schema object {name} is missing or differs from the validated layout")


def check_schema(connection: sqlite3.Connection, initialize: bool = False) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version == 0 and initialize:
        tables = connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        if tables:
            raise StorageError("unversioned nonempty database; explicit migration is required")
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    elif version != SCHEMA_VERSION:
        raise StorageError(f"unsupported database schema {version}; expected {SCHEMA_VERSION}; no automatic migration")
    required = {"tasks", "events", "commands", "events_no_update", "events_no_delete",
                "commands_no_update", "commands_no_delete", "events_no_replace", "commands_no_replace",
                "attempts", "attempt_commands", "attempt_evidence", "attempt_commands_no_update",
                "attempt_commands_no_delete", "attempt_commands_no_replace", "attempt_evidence_no_update",
                "attempt_evidence_no_delete", "attempt_evidence_no_replace", "dispatch_claims",
                "dispatch_claims_no_update", "dispatch_claims_no_delete", "dispatch_claims_no_replace"}
    names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    if not required <= names:
        raise StorageError(f"schema-{SCHEMA_VERSION} database is incomplete; restore or explicitly repair it")
    _check_layout(connection, SCHEMA_STATEMENTS)
    for table in ("events", "commands", "attempts", "attempt_commands", "attempt_evidence", "dispatch_claims",
                   "workflows", "workflow_events", "workflow_commands", "deliveries", "delivery_events", "delivery_commands",
                 "improvement_checkpoints", "improvement_publications", "improvement_publication_events",
                 "improvement_cancellations", "improvement_dispositions", "profile_trials",
                 "profile_trial_events", "profile_trial_assignments", "profile_trial_outcomes"):
        try:
            connection.execute(f"SELECT _rowid_ FROM {table} LIMIT 0")
        except sqlite3.OperationalError as exc:
            if "no such column: _rowid_" not in str(exc):
                raise
        else:
            raise StorageError(f"schema-{SCHEMA_VERSION} {table} must be WITHOUT ROWID; legacy storage is unsupported")


def migrate_schema(connection: sqlite3.Connection) -> None:
    """Apply only explicit schema migrations; never rewrite an unknown database."""
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version == 2:
        _check_layout(connection, SCHEMA_STATEMENTS[:_EVIDENCE_START])
        legacy_required = {"tasks", "events", "commands", "events_no_update", "events_no_delete",
                           "commands_no_update", "commands_no_delete", "events_no_replace", "commands_no_replace"}
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        if not legacy_required <= names:
            raise StorageError("schema-2 database is incomplete; migration refused")
        for table in ("events", "commands"):
            try:
                connection.execute(f"SELECT _rowid_ FROM {table} LIMIT 0")
            except sqlite3.OperationalError as exc:
                if "no such column: _rowid_" not in str(exc):
                    raise StorageError("schema-2 database has invalid ledger storage") from exc
            else:
                raise StorageError("schema-2 ledger tables must be WITHOUT ROWID")
        for statement in MIGRATION_2_TO_3:
            connection.execute(statement)
        connection.execute("PRAGMA user_version=3")
        version = 3
    if version == 3:
        _check_layout(connection, SCHEMA_STATEMENTS[:_DISPATCH_START])
        required = {"tasks", "events", "commands", "attempts", "attempt_commands", "attempt_evidence",
                    "attempt_commands_no_update", "attempt_commands_no_delete", "attempt_commands_no_replace",
                    "attempt_evidence_no_update", "attempt_evidence_no_delete", "attempt_evidence_no_replace"}
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        if not required <= names:
            raise StorageError("schema-3 database is incomplete; migration refused")
        for statement in MIGRATION_3_TO_4:
            connection.execute(statement)
        connection.execute("PRAGMA user_version=4")
        version = 4
    if version == 4:
        _check_layout(connection, SCHEMA_STATEMENTS[:_WORKFLOW_START])
        for statement in MIGRATION_4_TO_5:
            connection.execute(statement)
        connection.execute("PRAGMA user_version=5")
        version = 5
    if version == 5:
        _check_layout(connection, SCHEMA_STATEMENTS[:_DELIVERY_START])
        for statement in MIGRATION_5_TO_6:
            connection.execute(statement)
        connection.execute("PRAGMA user_version=6")
        version = 6
    if version == 6:
        _check_layout(connection, SCHEMA_STATEMENTS[:_MIGRATION_6_TO_7_START])
        for statement in MIGRATION_6_TO_7:
            connection.execute(statement)
        connection.execute("PRAGMA user_version=7")
        version = 7
    if version == 7:
        _check_layout(connection, SCHEMA_STATEMENTS[:_MIGRATION_7_TO_8_START])
        for statement in MIGRATION_7_TO_8:
            connection.execute(statement)
        connection.execute("PRAGMA user_version=8")
        version = 8
    if version == 8:
        _check_layout(connection, SCHEMA_STATEMENTS[:_TRIAL_START])
        for statement in MIGRATION_8_TO_9:
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        return
    if version != SCHEMA_VERSION:
        raise StorageError(f"explicit migration supports schema 2 through 8; found schema {version}")


def backup_ledger(connection: sqlite3.Connection, destination: Path) -> Path:
    if not isinstance(destination, Path) or not destination.is_absolute() or ".." in destination.parts:
        raise ValidationError("backup destination must be an absolute contained path without traversal")
    parent = destination.parent.resolve(strict=True)
    if not parent.is_dir() or destination.is_symlink() or destination.exists():
        raise ValidationError("backup requires an existing parent and a new destination file")
    destination = parent / destination.name
    descriptor, temporary = tempfile.mkstemp(prefix=".diamond-backup-", dir=parent)
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(temporary)) as target:
            connection.backup(target)
            check_schema(target)
            if target.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or target.execute("PRAGMA foreign_key_check").fetchall():
                raise StorageError("backup failed ledger integrity validation")
        with open(temporary, "rb") as source:
            os.fsync(source.fileno())
        os.link(temporary, destination)
        return destination
    finally:
        os.unlink(temporary)
