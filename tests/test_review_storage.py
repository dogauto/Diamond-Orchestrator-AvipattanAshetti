import errno
import io
import json
import os
import sqlite3
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from diamond.cli import main
from diamond.domain import Provider, Role, TaskSpec, TaskStatus
from diamond.errors import StorageError
from diamond.schema import SCHEMA_STATEMENTS, SCHEMA_VERSION
from diamond.store import EventStore
from support import TASK_ID, ProjectCase


class ReviewStorageTests(ProjectCase):
    def setUp(self) -> None:
        super().setUp()
        self.database = self.project / ".diamond" / "diamond.sqlite3"
        self.store = EventStore(self.database)
        self.spec = TaskSpec(TASK_ID, "Preserve ledger facts", Role.BUILDER, Provider.CLAUDE)

    def operations(self):
        return (self.store.list, lambda: self.store.get(TASK_ID), lambda: self.store.events(TASK_ID),
                lambda: self.store.transition(TASK_ID, TaskStatus.CANCELLED, 1, "cancel"))

    def test_stat_permission_and_io_errors_are_storage_errors_without_mutation(self) -> None:
        original = self.store.create(self.spec, "create")
        before = self.database.read_bytes()
        original_stat = Path.stat
        for error in (PermissionError(errno.EACCES, "injected denied access"), OSError(errno.EIO, "injected I/O error")):
            def failing_stat(path, *args, **kwargs):
                if path == self.store.database:
                    raise error
                return original_stat(path, *args, **kwargs)

            with patch.object(Path, "stat", new=failing_stat), \
                 patch.object(Path, "exists", side_effect=AssertionError("exists() must not classify storage")):
                for operation in self.operations():
                    with self.subTest(error=error, operation=operation), self.assertRaises(StorageError):
                        operation()
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(self.store.get(TASK_ID), original)

    def test_parent_stat_and_resolution_errors_are_storage_errors(self) -> None:
        self.store.create(self.spec, "create")
        before = self.database.read_bytes()
        original_stat, original_resolve = Path.stat, Path.resolve

        def parent_denied(path, *args, **kwargs):
            if path == self.store.database.parent:
                raise PermissionError(errno.EACCES, "injected parent access denied")
            return original_stat(path, *args, **kwargs)

        with patch.object(Path, "stat", new=parent_denied), self.assertRaises(StorageError):
            EventStore(self.store.database)

        def resolution_denied(path, *args, **kwargs):
            if path == self.store.database:
                raise PermissionError(errno.EACCES, "injected symlink resolution access denied")
            return original_resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", new=resolution_denied):
            with self.assertRaises(StorageError):
                EventStore(self.store.database)
            for operation in self.operations():
                with self.assertRaises(StorageError):
                    operation()
        self.assertEqual(self.database.read_bytes(), before)

    def test_cli_storage_errors_for_mocked_database_and_parent_permissions(self) -> None:
        self.store.create(self.spec, "create")
        before = self.database.read_bytes()
        original_stat = Path.stat
        for inaccessible in (self.store.database, self.store.database.parent):
            def denied_stat(path, *args, **kwargs):
                if path == inaccessible:
                    raise PermissionError(errno.EACCES, "injected inaccessible state")
                return original_stat(path, *args, **kwargs)

            with patch.object(Path, "stat", new=denied_stat):
                for arguments in (("list",), ("show", TASK_ID), ("events", TASK_ID)):
                    output, errors = io.StringIO(), io.StringIO()
                    with redirect_stdout(output), redirect_stderr(errors):
                        code = main(["--project", str(self.project), "--json", "task", *arguments])
                    self.assertEqual(code, 5, output.getvalue() + errors.getvalue())
                    self.assertEqual(json.loads(output.getvalue())["error"]["code"], "STORAGE_ERROR")
                    self.assertEqual(errors.getvalue(), "")
        self.assertEqual(self.database.read_bytes(), before)

    def test_real_inaccessible_state_directory_is_not_missing_and_cli_exits_five(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root bypasses directory permission restrictions; mocked checks still run")
        original = self.store.create(self.spec, "create")
        before = self.database.read_bytes()
        state = self.database.parent
        mode = state.stat().st_mode & 0o777
        try:
            state.chmod(0)
            for operation in self.operations():
                with self.assertRaises(StorageError):
                    operation()
            for arguments in (("list",), ("show", TASK_ID), ("events", TASK_ID)):
                result = self.cli_process("--json", "task", *arguments)
                self.assertEqual(result.returncode, 5, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout)["error"]["code"], "STORAGE_ERROR")
        finally:
            state.chmod(mode)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(self.store.get(TASK_ID), original)

    def test_implicit_rowid_and_explicit_key_replacements_preserve_ledger(self) -> None:
        original = self.store.create(self.spec, "create")
        blocked = self.store.transition(TASK_ID, TaskStatus.BLOCKED, 1, "block", "waiting")
        events = self.store.events(TASK_ID)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            for table in ("events", "commands"):
                for alias in ("rowid", "_rowid_", "oid"):
                    with self.subTest(table=table, alias=alias), self.assertRaises(sqlite3.OperationalError):
                        if table == "events":
                            connection.execute(f"""INSERT OR REPLACE INTO events
                                ({alias},event_id,schema_version,task_id,revision,event_type,timestamp,actor,command_id,payload_json)
                                VALUES (1,?,1,?,3,'TASK_TRANSITIONED',?,'controller','fresh-command','{{}}')""",
                                ("f" * 32, TASK_ID, original.created_at))
                        else:
                            connection.execute(f"INSERT OR REPLACE INTO commands ({alias},command_id,request_json,snapshot_json) VALUES (1,'fresh-command','{{}}','{{}}')")
            for statement in (
                "INSERT OR REPLACE INTO events SELECT event_id,schema_version,task_id,revision+10,event_type,timestamp,actor,'fresh-command',payload_json FROM events WHERE revision=1",
                "INSERT OR REPLACE INTO commands SELECT command_id,'{}','{}' FROM commands WHERE command_id='create'",
            ):
                with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(statement)
        reopened = EventStore(self.database)
        self.assertEqual(reopened.events(TASK_ID), events)
        self.assertEqual(reopened.get(TASK_ID), blocked)
        self.assertEqual(reopened.replay(TASK_ID), blocked)
        self.assertEqual(reopened.create(self.spec, "create"), original)
        self.assertEqual(reopened.transition(TASK_ID, TaskStatus.BLOCKED, 1, "block", "waiting"), blocked)

    def test_legacy_schema_and_forged_version_rejected_without_migration(self) -> None:
        self.database.parent.mkdir()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            for statement in SCHEMA_STATEMENTS:
                connection.execute(statement.replace(" WITHOUT ROWID", ""))
            connection.execute("PRAGMA user_version=1")
        for version in (1, SCHEMA_VERSION):
            with closing(sqlite3.connect(self.database)) as connection, connection:
                connection.execute(f"PRAGMA user_version={version}")
            before = self.database.read_bytes()
            for operation in (self.store.list, lambda: self.store.create(self.spec, "create")):
                with self.subTest(version=version), self.assertRaises(StorageError):
                    operation()
            self.assertEqual(self.database.read_bytes(), before)
            with closing(sqlite3.connect(self.database)) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], version)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    def test_schema_two_requires_explicit_evidence_migration(self) -> None:
        self.database.parent.mkdir()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            for statement in SCHEMA_STATEMENTS[:9]:
                connection.execute(statement)
            connection.execute("PRAGMA user_version=2")
        self.assertEqual(self.store.migrate(), SCHEMA_VERSION)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            for table in ("attempts", "attempt_commands", "attempt_evidence"):
                self.assertIsNotNone(connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())
        self.assertEqual(self.store.list(), ())

    def test_cli_migration_is_explicit_and_does_not_require_provider_config(self) -> None:
        self.database.parent.mkdir()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            for statement in SCHEMA_STATEMENTS[:9]:
                connection.execute(statement)
            connection.execute("PRAGMA user_version=2")
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = main(["--project", str(self.project), "--json", "migrate"])
        self.assertEqual(code, 0, output.getvalue() + errors.getvalue())
        self.assertEqual(json.loads(output.getvalue())["result"]["schema_version"], SCHEMA_VERSION)
        self.assertEqual(errors.getvalue(), "")

    def test_schema_three_migration_preserves_tasks_and_rejects_trigger_spoofing(self) -> None:
        self.database.parent.mkdir()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            for statement in SCHEMA_STATEMENTS[:18]:
                connection.execute(statement)
            connection.execute("PRAGMA user_version=3")
        before = self.database.read_bytes()
        with self.assertRaises(StorageError):
            self.store.list()
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(self.store.migrate(), SCHEMA_VERSION)
        snapshot = self.store.create(self.spec, "create")
        self.assertEqual(self.store.replay(TASK_ID), snapshot)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("DROP TRIGGER dispatch_claims_no_update")
            connection.execute("CREATE TRIGGER dispatch_claims_no_update BEFORE UPDATE ON dispatch_claims BEGIN SELECT 1; END")
        before = self.database.read_bytes()
        with self.assertRaises(StorageError):
            self.store.list()
        self.assertEqual(self.database.read_bytes(), before)
