"""Durable message queue for inter-agent communication.

Python-only implementation of agentmux's delivery queue concepts:
- Atomic file enqueue via os.link() (equivalent to linkSync())
- Kernel file leases via fcntl.flock() (equivalent to flock -n -E 73)
- SHA-256 idempotency keys for message deduplication
- Sidecar .cancel-request files for cancellation
- 7-day pruning of acknowledged messages
"""

import contextlib
import fcntl
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from ..errors import StorageError, ValidationError
from ..validation import bounded_int, text as validate_text


class DeliveryState(str, Enum):
    """Delivery states matching the orchestration pipeline."""
    QUEUED = "QUEUED"
    DELIVERING = "DELIVERING"
    PASTING = "PASTING"
    DRAFTED = "DRAFTED"
    SUBMITTED = "SUBMITTED"
    DELIVERED_UNVERIFIED = "DELIVERED_UNVERIFIED"
    ACKNOWLEDGED = "ACKNOWLEDGED"


class QueueError(Exception):
    """Queue operation error."""
    pass


class LeaseError(QueueError):
    """File lease acquisition failed."""
    pass


class IdempotencyConflictError(QueueError):
    """Duplicate idempotency key with different payload."""
    pass


@dataclass(frozen=True)
class QueuedMessage:
    """A message in the delivery queue."""
    id: str
    agent_name: str
    pane: str
    text: str
    idempotency_key: str
    state: DeliveryState
    created_at: str
    updated_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({
            "id": self.id,
            "agent_name": self.agent_name,
            "pane": self.pane,
            "text": self.text,
            "idempotency_key": self.idempotency_key,
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }, separators=(",", ":"))

    @classmethod
    def from_json(cls, data: str) -> "QueuedMessage":
        obj = json.loads(data)
        return cls(
            id=obj["id"],
            agent_name=obj["agent_name"],
            pane=obj["pane"],
            text=obj["text"],
            idempotency_key=obj["idempotency_key"],
            state=DeliveryState(obj["state"]),
            created_at=obj["created_at"],
            updated_at=obj["updated_at"],
            metadata=obj.get("metadata", {}),
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


class DeliveryQueue:
    """Durable message queue with file-based storage and kernel leases."""

    def __init__(
        self,
        queue_root: Path,
        lease_timeout_ms: int = 5000,
        max_payload_bytes: int = 65536,
    ) -> None:
        if not isinstance(queue_root, Path) or not queue_root.is_absolute():
            raise ValidationError("queue_root must be an absolute pathlib.Path")
        bounded_int(lease_timeout_ms, "lease_timeout_ms", 1, 60_000)
        bounded_int(max_payload_bytes, "max_payload_bytes", 1, 10_000_000)

        self.queue_root = queue_root
        self.lease_timeout_ms = lease_timeout_ms
        self.max_payload_bytes = max_payload_bytes

        # Per-agent queues: queue_root / agent_name / pane / message-id.json
        self.queue_root.mkdir(parents=True, exist_ok=True)

    def _agent_dir(self, agent_name: str) -> Path:
        validate_text(agent_name, "agent_name", 256)
        return self.queue_root / agent_name

    def _pane_dir(self, agent_name: str, pane: str) -> Path:
        validate_text(pane, "pane", 256)
        return self._agent_dir(agent_name) / pane

    def _message_path(self, agent_name: str, pane: str, message_id: str) -> Path:
        validate_text(message_id, "message_id", 256)
        return self._pane_dir(agent_name, pane) / f"{message_id}.json"

    def _idempotency_path(self, agent_name: str, pane: str, idempotency_key: str) -> Path:
        # SHA-256 the idempotency key to avoid filesystem issues
        key_hash = _sha256_hex(idempotency_key)[:32]
        return self._pane_dir(agent_name, pane) / f".idem-{key_hash}"

    def _cancel_path(self, agent_name: str, pane: str, message_id: str) -> Path:
        return self._pane_dir(agent_name, pane) / f".{message_id}.cancel-request"

    def _lease_path(self, agent_name: str, pane: str) -> Path:
        return self._pane_dir(agent_name, pane) / ".lease"

    @contextlib.contextmanager
    def _file_lease(self, agent_name: str, pane: str):
        """Acquire exclusive kernel lease for a pane's queue directory."""
        pane_dir = self._pane_dir(agent_name, pane)
        pane_dir.mkdir(parents=True, exist_ok=True)
        lease_file = self._lease_path(agent_name, pane)

        fd = os.open(lease_file, os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            # Non-blocking exclusive lock with timeout via loop
            start = time.monotonic()
            timeout_s = self.lease_timeout_ms / 1000
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() - start > timeout_s:
                        raise LeaseError(
                            f"could not acquire lease for {agent_name}:{pane} within {self.lease_timeout_ms}ms"
                        )
                    time.sleep(0.01)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def enqueue(
        self,
        agent_name: str,
        pane: str,
        message_text: str,
        idempotency_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> QueuedMessage:
        """Enqueue a message atomically. Returns the queued message.

        If idempotency_key is provided, deduplicates against existing messages.
        """
        validate_text(message_text, "message text", self.max_payload_bytes)
        if idempotency_key is not None:
            validate_text(idempotency_key, "idempotency_key", 512)

        timestamp = _now_iso()
        message_id = uuid.uuid4().hex[:16]
        state = DeliveryState.QUEUED

        message = QueuedMessage(
            id=message_id,
            agent_name=agent_name,
            pane=pane,
            text=message_text,
            idempotency_key=idempotency_key or _sha256_hex(f"{agent_name}:{pane}:{message_text}:{timestamp}"),
            state=state,
            created_at=timestamp,
            updated_at=timestamp,
            metadata=metadata or {},
        )

        with self._file_lease(agent_name, pane):
            pane_dir = self._pane_dir(agent_name, pane)
            pane_dir.mkdir(parents=True, exist_ok=True)

            # Idempotency check
            if idempotency_key is not None:
                idem_path = self._idempotency_path(agent_name, pane, idempotency_key)
                if idem_path.exists():
                    existing_id = idem_path.read_text(encoding="utf-8").strip()
                    existing_msg = self.read(agent_name, pane, existing_id)
                    if existing_msg.text != message_text:
                        raise IdempotencyConflictError(
                            f"idempotency key {idempotency_key} already used for different message"
                        )
                    return existing_msg

            # Atomic write: write to temp file, then link into place
            temp_path = pane_dir / f".{message_id}.tmp"
            temp_path.write_text(message.to_json(), encoding="utf-8")
            final_path = self._message_path(agent_name, pane, message_id)

            try:
                os.link(temp_path, final_path)
            except FileExistsError:
                # Race: another process created the same message ID (extremely unlikely)
                temp_path.unlink(missing_ok=True)
                existing = self.read(agent_name, pane, message_id)
                return existing
            finally:
                temp_path.unlink(missing_ok=True)

            # Record idempotency key
            if idempotency_key is not None:
                self._idempotency_path(agent_name, pane, idempotency_key).write_text(
                    message_id, encoding="utf-8"
                )

        return message

    def read(self, agent_name: str, pane: str, message_id: str) -> QueuedMessage:
        """Read a message by ID."""
        path = self._message_path(agent_name, pane, message_id)
        if not path.exists():
            raise QueueError(f"message {message_id} not found for {agent_name}:{pane}")
        return QueuedMessage.from_json(path.read_text(encoding="utf-8"))

    def list(self, agent_name: str, pane: str, state: DeliveryState | None = None) -> list[QueuedMessage]:
        """List messages for an agent/pane, optionally filtered by state."""
        pane_dir = self._pane_dir(agent_name, pane)
        if not pane_dir.exists():
            return []

        messages = []
        for path in sorted(pane_dir.glob("*.json")):
            if path.name.startswith("."):
                continue
            try:
                msg = QueuedMessage.from_json(path.read_text(encoding="utf-8"))
                if state is None or msg.state == state:
                    messages.append(msg)
            except (json.JSONDecodeError, KeyError, ValueError):
                # Skip corrupted entries
                continue
        return messages

    def update_state(
        self,
        agent_name: str,
        pane: str,
        message_id: str,
        new_state: DeliveryState,
    ) -> QueuedMessage:
        """Update a message's delivery state."""
        if not isinstance(new_state, DeliveryState):
            raise ValidationError("new_state must be a DeliveryState enum")

        with self._file_lease(agent_name, pane):
            msg = self.read(agent_name, pane, message_id)
            if msg.state == new_state:
                return msg

            updated = QueuedMessage(
                id=msg.id,
                agent_name=msg.agent_name,
                pane=msg.pane,
                text=msg.text,
                idempotency_key=msg.idempotency_key,
                state=new_state,
                created_at=msg.created_at,
                updated_at=_now_iso(),
                metadata=msg.metadata,
            )

            # Atomic update via temp + link
            temp_path = self._pane_dir(agent_name, pane) / f".{message_id}.tmp"
            temp_path.write_text(updated.to_json(), encoding="utf-8")
            final_path = self._message_path(agent_name, pane, message_id)
            os.replace(temp_path, final_path)

            return updated

    def acknowledge(self, agent_name: str, pane: str, message_id: str) -> QueuedMessage:
        """Mark a message as acknowledged."""
        return self.update_state(agent_name, pane, message_id, DeliveryState.ACKNOWLEDGED)

    def request_cancellation(self, agent_name: str, pane: str, message_id: str) -> None:
        """Request cancellation by creating a sidecar .cancel-request file."""
        with self._file_lease(agent_name, pane):
            msg = self.read(agent_name, pane, message_id)
            if msg.state in (DeliveryState.ACKNOWLEDGED, DeliveryState.DELIVERED_UNVERIFIED):
                # Already terminal, ignore
                return
            cancel_path = self._cancel_path(agent_name, pane, message_id)
            cancel_path.write_text(_now_iso(), encoding="utf-8")

    def is_cancellation_requested(self, agent_name: str, pane: str, message_id: str) -> bool:
        """Check if cancellation was requested for a message."""
        cancel_path = self._cancel_path(agent_name, pane, message_id)
        return cancel_path.exists()

    def prune(self, acknowledged_older_than_ms: int) -> int:
        """Remove acknowledged messages older than the given threshold.

        Returns the number of messages pruned.
        """
        bounded_int(acknowledged_older_than_ms, "acknowledged_older_than_ms", 0, 365 * 24 * 3600 * 1000)
        cutoff = time.time() - (acknowledged_older_than_ms / 1000)
        pruned = 0

        for agent_dir in self.queue_root.iterdir():
            if not agent_dir.is_dir():
                continue
            for pane_dir in agent_dir.iterdir():
                if not pane_dir.is_dir():
                    continue
                with self._file_lease(agent_dir.name, pane_dir.name):
                    for path in pane_dir.glob("*.json"):
                        if path.name.startswith("."):
                            continue
                        try:
                            msg = QueuedMessage.from_json(path.read_text(encoding="utf-8"))
                            if msg.state == DeliveryState.ACKNOWLEDGED:
                                # Parse updated_at to check age
                                updated = datetime.fromisoformat(msg.updated_at.replace("Z", "+00:00")).timestamp()
                                if updated < cutoff:
                                    # Remove message and its idempotency record
                                    path.unlink(missing_ok=True)
                                    idem_path = self._idempotency_path(agent_dir.name, pane_dir.name, msg.idempotency_key)
                                    idem_path.unlink(missing_ok=True)
                                    cancel_path = self._cancel_path(agent_dir.name, pane_dir.name, msg.id)
                                    cancel_path.unlink(missing_ok=True)
                                    pruned += 1
                        except (json.JSONDecodeError, KeyError, ValueError, OSError):
                            # Skip corrupted entries
                            continue
        return pruned