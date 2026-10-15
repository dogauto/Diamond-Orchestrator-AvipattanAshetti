"""Tests for the session registry using unittest conventions."""

import tempfile
from pathlib import Path

from support import ProjectCase

from diamond.domain import Provider, Role
from diamond.transport.session_registry import SessionRegistry, SessionRecord


class SessionRegistryTests(ProjectCase):
    """Tests for SessionRegistry."""

    def setUp(self) -> None:
        super().setUp()
        self.db_path = self.base / "sessions.sqlite3"
        self.registry = SessionRegistry(self.db_path)

    def test_register_session(self) -> None:
        """Test registering a new session."""
        record = self.registry.register(
            agent_name="builder-1",
            role=Role.BUILDER,
            provider=Provider.CLAUDE,
            tmux_session="diamond-agent-builder-1",
            tmux_pane="0",
            capabilities=("read", "write", "execute"),
        )

        self.assertEqual(record.agent_name, "builder-1")
        self.assertEqual(record.role, Role.BUILDER)
        self.assertEqual(record.provider, Provider.CLAUDE)
        self.assertEqual(record.tmux_session, "diamond-agent-builder-1")
        self.assertEqual(record.tmux_pane, "0")
        self.assertEqual(record.capabilities, ("read", "write", "execute"))
        self.assertEqual(record.owned_task_ids, ())
        self.assertEqual(record.status, "ACTIVE")
        self.assertTrue(record.session_id.startswith("builder-1-"))

    def test_get_session(self) -> None:
        """Test getting a session by ID."""
        record = self.registry.register(
            agent_name="builder-1",
            role=Role.BUILDER,
            provider=Provider.CLAUDE,
            tmux_session="diamond-agent-builder-1",
            tmux_pane="0",
        )

        retrieved = self.registry.get(record.session_id)
        self.assertEqual(retrieved.session_id, record.session_id)
        self.assertEqual(retrieved.agent_name, "builder-1")

    def test_get_by_agent(self) -> None:
        """Test getting a session by agent name."""
        self.registry.register(
            agent_name="builder-1",
            role=Role.BUILDER,
            provider=Provider.CLAUDE,
            tmux_session="diamond-agent-builder-1",
            tmux_pane="0",
        )

        retrieved = self.registry.get_by_agent("builder-1")
        self.assertEqual(retrieved.agent_name, "builder-1")

    def test_duplicate_agent_rejected(self) -> None:
        """Test duplicate agent name is rejected."""
        self.registry.register(
            agent_name="builder-1",
            role=Role.BUILDER,
            provider=Provider.CLAUDE,
            tmux_session="diamond-agent-builder-1",
            tmux_pane="0",
        )

        with self.assertRaises(Exception):  # ValidationError
            self.registry.register(
                agent_name="builder-1",
                role=Role.RESEARCHER,
                provider=Provider.CODEX,
                tmux_session="diamond-agent-builder-1",
                tmux_pane="0",
            )

    def test_list_sessions(self) -> None:
        """Test listing sessions."""
        self.registry.register("builder-1", Role.BUILDER, Provider.CLAUDE, "s1", "0")
        self.registry.register("researcher-1", Role.RESEARCHER, Provider.CODEX, "s2", "0")
        self.registry.register("builder-2", Role.BUILDER, Provider.GEMINI, "s3", "0")

        all_sessions = self.registry.list()
        self.assertEqual(len(all_sessions), 3)

        builders = self.registry.list(role=Role.BUILDER)
        self.assertEqual(len(builders), 2)

        active = self.registry.list(status="ACTIVE")
        self.assertEqual(len(active), 3)

    def test_update_status_cas(self) -> None:
        """Test updating status with CAS."""
        record = self.registry.register("builder-1", Role.BUILDER, Provider.CLAUDE, "s1", "0")

        # Get current revision (it's 1 for new sessions)
        updated = self.registry.update_status(record.session_id, "BUSY", expected_revision=1)
        self.assertEqual(updated.status, "BUSY")
        self.assertNotEqual(updated.updated_at, record.updated_at)

        # Try with wrong revision - should fail
        with self.assertRaises(Exception):  # ValidationError
            self.registry.update_status(record.session_id, "IDLE", expected_revision=1)

        # Correct revision works
        updated2 = self.registry.update_status(record.session_id, "IDLE", expected_revision=2)
        self.assertEqual(updated2.status, "IDLE")

    def test_claim_task(self) -> None:
        """Test claiming a task."""
        record = self.registry.register("builder-1", Role.BUILDER, Provider.CLAUDE, "s1", "0")

        updated = self.registry.claim_task(record.session_id, "task-123", expected_revision=1)
        self.assertIn("task-123", updated.owned_task_ids)

        # Claim same task again - should be idempotent
        updated2 = self.registry.claim_task(record.session_id, "task-123", expected_revision=2)
        self.assertEqual(updated2.owned_task_ids, ("task-123",))

        # Claim another task
        updated3 = self.registry.claim_task(record.session_id, "task-456", expected_revision=2)
        self.assertEqual(set(updated3.owned_task_ids), {"task-123", "task-456"})

    def test_release_task(self) -> None:
        """Test releasing a task."""
        record = self.registry.register("builder-1", Role.BUILDER, Provider.CLAUDE, "s1", "0")
        self.registry.claim_task(record.session_id, "task-123", expected_revision=1)

        updated = self.registry.release_task(record.session_id, "task-123", expected_revision=2)
        self.assertNotIn("task-123", updated.owned_task_ids)

        # Release non-existent task - should be idempotent
        updated2 = self.registry.release_task(record.session_id, "task-123", expected_revision=3)
        self.assertEqual(updated2.owned_task_ids, ())

    def test_update_capabilities(self) -> None:
        """Test updating capabilities."""
        record = self.registry.register("builder-1", Role.BUILDER, Provider.CLAUDE, "s1", "0")

        updated = self.registry.update_capabilities(record.session_id, ("read", "write"), expected_revision=1)
        self.assertEqual(updated.capabilities, ("read", "write"))

    def test_terminate(self) -> None:
        """Test terminating a session."""
        record = self.registry.register("builder-1", Role.BUILDER, Provider.CLAUDE, "s1", "0")

        terminated = self.registry.terminate(record.session_id, expected_revision=1)
        self.assertEqual(terminated.status, "TERMINATED")

    def test_list_with_status_filter(self) -> None:
        """Test listing with status filter."""
        r1 = self.registry.register("a1", Role.BUILDER, Provider.CLAUDE, "s1", "0")
        r2 = self.registry.register("a2", Role.RESEARCHER, Provider.CODEX, "s2", "0")
        self.registry.terminate(r1.session_id, expected_revision=1)

        active = self.registry.list(status="ACTIVE")
        terminated = self.registry.list(status="TERMINATED")

        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].agent_name, "a2")
        self.assertEqual(len(terminated), 1)
        self.assertEqual(terminated[0].agent_name, "a1")

    def test_session_record_json_serialization(self) -> None:
        """Test SessionRecord JSON serialization."""
        record = SessionRecord(
            session_id="test-123",
            agent_name="builder-1",
            role=Role.BUILDER,
            provider=Provider.CLAUDE,
            tmux_session="s1",
            tmux_pane="0",
            capabilities=("read", "write"),
            owned_task_ids=("task-1",),
            status="ACTIVE",
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
            metadata={"key": "value"},
        )

        json_str = record.to_json()
        self.assertIn('"agent_name":"builder-1"', json_str)
        self.assertIn('"capabilities":["read","write"]', json_str)