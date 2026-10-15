"""Diamond transport layer for inter-agent communication.

Provides a durable message queue, session registry, message transport,
and tmux-backed session adapter. All components are Python-only; no
third-party Node.js dependencies are used.
"""

from .delivery_queue import (
    DeliveryQueue,
    DeliveryState,
    QueuedMessage,
    QueueError,
    LeaseError,
    IdempotencyConflictError,
)
from .message_transport import (
    MessageTransport,
    TransportMessage,
    TransportError,
    CorrelationError,
)
from .session_adapter import (
    SessionAdapter,
    TmuxSessionInfo,
    SessionAdapterError,
    TmuxNotAvailableError,
)
from .session_registry import (
    SessionRegistry,
    SessionRecord,
)

__all__ = [
    # Delivery queue
    "DeliveryQueue",
    "DeliveryState",
    "QueuedMessage",
    "QueueError",
    "LeaseError",
    "IdempotencyConflictError",
    # Message transport
    "MessageTransport",
    "TransportMessage",
    "TransportError",
    "CorrelationError",
    # Session adapter
    "SessionAdapter",
    "TmuxSessionInfo",
    "SessionAdapterError",
    "TmuxNotAvailableError",
    # Session registry
    "SessionRegistry",
    "SessionRecord",
]