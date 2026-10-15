"""Integration test for two simulated CLI agent sessions using unittest conventions."""

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from support import ProjectCase

from diamond.domain import Provider, Role
from diamond.transport import (
    DeliveryQueue,
    DeliveryState,
    MessageTransport,
    SessionRegistry,
)


class TestTwoAgentCommunication(ProjectCase):
    """Integration test with two simulated CLI agents."""

    def setUp(self) -> None:
        super().setUp()
        self.queue_root = self.base / "queue"
        self.db_path = self.base / "sessions.sqlite3"

        self.queue = DeliveryQueue(self.queue_root)
        self.registry = SessionRegistry(self.db_path)
        self.transport = MessageTransport(self.queue_root, self.registry)

        # Register two agent sessions
        self.agent1 = self.registry.register(
            agent_name="agent-builder",
            role=Role.BUILDER,
            provider=Provider.CLAUDE,
            tmux_session="diamond-agent-agent-builder",
            tmux_pane="0",
            capabilities=("read", "write", "execute"),
        )
        self.agent2 = self.registry.register(
            agent_name="agent-researcher",
            role=Role.RESEARCHER,
            provider=Provider.CODEX,
            tmux_session="diamond-agent-agent-researcher",
            tmux_pane="0",
            capabilities=("read", "search", "analyze"),
        )

    def test_correlated_task_response_cycle(self) -> None:
        """Test a complete correlated task/response cycle between two agents."""
        # Agent 1 (builder) sends a task to Agent 2 (researcher)
        task_payload = {
            "type": "task_request",
            "task_id": "task-001",
            "objective": "Research authentication patterns",
            "acceptance_conditions": ["At least 3 patterns documented", "Code examples included"],
        }

        sent_msg = self.transport.send(
            sender_session_id=self.agent1.session_id,
            recipient_agent=self.agent2.agent_name,
            recipient_pane="0",
            payload=task_payload,
        )

        correlation_id = sent_msg.correlation_id

        # Agent 2 receives the task
        received = self.transport.receive(
            recipient_session_id=self.agent2.session_id,
            pane="0",
        )

        self.assertEqual(len(received), 1)
        task_msg = received[0]
        self.assertEqual(task_msg.correlation_id, correlation_id)
        self.assertEqual(task_msg.payload, task_payload)
        self.assertEqual(task_msg.sender_session, self.agent1.session_id)

        # Agent 2 processes and sends response
        response_payload = {
            "type": "task_response",
            "task_id": "task-001",
            "status": "completed",
            "result": {
                "patterns_found": ["JWT with refresh", "Session cookies", "API keys"],
                "code_examples": ["example1.py", "example2.py"],
            },
        }

        response_msg = self.transport.send(
            sender_session_id=self.agent2.session_id,
            recipient_agent=self.agent1.agent_name,
            recipient_pane="0",
            payload=response_payload,
        )

        # Agent 1 receives response
        responses = self.transport.receive(
            recipient_session_id=self.agent1.session_id,
            pane="0",
        )

        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0].payload, response_payload)

        # Agent 1 acknowledges the original task request
        ack_result = self.transport.acknowledge(
            recipient_session_id=self.agent2.session_id,
            pane="0",
            correlation_id=correlation_id,
        )
        self.assertTrue(ack_result)

        # Verify delivery state
        state = self.transport.get_delivery_state(
            recipient_session_id=self.agent2.session_id,
            pane="0",
            correlation_id=correlation_id,
        )
        self.assertEqual(state, DeliveryState.ACKNOWLEDGED)

    def test_duplicate_message_handling(self) -> None:
        """Test duplicate message handling with idempotency."""
        payload = {"type": "notification", "data": "test"}

        # Send same message twice with same idempotency key
        idempotency_key = "idem-test-123"

        msg1 = self.transport.send(
            sender_session_id=self.agent1.session_id,
            recipient_agent=self.agent2.agent_name,
            recipient_pane="0",
            payload=payload,
            idempotency_key=idempotency_key,
        )

        msg2 = self.transport.send(
            sender_session_id=self.agent1.session_id,
            recipient_agent=self.agent2.agent_name,
            recipient_pane="0",
            payload=payload,
            idempotency_key=idempotency_key,
        )

        # Should be same message (same correlation_id and idempotency_key)
        self.assertEqual(msg1.correlation_id, msg2.correlation_id)
        self.assertEqual(msg1.idempotency_key, msg2.idempotency_key)

        # Only one message in queue
        received = self.transport.receive(
            recipient_session_id=self.agent2.session_id,
            pane="0",
        )
        self.assertEqual(len(received), 1)

    def test_failure_recovery_scenario(self) -> None:
        """Test failure recovery - message redelivery after crash simulation."""
        payload = {"type": "task", "task_id": "task-002", "data": "important work"}

        # Send message
        msg = self.transport.send(
            sender_session_id=self.agent1.session_id,
            recipient_agent=self.agent2.agent_name,
            recipient_pane="0",
            payload=payload,
        )

        # Simulate agent 2 crashing after receiving but before acknowledging
        received = self.transport.receive(
            recipient_session_id=self.agent2.session_id,
            pane="0",
        )
        self.assertEqual(len(received), 1)

        # Update state to DELIVERING (simulating processing started)
        queued_msgs = self.queue.list(self.agent2.agent_name, "0")
        self.queue.update_state(self.agent2.agent_name, "0", queued_msgs[0].id, DeliveryState.DELIVERING)

        # Now simulate crash recovery - agent restarts and checks for unacknowledged messages
        # In real implementation, agent would query for QUEUED or DELIVERING messages
        unacked = self.transport.receive(
            recipient_session_id=self.agent2.session_id,
            pane="0",
            state=DeliveryState.DELIVERING,
        )
        self.assertEqual(len(unacked), 1)
        self.assertEqual(unacked[0].correlation_id, msg.correlation_id)

        # Agent processes and acknowledges
        self.transport.acknowledge(
            recipient_session_id=self.agent2.session_id,
            pane="0",
            correlation_id=msg.correlation_id,
        )

        # Verify acknowledged
        state = self.transport.get_delivery_state(
            recipient_session_id=self.agent2.session_id,
            pane="0",
            correlation_id=msg.correlation_id,
        )
        self.assertEqual(state, DeliveryState.ACKNOWLEDGED)

    def test_cancellation_handling(self) -> None:
        """Test cancellation request handling."""
        payload = {"type": "long_task", "task_id": "task-003"}

        msg = self.transport.send(
            sender_session_id=self.agent1.session_id,
            recipient_agent=self.agent2.agent_name,
            recipient_pane="0",
            payload=payload,
        )

        # Agent 1 requests cancellation
        cancelled = self.transport.request_cancellation(
            recipient_session_id=self.agent2.session_id,
            pane="0",
            correlation_id=msg.correlation_id,
        )
        self.assertTrue(cancelled)

        # Verify cancellation requested
        queued_msgs = self.queue.list(self.agent2.agent_name, "0")
        self.assertTrue(self.queue.is_cancellation_requested(self.agent2.agent_name, "0", queued_msgs[0].id))

        # Agent 2 checks for cancellation before processing
        is_cancelled = self.queue.is_cancellation_requested(
            self.agent2.agent_name, "0", queued_msgs[0].id
        )
        self.assertTrue(is_cancelled)

    def test_multiple_concurrent_tasks(self) -> None:
        """Test multiple concurrent tasks between agents."""
        task_ids = [f"task-{i:03d}" for i in range(10)]

        # Agent 1 sends 10 tasks
        for task_id in task_ids:
            self.transport.send(
                sender_session_id=self.agent1.session_id,
                recipient_agent=self.agent2.agent_name,
                recipient_pane="0",
                payload={"type": "task", "task_id": task_id},
            )

        # Agent 2 receives all
        received = self.transport.receive(
            recipient_session_id=self.agent2.session_id,
            pane="0",
        )
        self.assertEqual(len(received), 10)

        # Agent 2 processes and acknowledges each
        for task_msg in received:
            self.transport.acknowledge(
                recipient_session_id=self.agent2.session_id,
                pane="0",
                correlation_id=task_msg.correlation_id,
            )

        # All should be acknowledged
        for task_msg in received:
            state = self.transport.get_delivery_state(
                recipient_session_id=self.agent2.session_id,
                pane="0",
                correlation_id=task_msg.correlation_id,
            )
            self.assertEqual(state, DeliveryState.ACKNOWLEDGED)

    def test_session_registry_task_ownership(self) -> None:
        """Test session registry tracks task ownership."""
        # Agent 1 claims a task
        updated = self.registry.claim_task(self.agent1.session_id, "task-shared", expected_revision=1)
        self.assertIn("task-shared", updated.owned_task_ids)

        # Agent 2 claims a different task (same task ID but different session - allowed)
        updated2 = self.registry.claim_task(self.agent2.session_id, "task-other", expected_revision=1)
        self.assertIn("task-other", updated2.owned_task_ids)

        # Agent 1 releases task
        released = self.registry.release_task(self.agent1.session_id, "task-shared", expected_revision=2)
        self.assertNotIn("task-shared", released.owned_task_ids)

    def test_delivery_state_transitions(self) -> None:
        """Test all delivery state transitions."""
        payload = {"type": "test"}

        msg = self.transport.send(
            sender_session_id=self.agent1.session_id,
            recipient_agent=self.agent2.agent_name,
            recipient_pane="0",
            payload=payload,
        )

        # Initial state
        state = self.transport.get_delivery_state(
            recipient_session_id=self.agent2.session_id,
            pane="0",
            correlation_id=msg.correlation_id,
        )
        self.assertEqual(state, DeliveryState.QUEUED)

        # Simulate state transitions
        queued_msgs = self.queue.list(self.agent2.agent_name, "0")
        msg_id = queued_msgs[0].id

        for state_value in [
            DeliveryState.DELIVERING,
            DeliveryState.PASTING,
            DeliveryState.DRAFTED,
            DeliveryState.SUBMITTED,
            DeliveryState.DELIVERED_UNVERIFIED,
        ]:
            self.queue.update_state(self.agent2.agent_name, "0", msg_id, state_value)
            state = self.transport.get_delivery_state(
                recipient_session_id=self.agent2.session_id,
                pane="0",
                correlation_id=msg.correlation_id,
            )
            self.assertEqual(state, state_value)

        # Final acknowledgment
        self.transport.acknowledge(
            recipient_session_id=self.agent2.session_id,
            pane="0",
            correlation_id=msg.correlation_id,
        )

        state = self.transport.get_delivery_state(
            recipient_session_id=self.agent2.session_id,
            pane="0",
            correlation_id=msg.correlation_id,
        )
        self.assertEqual(state, DeliveryState.ACKNOWLEDGED)


class TestPersistenceAcrossRestarts(ProjectCase):
    """Test that queue state persists across restarts."""

    def setUp(self) -> None:
        super().setUp()
        self.queue_root = self.base / "queue"

    def test_queue_persists_after_recreate(self) -> None:
        """Test queue data persists after recreating queue instance."""
        from diamond.transport import DeliveryQueue, MessageTransport, SessionRegistry

        db_path = self.base / "sessions.sqlite3"
        registry1 = SessionRegistry(db_path)
        a1 = registry1.register("a1", Role.BUILDER, Provider.CLAUDE, "s1", "0")
        a2 = registry1.register("a2", Role.RESEARCHER, Provider.CODEX, "s2", "0")

        transport1 = MessageTransport(self.queue_root, registry1)

        msg = transport1.send(
            sender_session_id=a1.session_id,
            recipient_agent=a2.agent_name,
            recipient_pane="0",
            payload={"data": "persistent"},
        )

        # Recreate instances
        registry2 = SessionRegistry(db_path)
        transport2 = MessageTransport(self.queue_root, registry2)

        # Message should still be there
        received = transport2.receive(
            recipient_session_id=a2.session_id,
            pane="0",
        )
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].payload, {"data": "persistent"})
        self.assertEqual(received[0].correlation_id, msg.correlation_id)

    def test_session_registry_persists(self) -> None:
        """Test session registry persists after recreate."""
        registry1 = SessionRegistry(self.base / "sessions.sqlite3")
        record = registry1.register("agent-persist", Role.BUILDER, Provider.CLAUDE, "s1", "0")

        registry2 = SessionRegistry(self.base / "sessions.sqlite3")
        retrieved = registry2.get(record.session_id)

        self.assertEqual(retrieved.session_id, record.session_id)
        self.assertEqual(retrieved.agent_name, "agent-persist")
        self.assertEqual(retrieved.capabilities, record.capabilities)

    def test_acknowledged_state_persists(self) -> None:
        """Test acknowledged state persists."""
        from diamond.transport import DeliveryQueue, MessageTransport, SessionRegistry

        queue_root = self.base / "queue"
        db_path = self.base / "sessions.sqlite3"
        registry1 = SessionRegistry(db_path)
        a1 = registry1.register("a1", Role.BUILDER, Provider.CLAUDE, "s1", "0")
        a2 = registry1.register("a2", Role.RESEARCHER, Provider.CODEX, "s2", "0")

        transport1 = MessageTransport(queue_root, registry1)

        msg = transport1.send(
            sender_session_id=a1.session_id,
            recipient_agent=a2.agent_name,
            recipient_pane="0",
            payload={"data": "ack-test"},
        )

        transport1.acknowledge(
            recipient_session_id=a2.session_id,
            pane="0",
            correlation_id=msg.correlation_id,
        )

        # Recreate
        registry2 = SessionRegistry(db_path)
        transport2 = MessageTransport(queue_root, registry2)

        state = transport2.get_delivery_state(
            recipient_session_id=a2.session_id,
            pane="0",
            correlation_id=msg.correlation_id,
        )
        self.assertEqual(state, DeliveryState.ACKNOWLEDGED)