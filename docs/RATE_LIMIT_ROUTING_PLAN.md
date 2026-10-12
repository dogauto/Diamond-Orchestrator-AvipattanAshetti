# Rate-Limit Aware Provider Routing — Foundational Plan

## Executive Summary

Replace hardcoded quota blocks (workflow.py:151, delivery_cli.py:61, delivery.py:183, live.py:105) with a **dynamic provider health & quota system** that:
- Probes each provider's rate-limit state on `doctor` and before dispatch
- Routes work to the **best available provider** for a given role
- Falls back gracefully when a provider is quota-blocked or unhealthy
- Keeps a **universal transcript** so swapped providers continue from the same context

Inspired by tandem's rate-limit tab bar and transcript translation, but adapted for Diamond's parallel, revisioned workflow architecture.

---

## Current State (Problem)

| Location | Hardcoded Check | Behavior |
|----------|----------------|----------|
| `workflow.py:151` | `provider is Provider.GEMINI` | Blocks all Gemini workflows unconditionally |
| `delivery_cli.py:61` | Same | Blocks Gemini delivery probes/stages |
| `delivery.py:183` | Same | Blocks Gemini quota confirmation |
| `live.py:105` | Same | Blocks Gemini controller confirmation |

**All four are static, provider-specific, and don't probe actual quota state.**

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                      PROVIDER HEALTH LAYER                           │
├─────────────────────────────────────────────────────────────────────┤
│  diagnostics.py:doctor()  ──►  ProbeResult(rate_limit: RateLimit)   │
│                              │                                      │
│                              ▼                                      │
│  ProviderHealthStore (new)  ──►  Persisted per-provider state       │
│      - last_probe_ts, rate_limit_remaining, quota_reset_ts,        │
│        consecutive_failures, last_error                            │
│                              │                                      │
│                              ▼                                      │
│  RoutingPolicy (new)        ──►  Select best provider for role      │
│      - primary, fallbacks, health threshold, cooldown               │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      WORKFLOW EXECUTION LAYER                        │
├─────────────────────────────────────────────────────────────────────┤
│  workflow.py:run_step()     ──►  Uses RoutingPolicy to pick provider │
│      before prompt()                                                            │
│      on quota error ──►  mark unhealthy, retry with fallback              │
│                              │                                      │
│                              ▼                                      │
│  UniversalTranscript (new)  ──►  Single canonical conversation      │
│      per workflow attempt                                           │
│      - all providers read same history                              │
│      - appended atomically with attempt events                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Component-by-Component Plan

### 1. `src/diamond/diagnostics.py` — Extended ProbeResult

**Current:** `ProbeResult(provider, status, version, auth_status, detail)`

**Add:** `rate_limit: RateLimitInfo | None`

```python
@dataclass(frozen=True)
class RateLimitInfo:
    """Rate-limit state from provider's own reporting."""
    remaining: int | None          # requests remaining in current window
    window_seconds: int | None     # window duration (e.g., 3600 = 1h)
    reset_at: float | None         # monotonic timestamp when window resets
    source: str                    # "claude_usage", "codex_status", "gemini_report", "inferred"
```

**Changes to `probe_version()`:**
- After `--version` succeeds, **also run provider-specific quota probe**:
  - `claude` → `claude --usage` (JSON, parses `remaining` per window)
  - `codex` → `codex status` (JSON, parses rate limits)
  - `gemini` → parse output for "quota" / "rate limit" / "429" patterns
  - `omnirush` / `copilot` / `antigravity` → best-effort or `None`
- Store in `ProbeResult.rate_limit`
- Update `doctor()` to return extended tuples

**New function:**
```python
def probe_rate_limit(provider: Provider, executable: str, timeout: float = 10.0) -> RateLimitInfo | None:
    """Run provider-specific quota check. Returns None if unavailable."""
```

---

### 2. `src/diamond/provider_health.py` — **NEW FILE**

**Purpose:** Persistent per-provider health state with TTL, failure tracking, cooldown.

```python
@dataclass
class ProviderHealth:
    provider: Provider
    last_probe_ts: float              # time.monotonic()
    rate_limit: RateLimitInfo | None
    consecutive_failures: int         # failed probes or dispatch errors
    last_error: str | None
    is_quarantined: bool              # set after N consecutive failures
    quarantine_until: float | None    # monotonic timestamp
    last_successful_dispatch_ts: float | None
```

**Storage:** SQLite table `provider_health` (in `store.py` schema):
```sql
CREATE TABLE provider_health (
    provider TEXT PRIMARY KEY,
    last_probe_ts REAL NOT NULL,
    rate_limit_json TEXT,              -- JSON of RateLimitInfo
    consecutive_failures INT DEFAULT 0,
    last_error TEXT,
    is_quarantined INT DEFAULT 0,
    quarantine_until REAL,
    last_successful_dispatch_ts REAL
);
```

**API:**
```python
class ProviderHealthStore:
    def __init__(self, ledger: EventStore): ...
    def upsert_probe(self, provider: Provider, result: ProbeResult): ...
    def record_dispatch_start(self, provider: Provider): ...
    def record_dispatch_result(self, provider: Provider, success: bool, error: str | None): ...
    def get_health(self, provider: Provider) -> ProviderHealth: ...
    def list_healthy(self, min_remaining: int = 1) -> list[ProviderHealth]: ...
    def quarantine(self, provider: Provider, duration_seconds: float): ...
    def release_quarantine(self, provider: Provider): ...
```

**Quarantine logic:**
- 3 consecutive failures → quarantine for 5 min (configurable)
- On successful dispatch → reset failures, clear quarantine
- `doctor()` auto-releases expired quarantines

---

### 3. `src/diamond/routing.py` — **NEW FILE**

**Purpose:** Policy-driven provider selection for a role.

```python
@dataclass(frozen=True)
class RoutingRule:
    role: Role
    primary: Provider
    fallbacks: tuple[Provider, ...]    # ordered preference
    min_remaining: int                 # skip provider if rate_limit.remaining < this
    require_healthy: bool = True       # skip quarantined providers
    cooldown_seconds: float = 300      # min time between switches

@dataclass(frozen=True)
class RoutingPolicy:
    rules: Mapping[Role, RoutingRule]
    default_min_remaining: int = 1
    default_cooldown: float = 300
```

**Config source:** `config/diamond.toml` — new `[routing]` section:
```toml
[routing]
# role-specific rules
builder.primary = "claude"
builder.fallbacks = ["codex", "omnirush", "copilot", "antigravity", "gemini"]
builder.min_remaining = 5
builder.cooldown_seconds = 300

debugger.primary = "codex"
debugger.fallbacks = ["claude", "omnirush", "copilot", "antigravity", "gemini"]
debugger.min_remaining = 3

integrator.primary = "claude"
# ... etc for QA, REVIEWER, RELEASE
```

**API:**
```python
def load_routing_policy(config: DiamondConfig) -> RoutingPolicy:
    """Parse [routing] from TOML, fall back to sensible defaults."""

def select_provider(
    policy: RoutingPolicy,
    health_store: ProviderHealthStore,
    role: Role,
    exclude: set[Provider] = frozenset(),
    now: float | None = None
) -> Provider:
    """
    Returns best provider for role:
    1. Primary if healthy and rate_limit.remaining >= min_remaining
    2. First fallback meeting criteria
    3. Raise if none available
    """
```

---

### 4. `src/diamond/workflow.py` — Integrate Routing

**Change `run_step()`** (around line 149):
```python
# OLD: hardcoded Gemini block
# if provider is Provider.GEMINI:
#     raise ExecutionGuardError("Gemini remains quota-blocked...")

# NEW: use routing policy
from .routing import select_provider
from .provider_health import ProviderHealthStore

policy = load_routing_policy(config)
health_store = ProviderHealthStore(ledger)

# Determine role from workflow status
role = Role.BUILDER if initial.status is WorkflowStatus.CREATED else Role.DEBUGGER

# Select provider (respects health, quota, fallbacks)
provider = select_provider(policy, health_store, role, exclude=set())

# Verify provider is available via probe
diagnostic = probe_version(provider, config.providers[provider].executable)
if diagnostic.status != "available":
    # Try fallbacks automatically
    provider = select_provider(policy, health_store, role, exclude={provider})
    diagnostic = probe_version(provider, config.providers[provider].executable)
    if diagnostic.status != "available":
        raise ExecutionGuardError(f"no healthy provider for role {role.value}")

# Record dispatch start for health tracking
health_store.record_dispatch_start(provider)

try:
    # ... existing prompt() call ...
    prompt(initial, ...)
finally:
    # Record result for health tracking
    health_store.record_dispatch_result(provider, success, error)
```

---

### 5. `src/diamond/transcript.py` — **NEW FILE** (Universal Transcript)

**Purpose:** Single canonical conversation per workflow attempt that all providers read/append.

```python
@dataclass(frozen=True)
class TranscriptEntry:
    role: str                 # "user" | "assistant" | "system" | "tool"
    content: str
    provider: Provider        # who generated this
    attempt_id: str
    timestamp: float
    metadata: dict[str, Any]  # tool_calls, tool_results, etc.

class UniversalTranscript:
    """Canonical conversation for a workflow attempt."""
    
    def __init__(self, attempt_id: str): ...
    
    def append(self, entry: TranscriptEntry) -> None: ...
    def to_claude_format(self) -> list[dict]: ...
    def to_codex_format(self) -> list[dict]: ...
    def to_opencode_format(self) -> list[dict]: ...
    def to_generic_format(self) -> list[dict]: ...
    
    @staticmethod
    def from_evidence(ledger: EventStore, attempt_id: str) -> "UniversalTranscript":
        """Reconstruct from evidence records."""
```

**Integration in `workflow.py`:**
- Before `prompt()`, load `UniversalTranscript.from_evidence(ledger, attempt_id)`
- Pass transcript to provider adapter (each adapter translates to its format)
- After `provider_result`, append assistant/tool entries to transcript
- Persist transcript entries as new evidence kind `TRANSCRIPT_ENTRY`

---

### 6. `src/diamond/evidence.py` — New Evidence Kind

Add `EvidenceKind.TRANSCRIPT_ENTRY`:
```python
class EvidenceKind(str, Enum):
    # ... existing ...
    TRANSCRIPT_ENTRY = "transcript_entry"
```

**Payload:**
```json
{
  "attempt_id": "...",
  "role": "assistant",
  "provider": "claude",
  "content": "...",
  "metadata": {"tool_calls": [...], "tokens_in": 1000, "tokens_out": 500}
}
```

---

### 7. `src/diamond/config.py` — Extend ProviderConfig

```python
@dataclass(frozen=True)
class ProviderConfig:
    executable: str
    enabled: bool
    # NEW
    rate_limit_probe: str | None = None      # custom probe command, e.g. "usage" "status"
    rate_limit_parse: str | None = None      # regex/JSON path to extract remaining
    health_weight: float = 1.0               # for routing tie-breaking
```

**Default config in `config/diamond.toml`:**
```toml
[providers.gemini]
executable = "gemini"
enabled = true
rate_limit_probe = "usage"         # gemini doesn't have native; will infer
rate_limit_parse = "quota_remaining: (\\d+)"

[providers.claude]
executable = "claude"
enabled = true
rate_limit_probe = "usage"         # native --usage JSON
rate_limit_parse = "$.windows[0].remaining"
```

---

### 8. `src/diamond/delivery.py` & `delivery_cli.py` — Use Routing

**Same pattern as `workflow.py`:**
- Replace hardcoded Gemini checks with `select_provider(policy, health, Role.INTEGRATOR, ...)`
- Record dispatch results in health store
- On quota error → quarantine, retry with fallback

---

### 9. `src/diamond/live.py` — Use Routing

**Controller confirmation:**
- Use `select_provider` for `Role.CONTROLLER` (or new `Role.CONFIRMATION`)
- Apply same fallback logic

---

### 10. `src/diamond/doctor.py` / CLI — Surface Health

**Extend `doctor` command output:**
```
gemini       available  v0.46.0  auth=NOT_CHECKED  quota=12/100 (1h window)  healthy
claude       available  v2.1.296 auth=NOT_CHECKED  quota=4850/5000 (5h)      healthy
codex        available  v0.159.3 auth=NOT_CHECKED  quota=234/1000 (7d)       healthy
```

**New CLI:** `diamond provider health` — detailed table with quarantine state, last error, cooldown.

---

### 11. `src/diamond/store.py` — Schema Migration

Add migration in `EventStore.migrate()`:
```python
def _migrate_005_provider_health(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS provider_health (
            provider TEXT PRIMARY KEY,
            last_probe_ts REAL NOT NULL,
            rate_limit_json TEXT,
            consecutive_failures INT DEFAULT 0,
            last_error TEXT,
            is_quarantined INT DEFAULT 0,
            quarantine_until REAL,
            last_successful_dispatch_ts REAL
        )
    """)
```

---

## Data Flow Summary

```
doctor()
   │
   ├─► probe_version() ──► RateLimitInfo ──► ProviderHealthStore.upsert_probe()
   │
workflow run_step()
   │
   ├─► select_provider(policy, health_store, role) ──► Provider
   │       │
   │       ├─ checks health.quarantined
   │       ├─ checks rate_limit.remaining >= min_remaining
   │       └─ returns primary or first healthy fallback
   │
   ├─► probe_version(selected) ──► confirm available
   │
   ├─► health_store.record_dispatch_start(provider)
   │
   ├─► prompt(transcript) ──► provider executes
   │       │
   │       ├─ on success: health_store.record_dispatch_result(provider, True, None)
   │       └─ on quota/429: health_store.record_dispatch_result(provider, False, "quota")
   │            ├─ health.quarantine(provider, 300s)
   │            ├─ select_provider(policy, health, role, exclude={provider})
   │            └─ retry with fallback
   │
   └─► UniversalTranscript.append(entries) ──► EvidenceRecord(TRANSCRIPT_ENTRY)
```

---

## Migration Strategy

| Phase | Changes | Risk |
|-------|---------|------|
| **1. Foundation** | `provider_health.py`, `routing.py`, schema migration, `diagnostics.py` rate-limit probe | Low — new files, no behavior change yet |
| **2. Workflow Integration** | `workflow.py` uses `select_provider`, records health | Medium — changes dispatch logic |
| **3. Delivery/Live** | `delivery.py`, `delivery_cli.py`, `live.py` use routing | Medium — same pattern |
| **4. Transcript** | `transcript.py`, `evidence.py` kind, adapter integration | High — changes prompt construction |
| **5. Config & CLI** | `config.py` ProviderConfig, `diamond.toml` [routing], `doctor` output | Low — config only |

---

## Testing Requirements

| Test | Description |
|------|-------------|
| `test_provider_health` | Quarantine triggers after N failures, auto-releases |
| `test_routing_policy` | Primary selected when healthy; fallbacks on quota/quarantine |
| `test_routing_fallback_chain` | Multiple fallbacks tried in order |
| `test_workflow_dispatch_fallback` | Gemini quota → Codex selected automatically |
| `test_delivery_fallback` | Integrator quota → fallback selected |
| `test_transcript_roundtrip` | Transcript reconstructs from evidence, exports to all formats |
| `test_doctor_output` | Shows quota remaining, quarantine state |
| `test_config_routing_section` | TOML parsing, defaults, validation |

---

## Files to Create / Modify

| File | Action |
|------|--------|
| `src/diamond/provider_health.py` | **CREATE** — health store, quarantine logic |
| `src/diamond/routing.py` | **CREATE** — policy, selection logic |
| `src/diamond/transcript.py` | **CREATE** — universal transcript, format translation |
| `src/diamond/diagnostics.py` | **MODIFY** — `ProbeResult.rate_limit`, `probe_rate_limit()` |
| `src/diamond/config.py` | **MODIFY** — `ProviderConfig` fields, `[routing]` parsing |
| `src/diamond/workflow.py` | **MODIFY** — `run_step()` uses routing, health tracking |
| `src/diamond/delivery.py` | **MODIFY** — quota checks → routing |
| `src/diamond/delivery_cli.py` | **MODIFY** — quota checks → routing |
| `src/diamond/live.py` | **MODIFY** — controller confirmation uses routing |
| `src/diamond/evidence.py` | **MODIFY** — `TRANSCRIPT_ENTRY` kind |
| `src/diamond/store.py` | **MODIFY** — schema migration for `provider_health` table |
| `config/diamond.toml` | **MODIFY** — add `[routing]` section, provider probe config |
| `tests/test_provider_health.py` | **CREATE** |
| `tests/test_routing.py` | **CREATE** |
| `tests/test_transcript.py` | **CREATE** |

---

## Configuration Example (`config/diamond.toml`)

```toml
[routing]
# Builder (CREATED workflows)
builder.primary = "claude"
builder.fallbacks = ["codex", "omnirush", "copilot", "antigravity", "gemini"]
builder.min_remaining = 5
builder.cooldown_seconds = 300

# Debugger (NEEDS_CORRECTION workflows)
debugger.primary = "codex"
debugger.fallbacks = ["claude", "omnirush", "copilot", "antigravity", "gemini"]
debugger.min_remaining = 3

# Integrator (delivery stage 1)
integrator.primary = "claude"
integrator.fallbacks = ["codex", "omnirush"]
integrator.min_remaining = 10

# QA (delivery stage 2)
qa.primary = "copilot"
qa.fallbacks = ["codex", "claude"]
qa.min_remaining = 5

# Reviewer (delivery stage 3)
reviewer.primary = "codex"
reviewer.fallbacks = ["claude", "omnirush"]
reviewer.min_remaining = 5

# Release (delivery stage 4)
release.primary = "copilot"
release.fallbacks = ["codex", "claude"]
release.min_remaining = 5

[providers.gemini]
executable = "gemini"
enabled = true
rate_limit_probe = ""          # no native probe; infer from errors
rate_limit_parse = ""

[providers.claude]
executable = "claude"
enabled = true
rate_limit_probe = "usage"
rate_limit_parse = "$.windows[0].remaining"

[providers.codex]
executable = "codex"
enabled = true
rate_limit_probe = "status"
rate_limit_parse = "$.rate_limit.remaining"
```

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Provider quota APIs change | Parse defensively; fall back to "inferred" from 429 errors; configurable parse patterns |
| Transcript format divergence | Keep generic format as source of truth; provider-specific formats are derived |
| Quarantine too aggressive | Start conservative (3 failures, 5 min); make configurable; expose `provider health` CLI |
| Routing loops | Cooldown prevents rapid switching; max 1 fallback attempt per step |
| Schema migration fails | Run migration in transaction; test on existing DBs |

---

## Success Criteria

1. `doctor` shows quota remaining for all 6 providers
2. `workflow run` on Gemini-quota-blocked workflow automatically uses Codex
3. `delivery create` with quota-blocked integrator falls back to Codex
4. Universal transcript reconstructs identically from evidence
5. 306 tests pass + new tests pass
6. No hardcoded `provider is Provider.GEMINI` checks remain