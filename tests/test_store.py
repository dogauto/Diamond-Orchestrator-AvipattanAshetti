import sqlite3
import time
import json
import asyncio
import sys
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from diamond.adapters import Invocation, OutputMode, PermissionMode, build_invocation
from diamond.controller import run_persisted
from diamond.domain import FOUNDATION_STATES, Provider, Role, TaskSnapshot, TaskSpec, TaskStatus
from diamond.evidence import (AttemptStatus, EvidenceRecord, ExecutionAttempt, digest_text,
                               output_evidence)
from diamond.errors import (CommandConflictError, ConflictError, ExecutionGuardError,
                            NotFoundError, StorageError, TransitionError, ValidationError)
from diamond.paths import resolve_paths
from diamond.packets import OutputContract, PacketContext, TaskPacket
from diamond.replay import replay_events
from diamond.skills import SkillRegistry
from diamond.store import EventStore
from diamond.results import parse_result
from diamond.supervisor import ExecutionResult, ExecutionStatus
from diamond.validation import canonical_json, utc_now
from support import OTHER_ID, TASK_ID, ProjectCase


class StoreTests(ProjectCase):
    def setUp(self) -> None:
        super().setUp()
        self.database = self.project / ".diamond" / "diamond.sqlite3"
        self.store = EventStore(self.database)
        self.spec = TaskSpec(TASK_ID, "Durable foundation", Role.BUILDER, Provider.CLAUDE)

    def create(self) -> TaskSnapshot:
        return self.store.create(self.spec, "create:1")

    def test_create_reopen_and_replay_exact_snapshot(self) -> None:
        original = self.create()
        reopened = EventStore(self.database)
        self.assertEqual(original.status, TaskStatus.CREATED)
        self.assertEqual(original.revision, 1)
        self.assertEqual(reopened.get(TASK_ID), original)
        self.assertEqual(reopened.list(), (original,))
        self.assertEqual(reopened.replay(TASK_ID), original)
        event = reopened.events(TASK_ID)[0]
        self.assertEqual((event.revision, event.event_type, event.command_id), (1, "TASK_CREATED", "create:1"))
        self.assertEqual(event.timestamp, original.created_at)
        self.assertIsNone(original.plan_revision)
        self.assertIsNone(original.current_attempt)
        self.assertIsNone(original.spec.acceptance_conditions)

    def test_allowed_block_resume_cancel_and_recorded_reasons(self) -> None:
        self.create()
        blocked = self.store.transition(TASK_ID, TaskStatus.BLOCKED, 1, "block:1", "awaiting owner")
        self.assertEqual(blocked.blocking_reason, "awaiting owner")
        self.assertEqual(blocked.resume_status, TaskStatus.CREATED)
        resumed = self.store.transition(TASK_ID, TaskStatus.CREATED, 2, "resume:1", "owner resolved prerequisite")
        self.assertIsNone(resumed.blocking_reason)
        self.assertIsNone(resumed.resume_status)
        self.assertEqual(self.store.events(TASK_ID)[2].to_dict()["payload"]["reason"], "owner resolved prerequisite")
        cancelled = self.store.transition(TASK_ID, TaskStatus.CANCELLED, 3, "cancel:1")
        self.assertEqual(cancelled.revision, 4)
        self.assertEqual(self.store.replay(TASK_ID), cancelled)
        self.assertEqual([event.revision for event in self.store.events(TASK_ID)], [1, 2, 3, 4])

    def test_blocked_can_cancel_and_created_can_cancel(self) -> None:
        for index, blocked in enumerate((False, True)):
            identifier = str(index + 1) * 32
            spec = replace(self.spec, task_id=identifier)
            self.store.create(spec, f"create:{index}")
            if blocked:
                self.store.transition(identifier, TaskStatus.BLOCKED, 1, f"block:{index}", "waiting")
            result = self.store.transition(identifier, TaskStatus.CANCELLED, 2 if blocked else 1, f"cancel:{index}")
            self.assertEqual(self.store.replay(identifier), result)

    def test_all_rejected_transitions_and_future_execution_guards(self) -> None:
        self.create()
        for current in (TaskStatus.CREATED, TaskStatus.BLOCKED, TaskStatus.CANCELLED):
            if current is TaskStatus.BLOCKED:
                self.store.transition(TASK_ID, current, 1, "block", "pending prerequisite")
            if current is TaskStatus.CANCELLED:
                self.store.transition(TASK_ID, current, 2, "cancel")
            before = self.store.get(TASK_ID)
            for target in TaskStatus:
                allowed = current is TaskStatus.CREATED and target in {TaskStatus.BLOCKED, TaskStatus.CANCELLED}
                allowed |= current is TaskStatus.BLOCKED and target in {TaskStatus.CREATED, TaskStatus.CANCELLED}
                if allowed:
                    continue
                error = ExecutionGuardError if target not in FOUNDATION_STATES else TransitionError
                with self.subTest(current=current, target=target), self.assertRaises(error):
                    self.store.transition(TASK_ID, target, before.revision, f"reject:{current.value}:{target.value}", "reason")
                self.assertEqual(self.store.get(TASK_ID), before)
                self.assertEqual(len(self.store.events(TASK_ID)), before.revision)

    def test_reason_and_revision_validation_leave_ledger_unchanged(self) -> None:
        initial = self.create()
        for reason in (None, "", " "):
            with self.subTest(reason=reason), self.assertRaises(ValidationError):
                self.store.transition(TASK_ID, TaskStatus.BLOCKED, 1, "bad-block", reason)
        self.assertEqual(self.store.get(TASK_ID), initial)
        blocked = self.store.transition(TASK_ID, TaskStatus.BLOCKED, 1, "block", "reason")
        with self.assertRaises(ValidationError):
            self.store.transition(TASK_ID, TaskStatus.CREATED, 2, "bad-resume")
        with self.assertRaises(ConflictError):
            self.store.transition(TASK_ID, TaskStatus.CANCELLED, 1, "stale")
        self.assertEqual(self.store.get(TASK_ID), blocked)
        self.assertEqual(len(self.store.events(TASK_ID)), 2)

    def test_real_sql_abort_rolls_back_projection_and_event_after_reopen(self) -> None:
        initial = self.create()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("""CREATE TRIGGER reject_transition BEFORE INSERT ON events
                WHEN NEW.revision > 1 BEGIN SELECT RAISE(ABORT, 'injected event failure'); END""")
        with self.assertRaises(StorageError):
            self.store.transition(TASK_ID, TaskStatus.BLOCKED, 1, "rejected", "reason")
        reopened = EventStore(self.database)
        self.assertEqual(reopened.get(TASK_ID), initial)
        self.assertEqual(reopened.replay(TASK_ID), initial)
        self.assertEqual(len(reopened.events(TASK_ID)), 1)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM commands").fetchone()[0], 1)
            connection.execute("DROP TRIGGER reject_transition")
        self.assertEqual(reopened.transition(TASK_ID, TaskStatus.CANCELLED, 1, "rejected").revision, 2)

    def test_append_only_sql_protection(self) -> None:
        self.create()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            for statement in ("UPDATE events SET payload_json='{}'", "DELETE FROM events",
                              "UPDATE commands SET request_json='{}'", "DELETE FROM commands",
                              "INSERT OR REPLACE INTO events SELECT * FROM events",
                              "INSERT OR REPLACE INTO commands SELECT * FROM commands"):
                with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(statement)
        self.assertEqual(self.store.replay(TASK_ID), self.store.get(TASK_ID))

    def test_creation_sql_abort_leaves_no_task_event_or_command(self) -> None:
        preserved = self.store.create(replace(self.spec, task_id=OTHER_ID), "preserved")
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(f"""CREATE TRIGGER reject_creation BEFORE INSERT ON events
                WHEN NEW.task_id='{TASK_ID}' BEGIN SELECT RAISE(ABORT, 'injected creation failure'); END""")
        with self.assertRaises(StorageError):
            self.create()
        reopened = EventStore(self.database)
        self.assertEqual(reopened.list(), (preserved,))
        with self.assertRaises(NotFoundError):
            reopened.get(TASK_ID)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM commands").fetchone()[0], 1)

    def test_busy_timeout_is_finite_and_does_not_mutate(self) -> None:
        initial = self.create()
        with closing(sqlite3.connect(self.database, isolation_level=None)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            start = time.monotonic()
            with self.assertRaises(StorageError):
                EventStore(self.database, busy_timeout_ms=50).transition(TASK_ID, TaskStatus.CANCELLED, 1, "busy")
            self.assertLess(time.monotonic() - start, 2)
        self.assertEqual(EventStore(self.database).get(TASK_ID), initial)
        self.assertEqual(len(self.store.events(TASK_ID)), 1)

    def test_symlink_changed_after_resolution_is_rejected_before_write(self) -> None:
        paths = resolve_paths(str(self.project))
        store = EventStore(paths.database)
        outside = self.base / "outside"
        outside.mkdir()
        (self.project / ".diamond").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValidationError):
            store.create(self.spec, "changed-path")
        self.assertFalse((outside / "diamond.sqlite3").exists())

    def test_command_dedup_returns_original_even_after_further_mutations(self) -> None:
        original = self.create()
        blocked = self.store.transition(TASK_ID, TaskStatus.BLOCKED, 1, "block", "waiting")
        self.store.transition(TASK_ID, TaskStatus.CREATED, 2, "resume", "resolved")
        self.assertEqual(self.store.create(self.spec, "create:1"), original)
        self.assertEqual(self.store.transition(TASK_ID, TaskStatus.BLOCKED, 1, "block", "waiting"), blocked)
        self.assertEqual(self.store.get(TASK_ID).revision, 3)
        self.assertEqual(len(self.store.events(TASK_ID)), 3)
        for operation in (
            lambda: self.store.create(replace(self.spec, objective="different"), "create:1"),
            lambda: self.store.transition(TASK_ID, TaskStatus.BLOCKED, 1, "block", "different"),
            lambda: self.store.transition(TASK_ID, TaskStatus.BLOCKED, 3, "block", "waiting"),
        ):
            with self.assertRaises(CommandConflictError):
                operation()
        with self.assertRaises(ConflictError):
            self.store.create(self.spec, "new-create")

    def test_missing_reads_and_invalid_input_create_no_state(self) -> None:
        self.assertEqual(self.store.list(), ())
        for operation in (self.store.get, self.store.events, self.store.replay):
            with self.assertRaises(NotFoundError):
                operation(TASK_ID)
            with self.assertRaises(ValidationError):
                operation("../bad")
        with self.assertRaises(ValidationError):
            self.store.create(self.spec, "../bad")
        with self.assertRaises(ValidationError):
            self.store.transition("bad", TaskStatus.CANCELLED, 1, "cancel")
        self.assertFalse(self.database.parent.exists())

    def test_unknown_unversioned_and_corrupt_databases_visible(self) -> None:
        self.database.parent.mkdir()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("PRAGMA user_version=999")
        for operation in (self.store.list, lambda: self.create()):
            with self.assertRaises(StorageError):
                operation()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 999)
            connection.execute("PRAGMA user_version=0")
            connection.execute("CREATE TABLE unrelated(x)")
        with self.assertRaises(StorageError):
            self.create()
        self.database.write_bytes(b"not a sqlite database")
        with self.assertRaises(StorageError):
            self.store.list()

    def test_attempt_lifecycle_is_cas_protected_and_evidence_is_immutable(self) -> None:
        self.create()
        created = utc_now()
        attempt = ExecutionAttempt("a" * 32, TASK_ID, 1, Role.BUILDER, Provider.CLAUDE,
                                   "claude", str(self.project), AttemptStatus.INTENDED, created)
        self.assertEqual(self.store.begin_attempt(attempt, "attempt-intent"), attempt)
        self.assertEqual(self.store.begin_attempt(attempt, "attempt-intent"), attempt)
        started = replace(attempt, status=AttemptStatus.STARTED, started_at=utc_now(), revision=2)
        self.assertEqual(self.store.update_attempt(started, 1, "attempt-start"), started)
        completed = replace(started, status=AttemptStatus.SUCCEEDED, completed_at=utc_now(),
                            provider_session_id="session-1", reported_model="model-1", returncode=0,
                            result_status="valid", stdout_bytes=4, stdout_sha256=digest_text("pass"), revision=3)
        self.assertEqual(self.store.update_attempt(completed, 2, "attempt-complete"), completed)
        with self.assertRaises(ConflictError):
            self.store.update_attempt(replace(completed, detail="stale", revision=3), 2, "attempt-stale")
        payload = {"marker": "pass", "stdout": "<REDACTED>"}
        payload_json = canonical_json(payload)
        evidence = EvidenceRecord("e" * 32, attempt.attempt_id, "provider_result", utc_now(),
                                  payload_json, digest_text(payload_json))
        self.assertEqual(self.store.record_evidence(evidence, "evidence-record"), evidence)
        self.assertEqual(self.store.record_evidence(evidence, "evidence-record"), evidence)
        self.assertEqual(self.store.attempts(TASK_ID), (completed,))
        self.assertEqual(self.store.evidence(TASK_ID), (evidence,))
        with self.assertRaises(sqlite3.IntegrityError):
            with closing(sqlite3.connect(self.database)) as connection, connection:
                connection.execute("UPDATE attempt_evidence SET kind='forged'")
        with self.assertRaises(sqlite3.IntegrityError):
            with closing(sqlite3.connect(self.database)) as connection, connection:
                connection.execute("DELETE FROM attempt_evidence")

    def test_provider_evidence_redacts_output_and_argv_before_persistence(self) -> None:
        self.create()
        attempt = ExecutionAttempt("b" * 32, TASK_ID, 1, Role.BUILDER, Provider.OMNIRUSH,
                                   "omnirush", str(self.project), AttemptStatus.INTENDED, utc_now())
        invocation = build_invocation(Provider.OMNIRUSH, "omnirush", "token=secret-value")
        execution = ExecutionResult(ExecutionStatus.SUCCEEDED, 0, "OK token=secret-value", "")
        result = parse_result(invocation, execution)
        evidence = output_evidence(attempt, invocation, execution, result, utc_now())
        payload = json.loads(evidence.payload_json)
        self.assertIn("<REDACTED>", payload["argv"][-1])
        self.assertNotIn("secret-value", evidence.payload_json)

    def test_controller_persists_intent_start_completion_and_result(self) -> None:
        executable = Path(sys.executable).name
        packet_spec = replace(self.spec, provider=Provider.OMNIRUSH, allowed_tools=(executable,))
        self.store.create(packet_spec, "create:1")
        packet = TaskPacket.from_task_spec(packet_spec, "c" * 32, self.project, 1,
                                           "1" * 64, "2" * 64, "3" * 64,
                           SkillRegistry(()), (), OutputContract("text", (), 4096))
        context = PacketContext(1, "1" * 64, "2" * 64, "3" * 64)
        attempt = ExecutionAttempt("c" * 32, TASK_ID, 1, Role.BUILDER, Provider.OMNIRUSH,
                                   sys.executable, str(self.project.resolve()), AttemptStatus.INTENDED, utc_now())
        invocation = Invocation(Provider.OMNIRUSH,
                                (sys.executable, "-c", "print('controller-marker')"),
                                OutputMode.TEXT, None, False, PermissionMode.DEFAULT)
        completed, result, execution, evidence = asyncio.run(
            run_persisted(self.store, packet, attempt, invocation, self.project, "test-owner", "recorded",
                          SkillRegistry(()), context))
        self.assertEqual(completed.status, AttemptStatus.SUCCEEDED)
        self.assertEqual(result.status.value, "valid")
        self.assertEqual(execution.returncode, 0)
        self.assertEqual(self.store.attempts(TASK_ID), (completed,))
        self.assertIn(evidence, self.store.evidence(TASK_ID))
        self.assertEqual({record.kind for record in self.store.evidence(TASK_ID)},
                         {"launch_intent", "process_started", "provider_result"})
        self.assertIn("controller-marker", evidence.payload_json)
        with self.assertRaises(ConflictError):
            asyncio.run(run_persisted(self.store, packet, attempt, invocation, self.project, "test-owner", "recorded",
                                      SkillRegistry(()), context))

    def test_dispatch_claim_is_single_winner_for_task_revision(self) -> None:
        executable = Path(sys.executable).name
        packet_spec = replace(self.spec, provider=Provider.OMNIRUSH, allowed_tools=(executable,))
        self.store.create(packet_spec, "create:1")
        first = TaskPacket.from_task_spec(packet_spec, "d" * 32, self.project, 1,
                                          "1" * 64, "2" * 64, "3" * 64,
                                          SkillRegistry(()), (), OutputContract("text", (), 4096))
        second = replace(first, attempt_id="e" * 32)
        first_attempt = ExecutionAttempt("d" * 32, TASK_ID, 1, Role.BUILDER, Provider.OMNIRUSH,
                                        sys.executable, str(self.project.resolve()), AttemptStatus.INTENDED, utc_now())
        second_attempt = replace(first_attempt, attempt_id="e" * 32)
        claim, intent = self.store.reserve_dispatch(first, first_attempt, "owner-a", "claim-one")
        self.assertEqual((claim.attempt_id, intent.attempt_id), ("d" * 32, "d" * 32))
        with self.assertRaises(ConflictError):
            self.store.reserve_dispatch(second, second_attempt, "owner-b", "claim-two")
        self.assertEqual(self.store.dispatch_claims(TASK_ID), (claim,))

    def test_default_symlink_escape_and_explicit_state_validation(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        (self.project / ".diamond").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValidationError):
            resolve_paths(str(self.project))
        paths = resolve_paths(str(self.project), state_dir=str(outside))
        self.assertEqual(paths.database, outside.resolve() / "diamond.sqlite3")
        with self.assertRaises(ValidationError):
            resolve_paths(str(self.project), state_dir="../escape")
        (outside / "diamond.sqlite3").symlink_to(self.base / "outside-database")
        with self.assertRaises(ValidationError):
            resolve_paths(str(self.project), state_dir=str(outside))

    def test_replay_rejects_revision_gaps_and_bad_history(self) -> None:
        self.create()
        self.store.transition(TASK_ID, TaskStatus.BLOCKED, 1, "block", "waiting")
        events = self.store.events(TASK_ID)
        with self.assertRaises(StorageError):
            replay_events((events[0], replace(events[1], revision=3)))
        with self.assertRaises(StorageError):
            replay_events((replace(events[0], task_id=OTHER_ID),))
