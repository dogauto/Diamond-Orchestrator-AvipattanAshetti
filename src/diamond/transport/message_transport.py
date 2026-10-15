"""Message transport layer wrapping the durable delivery queue.

Provides higher-level send/receive/acknowledge operations with correlation IDs,
thread spill for large payloads, and integration with the session registry.
"""

import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .delivery_queue import DeliveryQueue, DeliveryState, QueuedMessage, IdempotencyConflictError, LeaseError, QueueError
from .session_registry import SessionRegistry, SessionRecord
from ..errors import NotFoundError, ValidationError
from ..validation import bounded_int, text


class TransportError(Exception):
    """Transport operation error."""
    pass


class CorrelationError(TransportError):
    """Correlation ID mismatch or missing."""
    pass


@dataclass(frozen=True)
class TransportMessage:
    """A message with transport metadata."""
    correlation_id: str
    sender_session: str
    recipient_agent: str
    recipient_pane: str
    payload: dict[str, Any]
    idempotency_key: str
    created_at: str
    thread_spill_path: str | None = None

    def to_json(self) -> str:
        return json.dumps({
            "correlation_id": self.correlation_id,
            "sender_session": self.sender_session,
            "recipient_agent": self.recipient_agent,
            "recipient_pane": self.recipient_pane,
            "payload": self.payload,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at,
            "thread_spill_path": self.thread_spill_path,
        }, separators=(",", ":"))

    @classmethod
    def from_json(cls, data: str) -> "TransportMessage":
        obj = json.loads(data)
        return cls(
            correlation_id=obj["correlation_id"],
            sender_session=obj["sender_session"],
            recipient_agent=obj["recipient_agent"],
            recipient_pane=obj["recipient_pane"],
            payload=obj["payload"],
            idempotency_key=obj["idempotency_key"],
            created_at=obj["created_at"],
            thread_spill_path=obj.get("thread_spill_path"),
        )


class MessageTransport:
    """High-level message transport with correlation tracking and thread spill."""

    THREAD_SPILL_THRESHOLD = 2048  # bytes

    def __init__(
        self,
        queue_root: Path,
        session_registry: SessionRegistry,
        lease_timeout_ms: int = 5000,
        max_payload_bytes: int = 65536,
    ) -> None:
        if not isinstance(queue_root, Path) or not queue_root.is_absolute():
            raise ValidationError("queue_root must be an absolute pathlib.Path")

        self.queue_root = queue_root
        self.session_registry = session_registry
        self.queue = DeliveryQueue(queue_root, lease_timeout_ms, max_payload_bytes)
        self.max_payload_bytes = max_payload_bytes

        # Thread spill directory
        self.spill_root = queue_root / ".thread-spill"
        self.spill_root.mkdir(parents=True, exist_ok=True)

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _write_thread_spill(self, content: str) -> str:
        """Write large payload to thread spill file, return relative path."""
        spill_id = uuid.uuid4().hex[:16]
        spill_path = self.spill_root / f"{spill_id}.md"
        spill_path.write_text(content, encoding="utf-8")
        return f".thread-spill/{spill_id}.md"

    def _read_thread_spill(self, relative_path: str) -> str:
        """Read content from thread spill file."""
        spill_path = self.queue_root / relative_path
        if not spill_path.exists():
            raise TransportError(f"thread spill file not found: {relative_path}")
        return spill_path.read_text(encoding="utf-8")

    def send(
        self,
        sender_session_id: str,
        recipient_agent: str,
        recipient_pane: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> TransportMessage:
        """Send a message to a recipient agent/pane.

        Handles thread spill for large payloads automatically.
        """
        # Validate sender session exists
        sender = self.session_registry.get(sender_session_id)

        # Generate correlation ID if not provided
        if correlation_id is None:
            correlation_id = f"corr-{uuid.uuid4().hex[:16]}"
        else:
            text(correlation_id, "correlation_id", 128)

        # Serialize payload
        payload_json = json.dumps(payload, separators=(",", ":"))
        if len(payload_json.encode("utf-8")) > self.max_payload_bytes:
            raise ValidationError(f"payload exceeds max size of {self.max_payload_bytes} bytes")

        # Handle thread spill
        thread_spill_path = None
        if len(payload_json) > self.THREAD_SPILL_THRESHOLD:
            thread_spill_path = self._write_thread_spill(payload_json)
            # Send reference instead
            payload_json = json.dumps({"__thread_spill__": thread_spill_path}, separators=(",", ":"))

        # Generate idempotency key if not provided
        if idempotency_key is None:
            idempotency_key = f"{sender_session_id}:{recipient_agent}:{recipient_pane}:{correlation_id}:{self._now_iso()}"

        # Enqueue via delivery queue
        try:
            queued = self.queue.enqueue(
                agent_name=recipient_agent,
                pane=recipient_pane,
                message_text=payload_json,
                idempotency_key=idempotency_key,
                metadata={
                    "correlation_id": correlation_id,
                    "sender_session": sender_session_id,
                    "thread_spill": thread_spill_path is not None,
                },
            )
        except IdempotencyConflictError as exc:
            raise CorrelationError(str(exc)) from exc
        except LeaseError as exc:
            raise TransportError(f"lease failed: {exc}") from exc
        except QueueError as exc:
            raise TransportError(f"enqueue failed: {exc}") from exc

        # If the message was deduplicated, use the existing correlation_id
        existing_correlation = queued.metadata.get("correlation_id", correlation_id)
        if existing_correlation != correlation_id:
            correlation_id = existing_correlation

        message = TransportMessage(
            correlation_id=correlation_id,
            sender_session=sender_session_id,
            recipient_agent=recipient_agent,
            recipient_pane=recipient_pane,
            payload=payload,
            idempotency_key=idempotency_key,
            created_at=queued.created_at,
            thread_spill_path=thread_spill_path,
        )

        return message

    def receive(
        self,
        recipient_session_id: str,
        pane: str,
        state: DeliveryState = DeliveryState.QUEUED,
    ) -> list[TransportMessage]:
        """Receive messages for a session/pane."""
        recipient = self.session_registry.get(recipient_session_id)

        messages = self.queue.list(recipient.agent_name, pane, state)
        result = []

        for queued in messages:
            # Reconstruct transport message
            meta = queued.metadata
            correlation_id = meta.get("correlation_id", "")
            sender_session = meta.get("sender_session", "")
            thread_spill = meta.get("thread_spill", False)

            # Read payload, handling thread spill
            payload_text = queued.text
            if thread_spill:
                # Find the spill path from the payload
                try:
                    spill_ref = json.loads(payload_text)
                    spill_path = spill_ref.get("__thread_spill__")
                    if spill_path:
                        payload_text = self._read_thread_spill(spill_path)
                except (json.JSONDecodeError, KeyError):
                    pass

            try:
                payload = json.loads(payload_text)
            except json.JSONDecodeError:
                payload = {"__raw__": payload_text}

            result.append(TransportMessage(
                correlation_id=correlation_id,
                sender_session=sender_session,
                recipient_agent=recipient.agent_name,
                recipient_pane=pane,
                payload=payload,
                idempotency_key=queued.idempotency_key,
                created_at=queued.created_at,
                thread_spill_path=meta.get("thread_spill_path"),
            ))

        return result

    def acknowledge(
        self,
        recipient_session_id: str,
        pane: str,
        correlation_id: str,
    ) -> bool:
        """Acknowledge a message by correlation ID."""
        recipient = self.session_registry.get(recipient_session_id)

        messages = self.queue.list(recipient.agent_name, pane)
        for queued in messages:
            if queued.metadata.get("correlation_id") == correlation_id:
                if queued.state == DeliveryState.ACKNOWLEDGED:
                    return True  # Already acknowledged
                # Transition to ACKNOWLEDGED regardless of current state
                self.queue.update_state(recipient.agent_name, pane, queued.id, DeliveryState.ACKNOWLEDGED)
                return True

        raise NotFoundError(f"message with correlation_id {correlation_id} not found for {recipient.agent_name}:{pane}")

    def get_delivery_state(
        self,
        recipient_session_id: str,
        pane: str,
        correlation_id: str,
    ) -> DeliveryState:
        """Get the delivery state of a message by correlation ID."""
        recipient = self.session_registry.get(recipient_session_id)

        messages = self.queue.list(recipient.agent_name, pane)
        for queued in messages:
            if queued.metadata.get("correlation_id") == correlation_id:
                return queued.state

        raise NotFoundError(f"message with correlation_id {correlation_id} not found")

    def request_cancellation(
        self,
        recipient_session_id: str,
        pane: str,
        correlation_id: str,
    ) -> bool:
        """Request cancellation of a message by correlation ID."""
        recipient = self.session_registry.get(recipient_session_id)

        messages = self.queue.list(recipient.agent_name, pane)
        for queued in messages:
            if queued.metadata.get("correlation_id") == correlation_id:
                if queued.state in (DeliveryState.ACKNOWLEDGED, DeliveryState.DELIVERED_UNVERIFIED):
                    return False  # Already terminal
                self.queue.request_cancellation(recipient.agent_name, pane, queued.id)
                return True

        raise NotFoundError(f"message with correlation_id {correlation_id} not found")

    @contextmanager
    def delivery_context(self, correlation_id: str):
        """Context manager for tracking delivery lifecycle."""
        # Could be extended with metrics, tracing, etc.
        yield

    def prune_acknowledged(self, older_than_ms: int = 7 * 24 * 3600 * 1000) -> int:
        """Prune acknowledged messages older than threshold.

        Default: 7 days.
        """
        return self.queue.prune(older_than_ms)