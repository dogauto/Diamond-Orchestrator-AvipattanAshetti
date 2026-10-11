import os
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from stat import S_IMODE
from unittest.mock import patch

from diamond.domain import Provider, Role, TaskSpec
from diamond.errors import ConflictError, StorageError
from diamond.evidence import AttemptStatus, ExecutionAttempt, digest_text
from diamond.packets import OutputContract, TaskPacket
from diamond.skills import SkillRegistry
from diamond.store import EventStore
from diamond.validation import utc_now
from diamond.workflow_contracts import TestCommand, WorkflowSpec, WorkflowStatus
from diamond.workflow_store import WorkflowStore
from diamond.workspace import PublicationRollbackError, apply_response, workspace_manifest
from support import ProjectCase, TASK_ID
import test_delivery as delivery_fixtures


class CommitFailure(sqlite3.Connection):
    def commit(self):
        raise sqlite3.OperationalError("injected commit failure")


class PublicationTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.root = self.project.resolve()
        self.store = EventStore(self.base.resolve() / "state" / "ledger.sqlite3")
        self.workflows = WorkflowStore(self.store)
        self.target = self.root / "value.py"
        self.original = b"value = 0\r\n"
        self.target.write_bytes(self.original)
        self.target.chmod(0o751)
        self.spec = WorkflowSpec(TASK_ID, "publication rollback", str(self.root), ("value.py", "new/deep/value.py"),
            (TestCommand("check", (sys.executable, "-B", "-c", "assert True")),), Provider.CLAUDE, Provider.CODEX,
            "3" * 64, workspace_manifest(self.root), max_workers=64)
        created = self.workflows.create(self.spec, "create")
        self.active = self.workflows.start(TASK_ID, created.revision, "start")
        self.response = {"files": [
            {"relative_path": "value.py", "base_sha256": digest_text(self.original.decode()), "content": "value = 42\n"},
            {"relative_path": "new/deep/value.py", "base_sha256": None, "content": "new = True\n"}], "summary": "replacement"}

    def reject_event(self):
        with closing(sqlite3.connect(self.store.database)) as connection, connection:
            connection.execute("CREATE TRIGGER reject_publish BEFORE INSERT ON workflow_events WHEN NEW.event_type='FILES_APPLIED' BEGIN SELECT RAISE(ABORT,'injected event failure'); END")

    def assert_restored(self):
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(S_IMODE(self.target.stat().st_mode), 0o751)
        self.assertFalse((self.root / "new").exists())
        self.assertEqual(workspace_manifest(self.root), self.spec.initial_manifest_json)
        self.assertEqual(self.workflows.replay(TASK_ID), self.active)

    def test_event_failure_restores_bytes_modes_and_removes_new_directories_then_retries(self):
        self.reject_event()
        with self.assertRaises(StorageError):
            self.workflows.publish(self.active, self.response)
        self.assert_restored()
        with closing(sqlite3.connect(self.store.database)) as connection, connection:
            connection.execute("DROP TRIGGER reject_publish")
        published, _ = self.workflows.publish(self.active, self.response)
        self.assertEqual(self.workflows.replay(TASK_ID), published)
        self.assertEqual(self.target.read_text(), "value = 42\n")
        self.assertEqual(S_IMODE(self.target.stat().st_mode), 0o751)

    def test_actual_connection_commit_failure_restores_publication(self):
        connect = sqlite3.connect
        with patch("diamond.store.sqlite3.connect", side_effect=lambda *args, **kwargs: connect(*args, factory=CommitFailure, **kwargs)):
            with self.assertRaisesRegex(StorageError, "commit failure"):
                self.workflows.publish(self.active, self.response)
        self.assert_restored()

    def test_second_file_replace_failure_undoes_first_file(self):
        real_replace = os.replace

        def fail_second(source, target):
            if Path(source).name.startswith(".diamond-stage-") and Path(target).parent.name == "deep":
                raise OSError("second replacement failed")
            return real_replace(source, target)

        with patch("diamond.workspace.os.replace", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "second replacement"):
                apply_response(self.spec, self.active.manifest_json, self.response)
        self.assert_restored()

    def test_failed_restore_retains_original_copy_and_quarantines_ownership(self):
        self.reject_event()
        real_replace = os.replace

        def fail_restore(source, target):
            if Path(source).name.startswith(".diamond-rollback-"):
                raise OSError("restore denied")
            return real_replace(source, target)

        with patch("diamond.workspace.os.replace", side_effect=fail_restore):
            with self.assertRaisesRegex(PublicationRollbackError, "original retained"):
                self.workflows.publish(self.active, self.response)
        quarantined = self.workflows.replay(TASK_ID)
        self.assertEqual(quarantined.status, WorkflowStatus.BLOCKED)
        backup, = self.root.glob(".diamond-rollback-*")
        self.assertEqual(backup.read_bytes(), self.original)
        self.assertEqual(S_IMODE(backup.stat().st_mode), 0o751)
        self.assertFalse((self.root / "new").exists())
        other = self.workflows.create(replace(self.spec, workflow_id="b" * 32), "other")
        with self.assertRaisesRegex(ConflictError, "overlaps"):
            self.workflows.start(other.spec.workflow_id, other.revision, "other-start")
        with self.assertRaisesRegex(ConflictError, "workspace changed"):
            self.workflows.reconcile(TASK_ID, quarantined.revision, "cannot recover yet", "reconcile")

    def test_temporary_cleanup_failure_is_reported_and_quarantined(self):
        real_unlink = Path.unlink

        def fail_cleanup(path, *args, **kwargs):
            if path.name.startswith(".diamond-stage-"):
                raise OSError("cleanup denied")
            return real_unlink(path, *args, **kwargs)

        with patch("diamond.workspace.os.replace", side_effect=OSError("publish denied")), \
             patch("diamond.workspace.Path.unlink", new=fail_cleanup):
            with self.assertRaisesRegex(PublicationRollbackError, "cleanup denied"):
                self.workflows.publish(self.active, self.response)
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(self.workflows.replay(TASK_ID).status, WorkflowStatus.BLOCKED)

    def test_intervening_human_edit_is_preserved_during_rollback(self):
        original_commit = self.workflows._commit

        def change_then_fail(connection, current, identifier, kind, *args):
            if kind == "FILES_APPLIED":
                self.target.write_text("human edit\n")
                raise StorageError("event rejected after human edit")
            return original_commit(connection, current, identifier, kind, *args)

        with patch.object(self.workflows, "_commit", side_effect=change_then_fail):
            with self.assertRaisesRegex(PublicationRollbackError, "preserving intervening edit"):
                self.workflows.publish(self.active, self.response)
        self.assertEqual(self.target.read_text(), "human edit\n")
        self.assertEqual(self.workflows.replay(TASK_ID).status, WorkflowStatus.BLOCKED)
        backup, = self.root.glob(".diamond-rollback-*")
        self.assertEqual(backup.read_bytes(), self.original)

    def test_workflow_own_attempt_can_reserve_but_quarantine_revokes_start(self):
        task = TaskSpec(self.active.active_task_id, "own workflow execution", Role.BUILDER, Provider.CLAUDE,
                        owned_paths=("value.py",), allowed_tools=(sys.executable,))
        self.store.create(task, "task")
        packet = TaskPacket.from_task_spec(task, self.active.active_attempt_id, self.root, 1,
            "1" * 64, "2" * 64, "3" * 64, SkillRegistry(()), (), OutputContract("text", (), 4096))
        attempt = ExecutionAttempt(packet.attempt_id, task.task_id, 1, task.role, task.provider,
                                   sys.executable, str(self.root), AttemptStatus.INTENDED, utc_now())
        claim, _ = self.store.reserve_dispatch(packet, attempt, "owner", "claim")
        self.workflows.mutate(TASK_ID, "BLOCKED", {"reason": "operator quarantine"}, self.active.revision, "block")
        with self.assertRaisesRegex(ConflictError, "quarantined"):
            self.store.acquire_start(claim, "launch")
        self.assertEqual(self.store.get_attempt(attempt.attempt_id).status, AttemptStatus.INTENDED)

    def test_delivery_restore_failure_quarantines_and_preserves_original_mode(self):
        fixture = delivery_fixtures.DeliveryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        target = fixture.root / "src/value.py"
        target.chmod(0o751)
        original = target.read_bytes()
        created = fixture.create(fixture.component())
        with closing(sqlite3.connect(fixture.ledger.database)) as connection, connection:
            connection.execute("CREATE TRIGGER reject_publish BEFORE INSERT ON delivery_events WHEN NEW.event_type='FILES_APPLIED' BEGIN SELECT RAISE(ABORT,'injected event failure'); END")
        real_replace = os.replace

        def fail_restore(source, destination):
            if Path(source).name.startswith(".diamond-rollback-") and Path(destination) == target:
                raise OSError("delivery restore denied")
            return real_replace(source, destination)

        with patch("diamond.workspace.os.replace", side_effect=fail_restore):
            with self.assertRaisesRegex(PublicationRollbackError, "delivery restore denied"):
                fixture.step(created)
        self.assertEqual(fixture.deliveries.replay(created.spec.delivery_id).status.value, "BLOCKED")
        backup, = target.parent.glob(".diamond-rollback-*")
        self.assertEqual(backup.read_bytes(), original)
        self.assertEqual(S_IMODE(backup.stat().st_mode), 0o751)
