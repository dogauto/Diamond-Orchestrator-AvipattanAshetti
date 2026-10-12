import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from diamond.daily_improve import analyze_ledger
from diamond.domain import Provider, Role, TaskSpec
from diamond.errors import CommandConflictError, ConflictError, StorageError
from diamond.evidence import AttemptStatus, EvidenceRecord, ExecutionAttempt, digest_text
from diamond.improvement_evidence import WINDOW_QUERY, record_disposition
from diamond.schema import SCHEMA_STATEMENTS, SCHEMA_VERSION
from diamond.store import EventStore
from diamond.validation import canonical_json, utc_now
from support import ProjectCase


class ImprovementEvidenceTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.project = self.project.resolve()
        self.ledger = EventStore(self.base.resolve() / "state/ledger.sqlite3")

    def attempt(self, index=1, workspace=None):
        identifier = f"{index:032x}"
        self.ledger.create(TaskSpec(identifier, "evidence fixture", Role.QA, Provider.CLAUDE), f"task:{index}")
        attempt = ExecutionAttempt(f"{index + 100:032x}", identifier, 1, Role.QA, Provider.CLAUDE, sys.executable,
                                   str(workspace or self.project), AttemptStatus.INTENDED, utc_now())
        return self.ledger.begin_attempt(attempt, f"attempt:{index}")

    def record(self, attempt, index=1, payload=None, created=None):
        value = canonical_json(payload or {"execution_status": "nonzero", "stderr": "quota unavailable"})
        record = EvidenceRecord(f"{index + 1000:032x}", attempt.attempt_id, "provider_result", created or utc_now(), value, digest_text(value))
        return self.ledger.record_evidence(record, f"evidence:{index}")

    def test_scopes_exclude_other_workspaces_and_unknown_origin(self):
        self.record(self.attempt())
        self.record(self.attempt(2, self.base.resolve()), 2)
        result = analyze_ledger(self.ledger, project=self.project)
        self.assertEqual(result.evidence_records, 1)
        self.assertEqual(result.read_window["excluded_scope"], 1)
        self.assertEqual(result.candidates[0].known_task_lineages, 1)
        self.assertIn("authentication", result.candidates[0].recommendation)
        self.assertEqual(analyze_ledger(self.ledger).candidates, ())

    def test_corrupted_digest_is_rejected_not_ranked(self):
        record = self.record(self.attempt())
        trigger = next(sql for sql in SCHEMA_STATEMENTS if sql.startswith("CREATE TRIGGER attempt_evidence_no_update"))
        with closing(sqlite3.connect(self.ledger.database)) as connection:
            connection.execute("DROP TRIGGER attempt_evidence_no_update")
            connection.execute("UPDATE attempt_evidence SET digest_sha256=? WHERE evidence_id=?", ("0" * 64, record.evidence_id))
            connection.execute(trigger)
            connection.commit()
        with self.assertRaises(StorageError):
            analyze_ledger(self.ledger, project=self.project)

    def test_window_is_indexed_bounded_and_reports_truncation(self):
        attempt = self.attempt()
        payload = canonical_json({"execution_status": "nonzero", "stderr": "bounded failure"})
        now = utc_now()
        with self.ledger._connection(write=True) as connection:
            for index in range(1200):
                record = EvidenceRecord(f"{index + 1000:032x}", attempt.attempt_id, "provider_result", now, payload, digest_text(payload))
                self.ledger._insert_evidence(connection, record, f"bulk:{index}", canonical_json(record.to_dict()))
            plan = connection.execute("EXPLAIN QUERY PLAN " + WINDOW_QUERY, (1024, "2000", "2100", 101)).fetchall()
        self.assertTrue(any("evidence_window" in row[3] for row in plan))
        self.assertFalse(any("USE TEMP B-TREE" in row[3] for row in plan))
        report = analyze_ledger(self.ledger, project=self.project, max_records=100)
        self.assertEqual(report.evidence_records, 100)
        self.assertTrue(report.read_window["truncated"])
        self.assertEqual(report.candidates[0].evidence_count, 100)
        self.assertEqual(report.candidates[0].occurrences, 1)

    def test_oversize_payload_and_future_evidence_are_not_consumed(self):
        attempt = self.attempt()
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="microseconds").replace("+00:00", "Z")
        self.record(attempt, 1, created=future)
        self.assertEqual(analyze_ledger(self.ledger, project=self.project).evidence_records, 0)
        self.record(attempt, 2, {"execution_status": "nonzero", "stderr": "x" * 4096})
        report = analyze_ledger(self.ledger, project=self.project, max_bytes=1024)
        self.assertTrue(report.read_window["truncated"])
        self.assertEqual(report.evidence_records, 0)

    def test_disposition_is_idempotent_scoped_and_requalified_after_source_change(self):
        record = self.record(self.attempt())
        report = analyze_ledger(self.ledger, project=self.project)
        candidate, = report.candidates
        args = (self.ledger, self.project, candidate.signature, report.read_window["source_digest"],
                "failed_experiment", "previous correction did not help", (record.evidence_id,), "disposition:1")
        receipt = record_disposition(*args)
        self.assertEqual(record_disposition(*args), receipt)
        after = analyze_ledger(self.ledger, project=self.project)
        self.assertEqual(after.candidates, ())
        self.assertEqual(after.historical_candidates[0].adjudication, "operator_failed_experiment")
        with self.assertRaises(CommandConflictError):
            record_disposition(*args[:4], "resolved", *args[5:])
        (self.project / "src").mkdir()
        (self.project / "src/new.py").write_text("new_version = True\n")
        self.assertEqual(len(analyze_ledger(self.ledger, project=self.project).candidates), 1)
        with self.assertRaises(ConflictError):
            record_disposition(*args[:-1], "disposition:2")

    def test_schema7_migration_is_explicit_transactional_and_backup_valid(self):
        self.ledger.database.parent.mkdir()
        with closing(sqlite3.connect(self.ledger.database)) as connection:
            for statement in SCHEMA_STATEMENTS[:43]:
                connection.execute(statement)
            connection.execute("PRAGMA user_version=7")
            connection.commit()
        with self.assertRaises(StorageError):
            analyze_ledger(self.ledger, project=self.project)
        with patch("diamond.schema.MIGRATION_7_TO_8", (*SCHEMA_STATEMENTS[43:], "INVALID MIGRATION")):
            with self.assertRaises(StorageError):
                self.ledger.migrate()
        with closing(sqlite3.connect(self.ledger.database)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 7)
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='improvement_publications'").fetchone())
        self.assertEqual(self.ledger.migrate(), SCHEMA_VERSION)
        record = self.record(self.attempt())
        backup = self.ledger.backup(self.base.resolve() / "backup.sqlite3")
        original = analyze_ledger(self.ledger, project=self.project)
        copied = analyze_ledger(EventStore(backup), project=self.project)
        self.assertEqual(original.candidates, copied.candidates)
        self.assertEqual(copied.candidates[0].evidence_ids, (record.evidence_id,))
