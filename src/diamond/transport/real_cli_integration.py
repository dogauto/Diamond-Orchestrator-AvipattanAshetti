"""Milestone 2A: Real CLI Session Integration - Opt-in Mechanism.

Provides environment-variable-gated real CLI integration alongside
deterministic fake-process tests that remain the primary verified path.
"""

import os
from pathlib import Path

# Opt-in guard: real CLI integration only activates when explicitly enabled.
# Ordinary project checks and deterministic tests must not trigger model calls.
REAL_CLI_INTEGRATION_ENABLED = os.environ.get("DIAMOND_REAL_CLI", "").lower() in {"1", "true", "yes"}

#: If this env var is not set, all integration tests use deterministic fake processes.
#: Set DIAMOND_REAL_CLI=1 to enable real CLI session integration (opt-in only).