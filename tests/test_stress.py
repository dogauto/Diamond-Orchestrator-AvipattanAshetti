import asyncio
import hashlib
import json
import multiprocessing
import os
import sqlite3
import sys
import time
from collections import Counter
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from diamond.adapters import Invocation, OutputMode
from diamond.controller import run_persisted
from diamond.delivery_store import DeliveryStore
from diamond.domain import Provider, Role, TaskSpec, TaskStatus
from diamond.errors import ConflictError, DiamondError, StorageError
from diamond.evidence import AttemptStatus, EvidenceRecord, ExecutionAttempt, digest_text, output_evidence
from diamond.packets import OutputContract, PacketContext, TaskPacket
from diamond.skills import SkillRegistry
from diamond.results import parse_result
from diamond.store import EventStore
from diamond.supervisor import ExecutionResult, ExecutionStatus, run
from diamond.validation import canonical_json, utc_now
from diamond.workflow_contracts import TestCommand, WorkflowSpec
from diamond.workflow_store import WorkflowStore
from diamond.workspace import workspace_manifest
from support import ProjectCase, TASK_ID
import test_delivery as delivery_fixtures


WORKERS = 12


def _contend(database, barrier, queue, operation, args):
    started = time.monotonic()
    try:
        store = EventStore(Path(database), busy_timeout_ms=15000)
        barrier.wait(timeout=30)
        if operation == "create":
            value = store.create(*args).revision
        elif operation == "transition":
            value = store.transition(*args).revision
        elif operation == "reserve":
            value = store.reserve_dispatch(*args)[0].attempt_id
        elif operation == "start":
            value = store.acquire_start(*args).revision
        elif operation == "workflow":
            value = WorkflowStore(store).start(*args).revision
        elif operation == "delivery":
            value = DeliveryStore(store).start(*args).revision
        elif operation == "evidence":
            value = store.record_evidence(*args).digest_sha256
        elif operation == "complete":
            value = store.complete_attempt(*args).revision
        elif operation == "reconcile":
            value = store.reconcile_attempt(*args).revision
        elif operation == "bulk":
            worker, count = args
            for index in range(count):
                identifier = f"{1000 + worker * count + index:032x}"
                spec = TaskSpec(identifier, "stress lifecycle", Role.QA, Provider.CLAUDE)
                store.create(spec, f"create:{identifier}")
                store.create(spec, f"create:{identifier}")
                for revision, status in enumerate((TaskStatus.BLOCKED, TaskStatus.CREATED, TaskStatus.CANCELLED), 1):
                    command = (identifier, status, revision, f"step:{identifier}:{revision}", "stress")
                    assert store.transition(*command) == store.transition(*command)
                assert store.replay(identifier) == store.get(identifier)
            value = count
        else:
            raise AssertionError(operation)
        queue.put(("ok", value, time.monotonic() - started))
    except DiamondError as exc:
        queue.put((exc.code, str(exc), time.monotonic() - started))
    except BaseException as exc:
        queue.put(("unexpected", repr(exc), time.monotonic() - started))


def _uncommitted_writer(database, queue):
    store = EventStore(Path(database))
    original = store._record

    def pause_before_commit(*args):
        original(*args)
        queue.put("written-uncommitted")
        time.sleep(60)

    store._record = pause_before_commit
    store.create(TaskSpec(TASK_ID, "crash before commit", Role.QA, Provider.CLAUDE), "crash")


class StressTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.store = EventStore(self.base.resolve() / "state" / "ledger.sqlite3")
        self.root = self.project.resolve()
        self.registry = SkillRegistry(())
        self.context = PacketContext(1, "1" * 64, "2" * 64, "3" * 64)
        self.started_at = time.monotonic()
        self.addCleanup(lambda: print(f"STRESS {self._testMethodName}: {time.monotonic() - self.started_at:.3f}s", flush=True))

    def compete(self, operations, database=None):
        context = multiprocessing.get_context("spawn")
        barrier, queue = context.Barrier(len(operations)), context.Queue()
        processes = [context.Process(target=_contend, args=(str(database or self.store.database), barrier, queue, op, args))
                     for op, args in operations]
        try:
            for process in processes:
                process.start()
            results = [queue.get(timeout=60) for _ in processes]
            for process in processes:
                process.join(timeout=5)
                self.assertFalse(process.is_alive(), "worker did not exit")
                self.assertEqual(process.exitcode, 0)
            self.assertNotIn("unexpected", [row[0] for row in results], results)
            print(f"STRESS contention={dict(Counter(row[0] for row in results))} max_worker={max(row[2] for row in results):.3f}s", flush=True)
            return results
        finally:
            for process in processes:
                if process.is_alive():
                    process.kill()
                    process.join(timeout=5)
            queue.close()
            queue.join_thread()

    def fixture(self, index=1, owned=()):
        identifier = f"{index:032x}"
        spec = TaskSpec(identifier, "stress local execution", Role.QA, Provider.CLAUDE,
                        owned_paths=owned, allowed_tools=(sys.executable,), timeout_seconds=5)
        self.store.create(spec, f"create:{identifier}")
        packet = TaskPacket.from_task_spec(spec, f"{index + 10000:032x}", self.root, 1,
            self.context.plan_digest, self.context.base_digest, self.context.config_digest,
            self.registry, (), OutputContract("text", (), 4096))
        attempt = ExecutionAttempt(packet.attempt_id, identifier, 1, spec.role, spec.provider, sys.executable,
                                   str(self.root), AttemptStatus.INTENDED, utc_now())
        return packet, attempt

    def workflow(self, index=1, owned=("value.py",)):
        spec = WorkflowSpec(f"{index + 20000:032x}", "stress workflow", str(self.root), owned,
            (TestCommand("local check", (sys.executable, "-B", "-c", "assert True")),),
            Provider.CLAUDE, Provider.CODEX, "3" * 64, workspace_manifest(self.root), max_workers=64)
        return WorkflowStore(self.store).create(spec, f"workflow:{index}")

    def test_twelve_duplicate_creates_and_cas_writers(self):
        spec = TaskSpec(TASK_ID, "cold ledger contention", Role.QA, Provider.CLAUDE)
        created = self.compete([("create", (spec, "same-create"))] * WORKERS)
        self.assertEqual([row[0] for row in created], ["ok"] * WORKERS)
        changed = self.compete([("transition", (TASK_ID, TaskStatus.BLOCKED, 1, f"block:{i}", "stress"))
                                for i in range(WORKERS)])
        self.assertEqual(Counter(row[0] for row in changed), {"ok": 1, "REVISION_CONFLICT": WORKERS - 1})
        reopened = EventStore(self.store.database)
        self.assertEqual(len(reopened.events(TASK_ID)), 2)
        self.assertEqual(reopened.replay(TASK_ID), reopened.get(TASK_ID))

    def test_192_lifecycles_with_duplicate_commands_and_restart_replay(self):
        results = self.compete([("bulk", (i, 16)) for i in range(WORKERS)])
        self.assertEqual([row[0] for row in results], ["ok"] * WORKERS)
        reopened = EventStore(self.store.database)
        self.assertEqual(len(reopened.list()), 192)
        for snapshot in reopened.list():
            self.assertEqual(snapshot.revision, 4)
            self.assertEqual(reopened.replay(snapshot.spec.task_id), snapshot)
        with closing(sqlite3.connect(self.store.database)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM events").fetchone()[0], 768)
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_duplicate_dispatch_and_identical_launch_have_exactly_one_start(self):
        packet, attempt = self.fixture()
        results = self.compete([("reserve", (packet, attempt, "owner", "same-reserve", 4))] * WORKERS)
        self.assertEqual([row[0] for row in results], ["ok"] * WORKERS)
        claim, = self.store.dispatch_claims(packet.task_id)
        results = self.compete([("start", (claim, "same-start"))] * WORKERS)
        self.assertEqual(Counter(row[0] for row in results), {"ok": 1, "REVISION_CONFLICT": WORKERS - 1})
        self.assertEqual(self.store.replay_attempt(attempt.attempt_id).revision, 2)

    def test_worker_capacity_is_atomic_across_twelve_distinct_claims(self):
        fixtures = [self.fixture(i + 1) for i in range(WORKERS)]
        results = self.compete([("reserve", (*fixture, "owner", f"claim:{i}", 4)) for i, fixture in enumerate(fixtures)])
        self.assertEqual(Counter(row[0] for row in results), {"ok": 4, "REVISION_CONFLICT": 8})
        self.assertEqual(sum(len(self.store.attempts(packet.task_id)) for packet, _ in fixtures), 4)

    def test_workflow_one_shot_and_overlapping_claims(self):
        current = self.workflow()
        results = self.compete([("workflow", (current.spec.workflow_id, 1, "same-start"))] * WORKERS)
        self.assertEqual(Counter(row[0] for row in results), {"ok": 1, "REVISION_CONFLICT": 11})
        others = [self.workflow(i + 2) for i in range(WORKERS)]
        results = self.compete([("workflow", (item.spec.workflow_id, 1, f"start:{i}")) for i, item in enumerate(others)])
        self.assertEqual(Counter(row[0] for row in results), {"REVISION_CONFLICT": WORKERS})
        self.assertEqual(WorkflowStore(self.store).replay(current.spec.workflow_id).revision, 2)

    def test_mixed_workflow_and_dispatch_cannot_own_same_file(self):
        workflow = self.workflow()
        fixtures = [self.fixture(i + 1, ("value.py",)) for i in range(6)]
        operations = [("workflow", (workflow.spec.workflow_id, 1, f"workflow-start:{i}")) for i in range(6)]
        operations += [("reserve", (*fixture, "owner", f"claim:{i}", 64)) for i, fixture in enumerate(fixtures)]
        results = self.compete(operations)
        self.assertEqual(sum(row[0] == "ok" for row in results), 1, "workflow and dispatch granted overlapping ownership")

    def test_evidence_deduplicates_and_resists_update_delete_and_replace(self):
        packet, attempt = self.fixture()
        self.store.reserve_dispatch(packet, attempt, "owner", "claim")
        payload = canonical_json({"message": "immutable stress evidence"})
        record = EvidenceRecord("e" * 32, attempt.attempt_id, "stress", utc_now(), payload, digest_text(payload))
        results = self.compete([("evidence", (record, "same-evidence"))] * WORKERS)
        self.assertEqual([row[0] for row in results], ["ok"] * WORKERS)
        for table in ("events", "commands", "attempt_commands", "attempt_evidence", "dispatch_claims"):
            for sql in (f"UPDATE {table} SET command_id=command_id", f"DELETE FROM {table}",
                        f"INSERT OR REPLACE INTO {table} SELECT * FROM {table}"):
                with self.subTest(sql=sql), closing(sqlite3.connect(self.store.database)) as connection:
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(sql)
        self.assertEqual(EventStore(self.store.database).evidence(packet.task_id), (record,))

    def test_killed_writer_rolls_back_then_command_can_retry(self):
        packet, _ = self.fixture()
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        process = context.Process(target=_uncommitted_writer, args=(str(self.store.database), queue))
        try:
            process.start()
            self.assertEqual(queue.get(timeout=15), "written-uncommitted")
            process.kill()
            process.join(timeout=5)
            self.assertFalse(process.is_alive())
            reopened = EventStore(self.store.database)
            self.assertEqual([item.spec.task_id for item in reopened.list()], [packet.task_id])
            created = reopened.create(TaskSpec(TASK_ID, "crash before commit", Role.QA, Provider.CLAUDE), "crash")
            self.assertEqual(reopened.replay(TASK_ID), created)
            self.assertEqual(len(reopened.events(TASK_ID)), 1)
        finally:
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
            queue.close()
            queue.join_thread()

    def test_concurrent_persisted_timeout_output_limit_nonzero_and_success(self):
        bodies = (("success", "print('ok')"), ("nonzero", "import sys; print('bad'); sys.exit(7)"),
                  ("timeout", "import time; time.sleep(30)"),
                  ("output_limit", "import os; os.write(1,b'x'*20000); os.write(2,b'y'*20000)"))

        async def scenario(index, name, body):
            packet, attempt = self.fixture(index)
            invocation = Invocation(Provider.CLAUDE, (sys.executable, "-B", "-c", body), OutputMode.TEXT, None)
            completed, _, execution, _ = await run_persisted(self.store, packet, attempt, invocation, self.root,
                "stress", f"run:{index}", self.registry, self.context, timeout_seconds=0.5 if name == "timeout" else 5,
                output_limit=1024, max_workers=32)
            self.assertEqual(execution.status.value, "succeeded" if name == "success" else name)
            self.assertTrue(execution.cleanup_complete)
            self.assertLessEqual(execution.stdout_bytes + execution.stderr_bytes, 1024)
            self.assertEqual(completed.status, AttemptStatus.SUCCEEDED if name == "success" else AttemptStatus.FAILED)
            reopened = EventStore(self.store.database)
            self.assertEqual(reopened.replay_attempt(attempt.attempt_id), completed)
            self.assertEqual(Counter(item.kind for item in reopened.evidence(packet.task_id)),
                             {"launch_intent": 1, "process_started": 1, "provider_result": 1})

        async def batch():
            await asyncio.gather(*(scenario(i + 1, *bodies[i % 4]) for i in range(12)))
        asyncio.run(batch())

    def test_eight_cancellations_after_start_retain_evidence_and_reap_processes(self):
        pids, signalled = [], {}

        async def observed(invocation, *args, **kwargs):
            callback = kwargs["on_started"]
            def started(pid, identity):
                callback(pid, identity)
                pids.append(pid)
                signalled[invocation.argv[-1]].set()
            return await run(invocation, *args, **dict(kwargs, on_started=started))

        async def scenario(index):
            packet, attempt = self.fixture(index)
            body = f"import time; time.sleep(30) # {index}"
            signalled[body] = asyncio.Event()
            invocation = Invocation(Provider.CLAUDE, (sys.executable, "-B", "-c", body), OutputMode.TEXT, None)
            worker = asyncio.create_task(run_persisted(self.store, packet, attempt, invocation, self.root,
                "stress", f"cancel:{index}", self.registry, self.context, max_workers=32))
            try:
                await asyncio.wait_for(signalled[body].wait(), timeout=10)
                for _ in range(3):
                    worker.cancel()
                    await asyncio.sleep(0)
                with self.assertRaises(asyncio.CancelledError):
                    await worker
            finally:
                if not worker.done():
                    worker.cancel()
                    await asyncio.gather(worker, return_exceptions=True)
            reopened = EventStore(self.store.database)
            self.assertEqual(reopened.replay_attempt(attempt.attempt_id).status, AttemptStatus.CANCELLED)
            receipt, = [item for item in reopened.evidence(packet.task_id) if item.kind == "provider_result"]
            self.assertTrue(receipt.to_dict()["payload"]["cleanup_complete"])

        async def batch():
            await asyncio.gather(*(scenario(i + 1) for i in range(8)))
        with patch("diamond.controller.run", new=observed):
            asyncio.run(batch())
        self.assertEqual(len(pids), 8)
        for pid in pids:
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_failed_workflow_event_does_not_leave_unrecorded_published_files(self):
        workflow = self.workflow()
        workflows = WorkflowStore(self.store)
        active = workflows.start(workflow.spec.workflow_id, 1, "start")
        with closing(sqlite3.connect(self.store.database)) as connection, connection:
            connection.execute("CREATE TRIGGER reject_publication BEFORE INSERT ON workflow_events WHEN NEW.event_type='FILES_APPLIED' BEGIN SELECT RAISE(ABORT,'stress fault'); END")
        response = {"files": [{"relative_path": "value.py", "base_sha256": None, "content": "value=42\n"}], "summary": "stress"}
        with self.assertRaises(StorageError):
            workflows.publish(active, response)
        self.assertEqual(WorkflowStore(EventStore(self.store.database)).replay(workflow.spec.workflow_id), active)
        self.assertEqual(workspace_manifest(self.root), active.manifest_json, "SQL rollback left unrecorded published source")

    def delivery_fixture(self):
        fixture = delivery_fixtures.DeliveryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_delivery_twelve_start_claims_then_restart_quarantine(self):
        fixture = self.delivery_fixture()
        created = fixture.create(fixture.component())
        results = self.compete([("delivery", (created.spec.delivery_id, created.revision))] * WORKERS, fixture.ledger.database)
        self.assertEqual(Counter(row[0] for row in results), {"ok": 1, "REVISION_CONFLICT": 11})
        reopened = DeliveryStore(EventStore(fixture.ledger.database))
        current = reopened.replay(created.spec.delivery_id)
        quarantined = reopened.invalidate(current, "stress restart with unproven stage")
        self.assertEqual(reopened.replay(created.spec.delivery_id), quarantined)
        self.assertEqual((fixture.root / "src/value.py").read_text(), "def value(): return 0\n")

    def test_delivery_all_gates_backup_replay_and_immutable_history(self):
        fixture = self.delivery_fixture()
        accepted = fixture.pipeline(fixture.create(fixture.component()))[-1]
        proof = fixture.deliveries.certificate(accepted.spec.delivery_id, fixture.config_digest)
        for table in ("workflow_events", "workflow_commands", "delivery_events", "delivery_commands"):
            for sql in (f"UPDATE {table} SET command_id=command_id", f"DELETE FROM {table}",
                        f"INSERT OR REPLACE INTO {table} SELECT * FROM {table}"):
                with self.subTest(sql=sql), closing(sqlite3.connect(fixture.ledger.database)) as connection:
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(sql)
        backup = fixture.base.resolve() / "stress-backup.sqlite3"
        fixture.ledger.backup(backup)
        restored = EventStore(backup)
        self.assertEqual(DeliveryStore(restored).replay(accepted.spec.delivery_id), accepted)
        self.assertEqual(DeliveryStore(restored).certificate(accepted.spec.delivery_id, fixture.config_digest), proof)
        for task in restored.list():
            self.assertEqual(restored.replay(task.spec.task_id), task)
            for attempt in restored.attempts(task.spec.task_id):
                self.assertEqual(restored.replay_attempt(attempt.attempt_id), attempt)
        print(f"STRESS delivery tasks={len(restored.list())} gate_reports={len(json.loads(accepted.reports_json))}", flush=True)

    def test_failed_delivery_event_does_not_leave_unrecorded_published_files(self):
        fixture = self.delivery_fixture()
        created = fixture.create(fixture.component())
        with closing(sqlite3.connect(fixture.ledger.database)) as connection, connection:
            connection.execute("CREATE TRIGGER reject_publication BEFORE INSERT ON delivery_events WHEN NEW.event_type='FILES_APPLIED' BEGIN SELECT RAISE(ABORT,'stress fault'); END")
        try:
            fixture.step(created)
        except StorageError:
            pass
        current = DeliveryStore(EventStore(fixture.ledger.database)).replay(created.spec.delivery_id)
        self.assertEqual(current.manifest_json, created.manifest_json)
        self.assertEqual(workspace_manifest(fixture.root), created.manifest_json, "SQL rollback left unrecorded integrated source")

    def test_twelve_completion_retries_commit_one_receipt(self):
        packet, attempt = self.fixture()
        claim, _ = self.store.reserve_dispatch(packet, attempt, "owner", "claim")
        started = self.store.acquire_start(claim, "start")
        invocation = Invocation(Provider.CLAUDE, (sys.executable,), OutputMode.TEXT, None)
        execution = ExecutionResult(ExecutionStatus.SUCCEEDED, 0, "ok", "")
        completed = replace(started, status=AttemptStatus.SUCCEEDED, completed_at=utc_now(), returncode=0,
                            result_status="valid", stdout_sha256=digest_text("ok"), stderr_sha256=digest_text(""),
                            stdout_bytes=2, revision=3)
        receipt = output_evidence(completed, invocation, execution, parse_result(invocation, execution), utc_now())
        results = self.compete([("complete", (completed, 2, receipt, "same-completion"))] * WORKERS)
        self.assertEqual([row[0] for row in results], ["ok"] * WORKERS)
        reopened = EventStore(self.store.database)
        self.assertEqual(reopened.replay_attempt(attempt.attempt_id), completed)
        self.assertEqual(reopened.evidence(packet.task_id), (receipt,))

    def test_twelve_restart_reconcilers_quarantine_once_without_relaunch(self):
        packet, attempt = self.fixture()
        claim, _ = self.store.reserve_dispatch(packet, attempt, "owner", "claim")
        self.store.acquire_start(claim, "start")
        results = self.compete([("reconcile", (attempt.attempt_id, "same-recovery", "stress restart"))] * WORKERS)
        self.assertEqual([row[0] for row in results], ["ok"] * WORKERS)
        reopened = EventStore(self.store.database)
        recovered = reopened.replay_attempt(attempt.attempt_id)
        self.assertEqual((recovered.status, recovered.revision), (AttemptStatus.AMBIGUOUS, 3))
        receipt, = reopened.evidence(packet.task_id)
        self.assertFalse(receipt.to_dict()["payload"]["relaunch_allowed"])
        with self.assertRaises(ConflictError):
            reopened.acquire_start(claim, "new-start")

    def test_busy_timeout_has_no_partial_commit_and_same_command_retries(self):
        packet, _ = self.fixture()
        impatient = EventStore(self.store.database, busy_timeout_ms=25)
        spec = TaskSpec(TASK_ID, "blocked writer", Role.QA, Provider.CLAUDE)
        with closing(sqlite3.connect(self.store.database)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            start = time.monotonic()
            with self.assertRaises(StorageError):
                impatient.create(spec, "retry-after-lock")
            elapsed = time.monotonic() - start
            connection.rollback()
        self.assertLess(elapsed, 2)
        self.assertEqual([item.spec.task_id for item in self.store.list()], [packet.task_id])
        created = impatient.create(spec, "retry-after-lock")
        self.assertEqual(impatient.create(spec, "retry-after-lock"), created)
        self.assertEqual(self.store.replay(TASK_ID), created)
        print(f"STRESS busy_timeout={elapsed:.4f}s", flush=True)

    def test_output_byte_boundaries_binary_hashes_and_spawn_failures(self):
        async def scenario(size):
            body = f"import os; os.write(1, b'\\xff' * {size})"
            invocation = Invocation(Provider.CLAUDE, (sys.executable, "-B", "-c", body), OutputMode.TEXT, None)
            result = await run(invocation, self.root, timeout_seconds=5, output_limit=1024)
            self.assertEqual(result.status, ExecutionStatus.SUCCEEDED if size <= 1024 else ExecutionStatus.OUTPUT_LIMIT)
            self.assertEqual(result.stdout_bytes, min(size, 1024))
            self.assertEqual(result.stdout_sha256, hashlib.sha256(b"\xff" * min(size, 1024)).hexdigest())
            self.assertTrue(result.cleanup_complete)

        async def missing():
            invocation = Invocation(Provider.CLAUDE, (str(self.root / "missing-executable"),), OutputMode.TEXT, None)
            result = await run(invocation, self.root, timeout_seconds=5)
            self.assertEqual(result.status, ExecutionStatus.START_FAILED)
            self.assertIsNone(result.returncode)

        async def batch():
            await asyncio.gather(*(scenario(size) for size in (1023, 1024, 1025) for _ in range(4)),
                                 *(missing() for _ in range(4)))
        asyncio.run(batch())
