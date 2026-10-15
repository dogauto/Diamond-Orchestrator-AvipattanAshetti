"""Tests for the durable delivery queue using unittest conventions."""

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from support import ProjectCase

from diamond.transport.delivery_queue import (
    DeliveryQueue,
    DeliveryState,
    QueuedMessage,
    QueueError,
    LeaseError,
    IdempotencyConflictError,
)


class DeliveryQueueTests(ProjectCase):
    """Tests for DeliveryQueue."""

    def setUp(self) -> None:
        super().setUp()
        self.queue_root = self.base / "queue"
        self.queue = DeliveryQueue(self.queue_root)

    def test_enqueue_and_read(self) -> None:
        """Test basic enqueue and read."""
        msg = self.queue.enqueue("agent1", "pane1", "Hello, world!")

        self.assertEqual(msg.agent_name, "agent1")
        self.assertEqual(msg.pane, "pane1")
        self.assertEqual(msg.text, "Hello, world!")
        self.assertEqual(msg.state, DeliveryState.QUEUED)
        self.assertIsNotNone(msg.idempotency_key)

        # Read it back
        read_msg = self.queue.read("agent1", "pane1", msg.id)
        self.assertEqual(read_msg.id, msg.id)
        self.assertEqual(read_msg.text, "Hello, world!")

    def test_enqueue_with_custom_idempotency_key(self) -> None:
        """Test enqueue with custom idempotency key."""
        msg = self.queue.enqueue(
            "agent1", "pane1", "Hello",
            idempotency_key="custom-key-123"
        )
        self.assertEqual(msg.idempotency_key, "custom-key-123")

        # Enqueue again with same key - should return same message
        msg2 = self.queue.enqueue(
            "agent1", "pane1", "Hello",
            idempotency_key="custom-key-123"
        )
        self.assertEqual(msg2.id, msg.id)

    def test_idempotency_conflict_different_text(self) -> None:
        """Test idempotency conflict with different text."""
        self.queue.enqueue("agent1", "pane1", "Hello", idempotency_key="key1")

        with self.assertRaises(IdempotencyConflictError):
            self.queue.enqueue("agent1", "pane1", "Different", idempotency_key="key1")

    def test_list_messages(self) -> None:
        """Test listing messages."""
        self.queue.enqueue("agent1", "pane1", "Msg 1")
        self.queue.enqueue("agent1", "pane1", "Msg 2")
        self.queue.enqueue("agent1", "pane2", "Msg 3")

        all_msgs = self.queue.list("agent1", "pane1")
        self.assertEqual(len(all_msgs), 2)

        pane2_msgs = self.queue.list("agent1", "pane2")
        self.assertEqual(len(pane2_msgs), 1)
        self.assertEqual(pane2_msgs[0].text, "Msg 3")

    def test_list_filtered_by_state(self) -> None:
        """Test listing filtered by state."""
        msg1 = self.queue.enqueue("agent1", "pane1", "Msg 1")
        self.queue.enqueue("agent1", "pane1", "Msg 2")

        # Acknowledge one
        self.queue.acknowledge("agent1", "pane1", msg1.id)

        queued = self.queue.list("agent1", "pane1", DeliveryState.QUEUED)
        acked = self.queue.list("agent1", "pane1", DeliveryState.ACKNOWLEDGED)

        self.assertEqual(len(queued), 1)
        self.assertEqual(len(acked), 1)

    def test_update_state(self) -> None:
        """Test updating message state."""
        msg = self.queue.enqueue("agent1", "pane1", "Hello")

        updated = self.queue.update_state("agent1", "pane1", msg.id, DeliveryState.DELIVERING)
        self.assertEqual(updated.state, DeliveryState.DELIVERING)
        # updated_at may be identical at sub-second resolution; verify state change is persisted
        self.assertGreaterEqual(updated.updated_at, msg.updated_at)

        # Verify persisted
        read = self.queue.read("agent1", "pane1", msg.id)
        self.assertEqual(read.state, DeliveryState.DELIVERING)

    def test_acknowledge(self) -> None:
        """Test acknowledging a message."""
        msg = self.queue.enqueue("agent1", "pane1", "Hello")

        acked = self.queue.acknowledge("agent1", "pane1", msg.id)
        self.assertEqual(acked.state, DeliveryState.ACKNOWLEDGED)

    def test_request_cancellation(self) -> None:
        """Test cancellation request."""
        msg = self.queue.enqueue("agent1", "pane1", "Hello")

        self.queue.request_cancellation("agent1", "pane1", msg.id)
        self.assertTrue(self.queue.is_cancellation_requested("agent1", "pane1", msg.id))

    def test_cancellation_ignored_for_terminal_states(self) -> None:
        """Test cancellation is ignored for terminal states."""
        msg = self.queue.enqueue("agent1", "pane1", "Hello")
        self.queue.acknowledge("agent1", "pane1", msg.id)

        # Should not raise
        self.queue.request_cancellation("agent1", "pane1", msg.id)
        self.assertFalse(self.queue.is_cancellation_requested("agent1", "pane1", msg.id))

    def test_prune_acknowledged(self) -> None:
        """Test pruning acknowledged messages."""
        msg1 = self.queue.enqueue("agent1", "pane1", "Old")
        msg2 = self.queue.enqueue("agent1", "pane1", "New")

        self.queue.acknowledge("agent1", "pane1", msg1.id)
        self.queue.acknowledge("agent1", "pane1", msg2.id)

        # Manually update msg1's updated_at to be old
        pane_dir = self.queue_root / "agent1" / "pane1"
        for path in pane_dir.glob("*.json"):
            if not path.name.startswith("."):
                data = json.loads(path.read_text())
                if data["id"] == msg1.id:
                    # Set updated_at to 8 days ago
                    old_time = time.time() - (8 * 24 * 3600)
                    from datetime import datetime, timezone
                    data["updated_at"] = datetime.fromtimestamp(old_time, timezone.utc).isoformat(timespec="seconds")
                    path.write_text(json.dumps(data))
                    break

        # Prune messages older than 7 days
        pruned = self.queue.prune(7 * 24 * 3600 * 1000)
        self.assertEqual(pruned, 1)

        # msg1 should be gone, msg2 should remain
        remaining = self.queue.list("agent1", "pane1")
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].id, msg2.id)

    def test_read_nonexistent(self) -> None:
        """Test reading nonexistent message raises."""
        with self.assertRaises(QueueError):
            self.queue.read("agent1", "pane1", "nonexistent")

    def test_concurrent_enqueue_same_idempotency(self) -> None:
        """Test concurrent enqueue with same idempotency key."""
        # This simulates two processes trying to enqueue the same message
        msg1 = self.queue.enqueue("agent1", "pane1", "Hello", idempotency_key="shared-key")
        msg2 = self.queue.enqueue("agent1", "pane1", "Hello", idempotency_key="shared-key")
        self.assertEqual(msg1.id, msg2.id)

    def test_metadata_persisted(self) -> None:
        """Test metadata is persisted with message."""
        msg = self.queue.enqueue(
            "agent1", "pane1", "Hello",
            metadata={"custom": "value", "number": 42}
        )
        self.assertEqual(msg.metadata["custom"], "value")
        self.assertEqual(msg.metadata["number"], 42)

        read = self.queue.read("agent1", "pane1", msg.id)
        self.assertEqual(read.metadata["custom"], "value")
        self.assertEqual(read.metadata["number"], 42)

    def test_multiple_agents_isolated(self) -> None:
        """Test multiple agents have isolated queues."""
        self.queue.enqueue("agent1", "pane1", "Agent 1 msg")
        self.queue.enqueue("agent2", "pane1", "Agent 2 msg")

        agent1_msgs = self.queue.list("agent1", "pane1")
        agent2_msgs = self.queue.list("agent2", "pane1")

        self.assertEqual(len(agent1_msgs), 1)
        self.assertEqual(agent1_msgs[0].text, "Agent 1 msg")
        self.assertEqual(len(agent2_msgs), 1)
        self.assertEqual(agent2_msgs[0].text, "Agent 2 msg")

    def test_delivery_state_enum_values(self) -> None:
        """Test all delivery state enum values."""
        states = [
            DeliveryState.QUEUED,
            DeliveryState.DELIVERING,
            DeliveryState.PASTING,
            DeliveryState.DRAFTED,
            DeliveryState.SUBMITTED,
            DeliveryState.DELIVERED_UNVERIFIED,
            DeliveryState.ACKNOWLEDGED,
        ]
        for state in states:
            msg = self.queue.enqueue("agent1", "pane1", f"Test {state.value}")
            updated = self.queue.update_state("agent1", "pane1", msg.id, state)
            self.assertEqual(updated.state, state)

    def test_payload_size_limit(self) -> None:
        """Test payload size limit is enforced."""
        large_text = "x" * 100000  # 100KB, exceeds default 64KB
        queue = DeliveryQueue(self.queue_root, max_payload_bytes=65536)

        with self.assertRaises(Exception):  # ValidationError from validation.text
            queue.enqueue("agent1", "pane1", large_text)


class QueuedMessageSerializationTests(ProjectCase):
    """Tests for QueuedMessage serialization."""

    def test_to_from_json(self) -> None:
        """Test JSON round-trip."""
        msg = QueuedMessage(
            id="test-id",
            agent_name="agent1",
            pane="pane1",
            text="Hello",
            idempotency_key="key1",
            state=DeliveryState.QUEUED,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
            metadata={"key": "value"},
        )

        json_str = msg.to_json()
        parsed = QueuedMessage.from_json(json_str)

        self.assertEqual(parsed.id, msg.id)
        self.assertEqual(parsed.agent_name, msg.agent_name)
        self.assertEqual(parsed.pane, msg.pane)
        self.assertEqual(parsed.text, msg.text)
        self.assertEqual(parsed.idempotency_key, msg.idempotency_key)
        self.assertEqual(parsed.state, msg.state)
        self.assertEqual(parsed.metadata, msg.metadata)