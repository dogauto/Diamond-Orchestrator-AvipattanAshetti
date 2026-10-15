"""Tests for the message transport layer using unittest conventions."""

import json
import tempfile
from pathlib import Path

from support import ProjectCase

from diamond.domain import Provider, Role
from diamond.transport import DeliveryQueue, DeliveryState, MessageTransport, SessionRegistry


class MessageTransportTests(ProjectCase):
    """Tests for MessageTransport."""

    def setUp(self) -> None:
        super().setUp()
        self.queue_root = self.base / "queue"
        self.db_path = self.base / "sessions.sqlite3"

        self.queue = DeliveryQueue(self.queue_root)
        self.registry = SessionRegistry(self.db_path)
        self.transport = MessageTransport(self.queue_root, self.registry)

        # Register sender and recipient sessions
        self.sender = self.registry.register(
            agent_name="sender-agent",
            role=Role.BUILDER,
            provider=Provider.CLAUDE,
            tmux_session="diamond-agent-sender-agent",
            tmux_pane="0",
        )
        self.recipient = self.registry.register(
            agent_name="recipient-agent",
            role=Role.RESEARCHER,
            provider=Provider.CODEX,
            tmux_session="diamond-agent-recipient-agent",
            tmux_pane="0",
        )

    def test_send_and_receive(self) -> None:
        """Test basic send and receive."""
        payload = {"type": "task", "data": {"objective": "Build something"}}

        msg = self.transport.send(
            sender_session_id=self.sender.session_id,
            recipient_agent=self.recipient.agent_name,
            recipient_pane="0",
            payload=payload,
        )

        self.assertIsNotNone(msg.correlation_id)
        self.assertEqual(msg.sender_session, self.sender.session_id)
        self.assertEqual(msg.recipient_agent, self.recipient.agent_name)
        self.assertEqual(msg.payload, payload)

        # Receive
        received = self.transport.receive(
            recipient_session_id=self.recipient.session_id,
            pane="0",
        )

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].correlation_id, msg.correlation_id)
        self.assertEqual(received[0].payload, payload)

    def test_send_with_correlation_id(self) -> None:
        """Test send with explicit correlation ID."""
        correlation_id = "custom-corr-123"
        msg = self.transport.send(
            sender_session_id=self.sender.session_id,
            recipient_agent=self.recipient.agent_name,
            recipient_pane="0",
            payload={"data": "test"},
            correlation_id=correlation_id,
        )

        self.assertEqual(msg.correlation_id, correlation_id)

    def test_acknowledge(self) -> None:
        """Test acknowledging a message."""
        msg = self.transport.send(
            sender_session_id=self.sender.session_id,
            recipient_agent=self.recipient.agent_name,
            recipient_pane="0",
            payload={"data": "test"},
        )

        # Acknowledge
        result = self.transport.acknowledge(
            recipient_session_id=self.recipient.session_id,
            pane="0",
            correlation_id=msg.correlation_id,
        )

        self.assertTrue(result)

        # Check state
        state = self.transport.get_delivery_state(
            recipient_session_id=self.recipient.session_id,
            pane="0",
            correlation_id=msg.correlation_id,
        )
        self.assertEqual(state, DeliveryState.ACKNOWLEDGED)

    def test_get_delivery_state(self) -> None:
        """Test getting delivery state."""
        msg = self.transport.send(
            sender_session_id=self.sender.session_id,
            recipient_agent=self.recipient.agent_name,
            recipient_pane="0",
            payload={"data": "test"},
        )

        state = self.transport.get_delivery_state(
            recipient_session_id=self.recipient.session_id,
            pane="0",
            correlation_id=msg.correlation_id,
        )
        self.assertEqual(state, DeliveryState.QUEUED)

    def test_request_cancellation(self) -> None:
        """Test requesting cancellation."""
        msg = self.transport.send(
            sender_session_id=self.sender.session_id,
            recipient_agent=self.recipient.agent_name,
            recipient_pane="0",
            payload={"data": "test"},
        )

        result = self.transport.request_cancellation(
            recipient_session_id=self.recipient.session_id,
            pane="0",
            correlation_id=msg.correlation_id,
        )

        self.assertTrue(result)

        # Check cancellation was requested
        queued_msgs = self.queue.list(self.recipient.agent_name, "0")
        self.assertEqual(len(queued_msgs), 1)
        self.assertTrue(self.queue.is_cancellation_requested(self.recipient.agent_name, "0", queued_msgs[0].id))

    def test_cancellation_ignored_for_acknowledged(self) -> None:
        """Test cancellation ignored for acknowledged messages."""
        msg = self.transport.send(
            sender_session_id=self.sender.session_id,
            recipient_agent=self.recipient.agent_name,
            recipient_pane="0",
            payload={"data": "test"},
        )

        self.transport.acknowledge(
            recipient_session_id=self.recipient.session_id,
            pane="0",
            correlation_id=msg.correlation_id,
        )

        result = self.transport.request_cancellation(
            recipient_session_id=self.recipient.session_id,
            pane="0",
            correlation_id=msg.correlation_id,
        )

        self.assertFalse(result)  # Ignored, already terminal

    def test_thread_spill_large_payload(self) -> None:
        """Test thread spill for large payloads."""
        # Create a payload larger than THREAD_SPILL_THRESHOLD (2048 bytes)
        large_data = "x" * 3000
        payload = {"large_field": large_data}

        msg = self.transport.send(
            sender_session_id=self.sender.session_id,
            recipient_agent=self.recipient.agent_name,
            recipient_pane="0",
            payload=payload,
        )

        self.assertIsNotNone(msg.thread_spill_path)
        self.assertTrue(msg.thread_spill_path.startswith(".thread-spill/"))

        # Receive should reconstruct the full payload
        received = self.transport.receive(
            recipient_session_id=self.recipient.session_id,
            pane="0",
        )

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].payload, payload)
        self.assertEqual(received[0].payload["large_field"], large_data)

    def test_thread_spill_file_created(self) -> None:
        """Test thread spill file is actually created."""
        large_data = "x" * 3000
        payload = {"large_field": large_data}

        msg = self.transport.send(
            sender_session_id=self.sender.session_id,
            recipient_agent=self.recipient.agent_name,
            recipient_pane="0",
            payload=payload,
        )

        spill_path = self.queue_root / msg.thread_spill_path
        self.assertTrue(spill_path.exists())
        self.assertEqual(spill_path.read_text(), json.dumps(payload, separators=(",", ":")))

    def test_multiple_messages_same_recipient(self) -> None:
        """Test multiple messages to same recipient."""
        self.transport.send(
            sender_session_id=self.sender.session_id,
            recipient_agent=self.recipient.agent_name,
            recipient_pane="0",
            payload={"msg": 1},
        )
        self.transport.send(
            sender_session_id=self.sender.session_id,
            recipient_agent=self.recipient.agent_name,
            recipient_pane="0",
            payload={"msg": 2},
        )
        self.transport.send(
            sender_session_id=self.sender.session_id,
            recipient_agent=self.recipient.agent_name,
            recipient_pane="0",
            payload={"msg": 3},
        )

        received = self.transport.receive(
            recipient_session_id=self.recipient.session_id,
            pane="0",
        )

        self.assertEqual(len(received), 3)

    def test_receive_filtered_by_state(self) -> None:
        """Test receiving filtered by state."""
        msg1 = self.transport.send(
            sender_session_id=self.sender.session_id,
            recipient_agent=self.recipient.agent_name,
            recipient_pane="0",
            payload={"msg": 1},
        )
        msg2 = self.transport.send(
            sender_session_id=self.sender.session_id,
            recipient_agent=self.recipient.agent_name,
            recipient_pane="0",
            payload={"msg": 2},
        )

        # Acknowledge one
        self.transport.acknowledge(
            recipient_session_id=self.recipient.session_id,
            pane="0",
            correlation_id=msg1.correlation_id,
        )

        queued = self.transport.receive(
            recipient_session_id=self.recipient.session_id,
            pane="0",
            state=DeliveryState.QUEUED,
        )
        acked = self.transport.receive(
            recipient_session_id=self.recipient.session_id,
            pane="0",
            state=DeliveryState.ACKNOWLEDGED,
        )

        self.assertEqual(len(queued), 1)
        self.assertEqual(len(acked), 1)

    def test_different_panes_isolated(self) -> None:
        """Test different panes have isolated queues."""
        self.transport.send(
            sender_session_id=self.sender.session_id,
            recipient_agent=self.recipient.agent_name,
            recipient_pane="0",
            payload={"pane": 0},
        )
        self.transport.send(
            sender_session_id=self.sender.session_id,
            recipient_agent=self.recipient.agent_name,
            recipient_pane="1",
            payload={"pane": 1},
        )

        pane0 = self.transport.receive(
            recipient_session_id=self.recipient.session_id,
            pane="0",
        )
        pane1 = self.transport.receive(
            recipient_session_id=self.recipient.session_id,
            pane="1",
        )

        self.assertEqual(len(pane0), 1)
        self.assertEqual(pane0[0].payload["pane"], 0)
        self.assertEqual(len(pane1), 1)
        self.assertEqual(pane1[0].payload["pane"], 1)

    def test_prune_acknowledged(self) -> None:
        """Test pruning acknowledged messages."""
        msg = self.transport.send(
            sender_session_id=self.sender.session_id,
            recipient_agent=self.recipient.agent_name,
            recipient_pane="0",
            payload={"data": "test"},
        )

        self.transport.acknowledge(
            recipient_session_id=self.recipient.session_id,
            pane="0",
            correlation_id=msg.correlation_id,
        )

        pruned = self.transport.prune_acknowledged(older_than_ms=0)
        self.assertEqual(pruned, 1)

        # Message should be gone
        received = self.transport.receive(
            recipient_session_id=self.recipient.session_id,
            pane="0",
        )
        self.assertEqual(len(received), 0)

    def test_sender_session_validation(self) -> None:
        """Test sender session validation."""
        with self.assertRaises(Exception):  # NotFoundError
            self.transport.send(
                sender_session_id="nonexistent-session",
                recipient_agent=self.recipient.agent_name,
                recipient_pane="0",
                payload={"data": "test"},
            )

    def test_recipient_session_validation(self) -> None:
        """Test recipient session validation."""
        # Create a different agent
        other = self.registry.register(
            agent_name="other-agent",
            role=Role.QA,
            provider=Provider.GEMINI,
            tmux_session="diamond-agent-other-agent",
            tmux_pane="0",
        )

        # Try to receive as other-agent - should fail because pane "0" has no messages
        # but the session itself is valid; we verify it gets an empty list
        received = self.transport.receive(
            recipient_session_id=other.session_id,
            pane="0",
        )
        self.assertEqual(len(received), 0)

    def test_transport_message_serialization(self) -> None:
        """Test TransportMessage serialization."""
        from diamond.transport.message_transport import TransportMessage

        msg = TransportMessage(
            correlation_id="corr-123",
            sender_session="sender-1",
            recipient_agent="recipient-1",
            recipient_pane="0",
            payload={"key": "value"},
            idempotency_key="idem-123",
            created_at="2024-01-01T00:00:00+00:00",
            thread_spill_path=".thread-spill/abc.md",
        )

        json_str = msg.to_json()
        parsed = TransportMessage.from_json(json_str)

        self.assertEqual(parsed.correlation_id, msg.correlation_id)
        self.assertEqual(parsed.sender_session, msg.sender_session)
        self.assertEqual(parsed.recipient_agent, msg.recipient_agent)
        self.assertEqual(parsed.payload, msg.payload)
        self.assertEqual(parsed.thread_spill_path, msg.thread_spill_path)