import multiprocessing
from pathlib import Path
from typing import Any

from diamond.domain import Provider, Role, TaskSpec, TaskStatus
from diamond.evidence import AttemptStatus, ExecutionAttempt
from diamond.errors import DiamondError
from diamond.packets import OutputContract, TaskPacket
from diamond.skills import SkillRegistry
from diamond.store import EventStore
from diamond.validation import utc_now
from support import TASK_ID, ProjectCase


def _writer(database: str, barrier: Any, queue: Any, operation: str, command: str) -> None:
    try:
        store = EventStore(Path(database))
        barrier.wait(timeout=8)
        if operation == "create":
            result = store.create(TaskSpec(TASK_ID, "Concurrent foundation", Role.BUILDER, Provider.CLAUDE), command)
        elif operation == "claim":
            spec = store.get(TASK_ID).spec
            attempt_id = ("c" if command == "left" else "d") * 32
            workspace = Path(database).parent.parent.resolve()
            packet = TaskPacket.from_task_spec(spec, attempt_id, workspace, 1, "1" * 64, "2" * 64, "3" * 64,
                                               SkillRegistry(()), (), OutputContract("text", (), 4096))
            attempt = ExecutionAttempt(attempt_id, TASK_ID, 1, spec.role, spec.provider, "claude",
                                       str(workspace), AttemptStatus.INTENDED, utc_now())
            result, _ = store.reserve_dispatch(packet, attempt, command, command)
        elif operation == "start":
            claim = store.dispatch_claims(TASK_ID)[0]
            result = store.acquire_start(claim, "identical-launch-command")
        else:
            result = store.transition(TASK_ID, TaskStatus(operation), 1, command, "waiting" if operation == "BLOCKED" else None)
        queue.put(("success", result.to_dict()))
    except DiamondError as exc:
        queue.put(("domain_error", exc.code))
    except Exception as exc:
        queue.put(("unexpected", repr(exc)))


class MultiprocessTests(ProjectCase):
    def compete(self, operations: tuple[tuple[str, str], ...]) -> list[Any]:
        context = multiprocessing.get_context("spawn")
        barrier, queue = context.Barrier(2), context.Queue()
        database = self.project / ".diamond" / "diamond.sqlite3"
        processes = [context.Process(target=_writer, args=(str(database), barrier, queue, operation, command))
                     for operation, command in operations]
        try:
            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=12)
                self.assertFalse(process.is_alive(), "competing writer exceeded bounded join")
                self.assertEqual(process.exitcode, 0)
            return [queue.get(timeout=2) for _ in processes]
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=2)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=2)
            queue.close()
            queue.join_thread()

    def test_spawn_cas_one_winner_one_conflict_one_revision(self) -> None:
        store = EventStore(self.project / ".diamond" / "diamond.sqlite3")
        store.create(TaskSpec(TASK_ID, "Concurrent foundation", Role.BUILDER, Provider.CLAUDE), "create")
        results = self.compete((("BLOCKED", "block"), ("CANCELLED", "cancel")))
        self.assertEqual([status for status, _ in results].count("success"), 1)
        self.assertEqual([result for result in results if result[0] == "domain_error"], [("domain_error", "REVISION_CONFLICT")])
        winner = next(payload for status, payload in results if status == "success")
        self.assertEqual(store.get(TASK_ID).to_dict(), winner)
        self.assertEqual(store.get(TASK_ID).revision, 2)
        self.assertEqual([event.revision for event in store.events(TASK_ID)], [1, 2])
        self.assertEqual(store.replay(TASK_ID), store.get(TASK_ID))

    def test_spawn_duplicate_create_serializes_without_double_event(self) -> None:
        results = self.compete((("create", "same-command"), ("create", "same-command")))
        self.assertEqual([status for status, _ in results], ["success", "success"])
        self.assertEqual(results[0][1], results[1][1])
        store = EventStore(self.project / ".diamond" / "diamond.sqlite3")
        self.assertEqual(store.get(TASK_ID).revision, 1)
        self.assertEqual(len(store.events(TASK_ID)), 1)

    def test_competing_claims_and_identical_launch_commands_have_one_winner(self) -> None:
        store = EventStore(self.project / ".diamond" / "diamond.sqlite3")
        store.create(TaskSpec(TASK_ID, "Concurrent foundation", Role.BUILDER, Provider.CLAUDE), "create")
        claims = self.compete((("claim", "left"), ("claim", "right")))
        self.assertEqual([status for status, _ in claims].count("success"), 1)
        self.assertEqual([status for status, _ in claims].count("domain_error"), 1)
        starts = self.compete((("start", "left"), ("start", "right")))
        self.assertEqual([status for status, _ in starts].count("success"), 1)
        self.assertEqual([status for status, _ in starts].count("domain_error"), 1)
        self.assertEqual(len(store.dispatch_claims(TASK_ID)), 1)
        self.assertEqual(store.attempts(TASK_ID)[0].revision, 2)
