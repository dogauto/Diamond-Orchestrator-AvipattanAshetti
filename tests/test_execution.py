import asyncio
import hashlib
import json
import os
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from diamond.adapters import Invocation, OutputMode, PermissionMode, build_invocation
from diamond.controller import run_persisted
from diamond.domain import Provider, Role, TaskSpec, TaskStatus
from diamond.errors import CommandConflictError, ConflictError, ExecutionGuardError, StorageError, ValidationError
from diamond.evidence import AttemptStatus, EvidenceRecord, ExecutionAttempt, digest_text, output_evidence
from diamond.packets import ArtifactRef, OutputContract, PacketContext, TaskPacket
from diamond.processes import ProcessIdentity, observe
from diamond.results import parse_result
from diamond.skills import SkillRegistry
from diamond.store import EventStore
from diamond.supervisor import CancelledExecution, ExecutionResult, ExecutionStatus
from diamond.validation import canonical_json, utc_now
from support import TASK_ID, OTHER_ID, ProjectCase


class ExecutionTests(ProjectCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = EventStore(self.project / ".diamond" / "diamond.sqlite3")
        self.registry = SkillRegistry(())
        self.context = PacketContext(1, "1" * 64, "2" * 64, "3" * 64)

    def fixture(self, identifier: str = TASK_ID, owned_paths: tuple[str, ...] = ()):
        spec = TaskSpec(identifier, "bounded fixture", Role.QA, Provider.CLAUDE,
                        owned_paths=owned_paths, allowed_tools=(sys.executable,), timeout_seconds=3)
        self.store.create(spec, f"create:{identifier}")
        packet = TaskPacket.from_task_spec(spec, identifier[::-1], self.project, 1,
                                           self.context.plan_digest, self.context.base_digest, self.context.config_digest,
                                           self.registry, (), OutputContract("text", (), 4096))
        attempt = ExecutionAttempt(packet.attempt_id, identifier, 1, spec.role, spec.provider, sys.executable,
                                   packet.workspace_root, AttemptStatus.INTENDED, utc_now())
        invocation = Invocation(spec.provider, (sys.executable, "-c", "print('fixture-ok')"), OutputMode.TEXT, None,
                                False, PermissionMode.DEFAULT)
        return packet, attempt, invocation

    async def execute(self, packet, attempt, invocation, **kwargs):
        return await run_persisted(self.store, packet, attempt, invocation, self.project,
                                    "test-controller", f"run:{packet.task_id}", self.registry, self.context, **kwargs)

    def started(self, packet, attempt):
        claim, _ = self.store.reserve_dispatch(packet, attempt, "test-controller", f"claim:{packet.task_id}")
        return claim, self.store.acquire_start(claim, f"start:{packet.task_id}")

    def terminal(self, started, invocation):
        execution = ExecutionResult(ExecutionStatus.SUCCEEDED, 0, "fixture-ok", "")
        result = parse_result(invocation, execution)
        completed = replace(started, status=AttemptStatus.SUCCEEDED, completed_at=utc_now(), returncode=0,
                            result_status="valid", stdout_sha256=digest_text("fixture-ok"), stderr_sha256=digest_text(""),
                            stdout_bytes=10, revision=started.revision + 1)
        evidence = output_evidence(completed, invocation, execution, result, utc_now())
        return completed, evidence

    def test_replayed_launch_acquisition_never_grants_launch_permission(self) -> None:
        packet, attempt, _ = self.fixture()
        claim, _ = self.started(packet, attempt)
        with self.assertRaises(ConflictError):
            self.store.acquire_start(claim, f"start:{packet.task_id}")
        with self.assertRaises(ConflictError):
            self.store.acquire_start(claim, "new-start-command")

    def test_cancelled_or_blocked_tasks_and_cancelled_reservations_cannot_start(self) -> None:
        packet, attempt, _ = self.fixture()
        claim, _ = self.store.reserve_dispatch(packet, attempt, "owner", "claim")
        self.store.transition(TASK_ID, TaskStatus.CANCELLED, 1, "cancel")
        with self.assertRaises(ConflictError):
            self.store.acquire_start(claim, "launch")
        second, intent, _ = self.fixture(OTHER_ID)
        self.store.transition(OTHER_ID, TaskStatus.BLOCKED, 1, "block", "waiting")
        with self.assertRaises(ExecutionGuardError):
            self.store.reserve_dispatch(replace(second, task_revision=2), replace(intent, task_revision=2), "owner", "blocked")

    def test_capacity_and_ambiguous_prior_revision_block_new_dispatch(self) -> None:
        packet, attempt, _ = self.fixture()
        claim, _ = self.store.reserve_dispatch(packet, attempt, "owner", "claim", max_workers=1)
        second, intent, _ = self.fixture(OTHER_ID)
        with self.assertRaisesRegex(ConflictError, "capacity"):
            self.store.reserve_dispatch(second, intent, "owner", "second", max_workers=1)
        self.assertEqual(self.store.reserve_dispatch(packet, attempt, "owner", "claim", max_workers=1)[0], claim)
        self.store.reconcile_attempt(attempt.attempt_id, "reconcile", "unknown launch after restart")
        self.store.transition(TASK_ID, TaskStatus.BLOCKED, 1, "block", "waiting")
        self.store.transition(TASK_ID, TaskStatus.CREATED, 2, "resume", "resolved")
        refreshed = replace(packet, task_revision=3, attempt_id="d" * 32)
        with self.assertRaisesRegex(ConflictError, "unresolved"):
            self.store.reserve_dispatch(refreshed, replace(attempt, task_revision=3, attempt_id="d" * 32), "owner", "third")

    def test_claim_insertion_failure_rolls_back_intent_and_command(self) -> None:
        packet, attempt, _ = self.fixture()
        with closing(sqlite3.connect(self.store.database)) as connection, connection:
            connection.execute("CREATE TRIGGER reject_claim BEFORE INSERT ON dispatch_claims BEGIN SELECT RAISE(ABORT,'fault'); END")
        with self.assertRaises(StorageError):
            self.store.reserve_dispatch(packet, attempt, "owner", "claim")
        reopened = EventStore(self.store.database)
        self.assertEqual(reopened.attempts(TASK_ID), ())
        self.assertEqual(reopened.dispatch_claims(TASK_ID), ())

    def test_completion_and_evidence_roll_back_together_then_retry(self) -> None:
        packet, attempt, invocation = self.fixture()
        _, started = self.started(packet, attempt)
        completed, evidence = self.terminal(started, invocation)
        with closing(sqlite3.connect(self.store.database)) as connection, connection:
            connection.execute("CREATE TRIGGER reject_evidence BEFORE INSERT ON attempt_evidence BEGIN SELECT RAISE(ABORT,'fault'); END")
        with self.assertRaises(StorageError):
            self.store.complete_attempt(completed, 2, evidence, "complete")
        reopened = EventStore(self.store.database)
        self.assertEqual(reopened.get_attempt(attempt.attempt_id), started)
        self.assertEqual(reopened.evidence(TASK_ID), ())
        with closing(sqlite3.connect(self.store.database)) as connection, connection:
            connection.execute("DROP TRIGGER reject_evidence")
        self.assertEqual(reopened.complete_attempt(completed, 2, evidence, "complete"), completed)
        self.assertEqual(reopened.complete_attempt(completed, 2, evidence, "complete"), completed)
        self.assertEqual(reopened.evidence(TASK_ID), (evidence,))
        self.assertEqual(reopened.replay_attempt(attempt.attempt_id), completed)

    def test_backup_reopens_with_matching_attempt_claim_and_evidence(self) -> None:
        packet, attempt, invocation = self.fixture()
        _, started = self.started(packet, attempt)
        completed, evidence = self.terminal(started, invocation)
        self.store.complete_attempt(completed, 2, evidence, "complete")
        destination = (self.project / "verified.sqlite3").resolve()
        self.assertEqual(self.store.backup(destination), destination)
        restored = EventStore(destination)
        self.assertEqual(restored.replay_attempt(attempt.attempt_id), completed)
        self.assertEqual(restored.dispatch_claims(TASK_ID), self.store.dispatch_claims(TASK_ID))
        self.assertEqual(restored.evidence(TASK_ID), (evidence,))
        before = destination.read_bytes()
        with self.assertRaises(ValidationError):
            self.store.backup(destination)
        self.assertEqual(destination.read_bytes(), before)

    def test_restart_reconciliation_is_deduplicated_and_never_launches(self) -> None:
        packet, attempt, _ = self.fixture()
        self.started(packet, attempt)
        reopened = EventStore(self.store.database)
        with patch("diamond.controller.run", new=AsyncMock()) as runner:
            ambiguous = reopened.reconcile_attempt(attempt.attempt_id, "recover", "crash with unknown external state")
            self.assertEqual(ambiguous.status, AttemptStatus.AMBIGUOUS)
            self.assertEqual(reopened.reconcile_attempt(attempt.attempt_id, "recover", "crash with unknown external state"), ambiguous)
            with self.assertRaises(CommandConflictError):
                reopened.reconcile_attempt(attempt.attempt_id, "recover", "different reason")
            runner.assert_not_called()
        self.assertFalse(reopened.evidence(TASK_ID)[0].to_dict()["payload"]["relaunch_allowed"])

    def test_late_result_after_reconciliation_is_retained_and_finalized(self) -> None:
        packet, attempt, invocation = self.fixture()

        async def reconcile_during_run(*args, **kwargs):
            self.store.reconcile_attempt(attempt.attempt_id, "recover-live", "restart observed unproven process")
            return ExecutionResult(ExecutionStatus.SUCCEEDED, 0, "fixture-ok", "")

        with patch("diamond.controller.run", new=reconcile_during_run):
            completed, _, _, evidence = asyncio.run(self.execute(packet, attempt, invocation))
        self.assertEqual(completed.status, AttemptStatus.SUCCEEDED)
        self.assertEqual(completed.revision, 4)
        self.assertIn(evidence, self.store.evidence(TASK_ID))

    def test_cancel_during_run_keeps_attempt_cancelled_with_evidence(self) -> None:
        packet, attempt, invocation = self.fixture()
        invocation = replace(invocation, argv=(sys.executable, "-c", "import time; time.sleep(30)"))

        async def cancel():
            worker = asyncio.create_task(self.execute(packet, attempt, invocation))
            await asyncio.sleep(0.15)
            worker.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await worker

        asyncio.run(cancel())
        self.assertEqual(self.store.get_attempt(attempt.attempt_id).status, AttemptStatus.CANCELLED)
        self.assertIn("provider_result", {item.kind for item in self.store.evidence(TASK_ID)})

    def test_recovered_cancellation_finalizes_and_recovered_cleanup_failure_stays_blocked(self) -> None:
        for identifier, execution_status in ((TASK_ID, ExecutionStatus.CANCELLED), (OTHER_ID, ExecutionStatus.CLEANUP_FAILED)):
            packet, attempt, invocation = self.fixture(identifier)

            async def complete_after_recovery(*args, **kwargs):
                self.store.reconcile_attempt(attempt.attempt_id, f"recover:{identifier}", "restart while supervised work was unproven")
                result = ExecutionResult(execution_status, -15, "", "", "interrupted",
                                         cleanup_complete=execution_status is ExecutionStatus.CANCELLED)
                if execution_status is ExecutionStatus.CANCELLED:
                    raise CancelledExecution(result)
                return result

            with patch("diamond.controller.run", new=complete_after_recovery):
                if execution_status is ExecutionStatus.CANCELLED:
                    with self.assertRaises(asyncio.CancelledError):
                        asyncio.run(self.execute(packet, attempt, invocation))
                else:
                    asyncio.run(self.execute(packet, attempt, invocation))
            expected = AttemptStatus.CANCELLED if execution_status is ExecutionStatus.CANCELLED else AttemptStatus.AMBIGUOUS
            self.assertEqual(self.store.get_attempt(attempt.attempt_id).status, expected)
            self.assertIn("provider_result", {record.kind for record in self.store.evidence(identifier)})

    def test_bad_digest_and_secret_bearing_evidence_are_rejected(self) -> None:
        payload = canonical_json({"message": "ok"})
        with self.assertRaisesRegex(ValidationError, "does not match"):
            EvidenceRecord("e" * 32, TASK_ID, "fixture", utc_now(), payload, "0" * 64)
        payload = canonical_json({"token": "synthetic-credential"})
        with self.assertRaisesRegex(ValidationError, "unredacted"):
            EvidenceRecord("e" * 32, TASK_ID, "fixture", utc_now(), payload, digest_text(payload))

    def test_identity_probe_does_not_signal_reused_pid(self) -> None:
        expected = ProcessIdentity(123, 123, "old process")
        with patch("diamond.processes.identify", return_value=ProcessIdentity(123, 999, "different process")), \
             patch("os.killpg") as signal:
            observation = observe(expected)
            self.assertEqual(observation["classification"], "PID_REUSED")
            self.assertFalse(observation["relaunch_allowed"])
            signal.assert_not_called()

    def test_same_basename_elsewhere_is_not_an_authorized_executable(self) -> None:
        packet, _, invocation = self.fixture()
        spoof = self.fake_executable(Path(sys.executable).name, "print('spoof')")
        with self.assertRaisesRegex(ValidationError, "not allowed"):
            packet.authorize_invocation(replace(invocation, argv=(str(spoof),)), self.project)

    def test_missing_artifact_and_wrong_output_contract_fail_with_evidence(self) -> None:
        packet, attempt, invocation = self.fixture(owned_paths=("missing.py",))
        artifact = ArtifactRef("missing", "missing.py", "0" * 64, 0, "source")
        completed, _, _, evidence = asyncio.run(self.execute(packet, attempt, invocation, output_artifacts=(artifact,)))
        self.assertEqual(completed.status, AttemptStatus.FAILED)
        self.assertFalse(evidence.to_dict()["payload"]["artifacts_verified"])

    def test_stale_context_never_launches_or_claims(self) -> None:
        packet, attempt, invocation = self.fixture()
        with patch("diamond.controller.run", new=AsyncMock()) as runner:
            with self.assertRaisesRegex(ValidationError, "stale"):
                asyncio.run(self.execute(replace(packet, config_digest="4" * 64), attempt, invocation))
            runner.assert_not_called()
        self.assertEqual(self.store.dispatch_claims(TASK_ID), ())

    def test_large_redacted_binary_output_remains_bounded_and_hashes_raw_bytes(self) -> None:
        packet, attempt, _ = self.fixture()
        execution = ExecutionResult(ExecutionStatus.SUCCEEDED, 0, "x" * 300000 + "\x00 token=synthetic-secret", "")
        invocation = Invocation(Provider.CLAUDE, (sys.executable,), OutputMode.TEXT, None)
        result = parse_result(invocation, replace(execution, stdout="response"))
        evidence = output_evidence(attempt, invocation, execution, result, utc_now())
        payload = evidence.to_dict()["payload"]
        self.assertTrue(payload["stdout_truncated"])
        self.assertEqual(payload["stdout_sha256"], digest_text(execution.stdout))
        self.assertEqual(payload["stdout_saved_sha256"], digest_text(payload["stdout"]))
        self.assertNotIn("synthetic-secret", evidence.payload_json)

    def test_host_sandbox_grants_only_owned_workspace_file(self) -> None:
        packet, attempt, invocation = self.fixture(owned_paths=("owned.txt",))
        body = "from pathlib import Path; Path('owned.txt').write_text('ok');\ntry: Path('outside.txt').write_text('bad')\nexcept PermissionError: print('blocked')"
        completed, _, _, _ = asyncio.run(self.execute(packet, attempt, replace(invocation, argv=(sys.executable, "-c", body))))
        self.assertEqual(completed.status, AttemptStatus.SUCCEEDED)
        self.assertEqual((self.project / "owned.txt").read_text(), "ok")
        self.assertFalse((self.project / "outside.txt").exists())
