"""Real CLI session integration guard.

When DIAMOND_REAL_CLI is not set, all integration tests fall back to deterministic
fake processes (the verified path). When set to "1", real CLI session integration
activates with explicit opt-in.

This file is import-safe: it never triggers model calls or CLI launches unless
the env var is set.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RealCLIIntegrationState:
    """Current state of real CLI integration."""
    enabled: bool = False
    reason: str = "not enabled (DIAMOND_REAL_CLI not set)"


# Singleton instance - imported once at module load
_real_cli_state = RealCLIIntegrationState(
    enabled=os.environ.get("DIAMOND_REAL_CLI", "").lower() in {"1", "true", "yes"},
    reason="enabled via DIAMOND_REAL_CLI environment variable"
    if os.environ.get("DIAMOND_REAL_CLI", "").lower() in {"1", "true", "yes"}
    else "not enabled (DIAMOND_REAL_CLI not set)"
)


def is_enabled() -> bool:
    """Return True if real CLI integration is enabled."""
    return _real_cli_state.enabled


def reason() -> str:
    """Return the reason string describing the current state."""
    return _real_cli_state.reason


# Convenience: skip decorator for tests
def skip_unless_real_cli(test_func):
    """Decorator that skips the test unless DIAMOND_REAL_CLI is enabled."""
    import functools
    @functools.wraps(test_func)
    def wrapper(*args, **kwargs):
        if not is_enabled():
            import unittest
            raise unittest.SkipTest(reason())
        return test_func(*args, **kwargs)
    return wrapper


# skip_if_not_real_cli = skip_unless_real_cli  # alias