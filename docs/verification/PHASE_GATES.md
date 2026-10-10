# Verification phase gates

All implementation gates start `NOT_STARTED`. Documentation is `DOCS_READY`,
meaning ready for parent review, not runtime acceptance. The parent updates
[Status](../progress/STATUS.md) only after retaining actual evidence.

## Gate rules and evidence

Gate statuses: `NOT_STARTED`, `IN_PROGRESS`, `PASS`, `FAIL`, `BLOCKED`.
`PASS` requires every applicable check to pass on the identified candidate.
`FAIL` means an observed contract violation. `BLOCKED` means required evidence,
prerequisite, or reconciliation is unavailable. No run means `NOT_STARTED`,
not a pass. An explicit non-applicability rationale applies to a check, not
as a shortcut around the requirement for all six live adapters.

Retain for each check: requirement ID, command/scenario, candidate/base/config
digests, host/Python/CLI versions, attempt/session if applicable, sanitized
actual stdout/stderr, exit/signal/timeouts, artifact digests, expected versus
observed behavior, reviewer and finding resolutions. Record redaction,
truncation, or missing fields explicitly. `UNKNOWN` is not verified evidence.

Store human summaries under `docs/verification/` and structured execution
evidence in the planned local state/evidence area. Workers cannot self-attest
to a passing gate. Parent/controller validation binds evidence to exact bytes.

**Deterministic tests** use controlled fake executables and synthetic faults:
they establish repeatable controller behavior, not provider authentication.
**Live tests** use real installed CLIs in isolated targets: they establish
actual host compatibility/auth/model behavior, not full crash coverage.
Production acceptance needs both; neither substitutes for the other.

## Evidence available before implementation

The parent supplied prior installed-path and CLI help observations. They
indicate noninteractive modes for all six providers, with Omnirush print
text and no verified JSON. Raw help artifacts, exact versions, dates, and
digests have not been attached to this project. Preserve/recheck them during
phase 1. No successful auth, model execution, or live readiness is established.

### Phase 1: CLI compatibility and foundation

**Status:** `PASS` for the bounded foundation slice; live model execution is
not part of this pass and remains a later gate.

**Purpose:** establish the safe local command/config/task surface.

**Tests/evidence:**
- Record Python/macOS baseline, command resolution, safe version probes,
  bounded help capability evidence, and provider-by-provider limitations.
- Config parse/validation negatives and portable role assignment checks.
- Black-box create/list/show/events including empty list and invalid/missing ID.
- Restart reads and foundational transaction/CAS/illegal-transition tests.
- Fake executable sentinels demonstrate no AI invocation; `doctor` always
  reports auth `NOT_CHECKED` and does not invoke authentication/model probes.
- Launcher checks from unrelated cwd/space-containing paths; actual test and
  build/package-check outputs; fresh independent review of the slice.

**Pass evidence:** 62 tests pass on Python 3.11/3.12/3.14, including the
independent RV01-RV05 review regressions; `scripts/check.sh` compiles 21 files;
config validation and safe version-only doctor probes pass for the installed five CLIs;
the independent re-review verdict is `ACCEPT_FOUNDATION`.

**Pass:** local CLI contracts and foundation invariants hold; compatibility
observations are explicit and no diagnostic implies live readiness.
**Fail:** invalid data mutates state, a read launches work, or evidence/launch
behavior contradicts the documented foundation surface.
**Block:** Python baseline, safe probe definition, mandatory evidence, or
independent reviewer is unavailable. Provider absence blocks compatibility
completion but does not prevent deterministic foundation development.

### Phase 2: Persistent contracts and ledger

**Status:** `BOUNDED_LEDGER_PASS`; broader recovery and dependency coordination remain.

**Purpose:** prove durable, deterministic authoritative state.

**Tests/evidence:**
- Validate serialized contract schemas and all state guards.
- Competing controller writer ownership, stale CAS, command-ID replay and
  conflicting duplicate payload; compare event/projection state before/after.
- Kill/fault between event append, projection update, and commit; reopen and
  verify all-or-nothing mutation plus deterministic projection replay.
- Dependency/correction limits, blocked resume guards, versioned migration
  failure behavior, integrity checks, and backup/restore fixtures.
- Prove replay makes zero executable invocations.

**Pass:** committed facts survive restart; rejected/aborted mutations leave
no split state; replay equals the saved projection without side effects.
**Fail:** duplicate revisions, lost committed events, split projection/event,
accepted stale transition, or replay-driven execution.
**Block:** migration/recovery policy or fault evidence is incomplete.

### Phase 3: Six adapters and supervision

**Status:** `IN_PROGRESS`; Gemini is fixed but quota-blocked for today's final
confirmation, Codex/Copilot/Omnirush pass smoke, and Omnirush plan mode now has
host-enforced workspace write protection through macOS `sandbox-exec`.

**Purpose:** prove normalized results and bounded subprocess behavior.

**Deterministic tests/evidence:**
- Fake executables emit success, malformed structured/text output, nonzero
  exits, auth/quota-like errors, slow output, output overflow, and signals.
- Verify concurrent stream draining, deadlines, cancellation, process-group
  cleanup, missing binary behavior, secret stripping, and unknown identities.
- Validate Omnirush text handling without assuming an unproven JSON option.

**Current deterministic evidence:** provider-specific invocation builders, an
async supervisor, normalized result parsers, verified skill records, immutable
task packets, a macOS deny-by-default Omnirush plan sandbox, and an Antigravity
headless adapter are implemented for all six providers. The full suite has 86
passing tests on Python
3.11/3.12/3.14, including bounded output, timeout,
nonzero-exit, cancellation, and process-pipe cleanup checks. `ruflo security
scan` reports zero findings.

The historical live report is `docs/verification/LIVE_SMOKE_REPORT_2026-10-07.md`;
the intermediate rerun is
`docs/verification/LIVE_SMOKE_REPORT_2026-10-07_RERUN.md`; final source-bound
evidence is `docs/verification/LIVE_SMOKE_REPORT_2026-10-07_FINAL.md`.
Provider blocker remediation is recorded in
`docs/verification/PROVIDER_FIX_REPORT_2026-10-07.md`.

**Live tests/evidence:**
- Individually initiate Gemini, Antigravity CLI, Codex, Omnirush, Copilot, and Claude smoke
  tasks in isolated workspaces with visible execution logs.
- Capture actual noninteractive invocation, CLI version/session, auth result,
  requested/reported model or explicit `UNKNOWN`, real output/exit, artifact
  digest, and cleanup. Record reporting limitations and mismatches honestly.

**Pass:** deterministic scenarios and all six live subgates pass on recorded
host/version combinations; parser/identity limitations are explicit.
**Fail:** invented metadata, undetected invalid output, hidden fallback,
deadlock, unbounded execution, secret leak, or incorrect outcome mapping.
**Block:** auth, quota, binary, supported flags, permission enforcement, or
required live evidence is unavailable; no automatic relaunch to resolve it.
The deterministic subgate does not authorize live model calls; each provider
still requires an explicit isolated smoke test and truthful result evidence.

### Phase 4: Roles, skills, and context

**Status:** `IN_PROGRESS`.

**Purpose:** prove bounded, current, capability-correct task packets.

**Tests/evidence:**
- Test role mapping changes, disabled providers, distinct model identity,
  exact needed-skill selection, canonical source/version/digest records.
- Reject missing skills/prerequisites, stale plan/base/config/skill digests,
  malformed packets, unauthorized tool/path expansion, and symlink escape.
- Verify context provenance, dependent artifact matching, and current accepted
  plan requirement before dispatch. Live check needed skill loading per host.
- Retain actual selective Council decisions when applicable, or explicit
  non-applicability; verify Task Observer does not replace task state.

**Current deterministic evidence:** skill source digests, role applicability,
dependency/prerequisite checks, immutable packets, stale plan/base/config
rejection, workspace/symlink containment, and dependent artifact digest checks
are covered by the packet slice.

**Pass:** only validated current packets dispatch, with necessary verified
skills and truthful descriptions of host-enforced restrictions.
**Fail:** stale context executes, prose expands permissions, skill presence is
misrepresented as runnable capability, or worktrees are treated as sandboxes.
**Block:** prerequisites, enforceable restriction, or needed live proof is absent.

### Phase 5: Builders and debuggers

**Status:** `BOUNDED_WORKFLOW_PASS`; integration and production acceptance remain.

**Purpose:** prove bounded production and evidence-driven correction.

**Tests/evidence:**
- Deterministic concurrent tasks demonstrate default global cap of two,
  configurable timeouts, correction cap of two, and dependency/ownership guards.
- Inject missing artifact, changed-path violation, stale result, interruption,
  failing test, and repeated failure through correction exhaustion.
- Retain failure packet, actual reproduction, discriminating root-cause
  evidence, bounded diff, failing-before/passing-after regression, and cleanup.
- Run a bounded live build/debug case with real test outputs and artifact hashes.

**Pass:** limits hold, verified output meets the packet, and debugger evidence
supports the cause and fix without unrelated changes.
**Fail:** uncontrolled retries, unsupported root-cause claims, missing checks,
budget resets, ownership conflict, or acceptance of a stale/missing artifact.
**Block:** failure cannot be reproduced, root cause remains uncertain, or
previous external work cannot be reconciled.

### Phase 6: Integration, review, and acceptance

**Status:** `NOT_STARTED`.

**Purpose:** prove exact integrated artifacts satisfy requirements independently.

**Tests/evidence:**
- Integrate conflicting/nonconflicting patches against exact base digests;
  capture decisions and actual post-integration build/tests.
- Fake and live end-to-end cases with independent QA, fresh Reviewer session,
  author lineage, exact candidate digests, and release audit.
- Reject sole-author review, inherited author sessions, unresolved blocking
  findings, stale evidence, and agent-only test/release claims.
- Modify candidate bytes after review; verify acceptance invalidation.

**Pass:** integrated behavior meets acceptance criteria and exact candidate
has valid independent review with no unresolved release blockers.
**Fail:** self-approval alone authorizes acceptance, checks apply to old bytes,
or a blocking finding disappears without resolution evidence.
**Block:** independent reviewer, required check, or integration decision is absent.

### Phase 7: Production recovery and install hardening

**Status:** `NOT_STARTED`.

**Purpose:** establish operational readiness on a clean macOS installation.

**Tests/evidence:**
- Inject crashes before launch, after intent, after process start but before
  recording it, after external completion, and during ledger commit/cleanup.
- Reconcile unknown attempts, PID reuse, late reports, process children,
  duplicate launch races, and termination failures before permitting relaunch.
- Repeat lifecycle/integrity/backup/migration acceptance on the release revision.
- Fresh install with documented prerequisites; verify launcher/config/state
  placement without dependence on this user's absolute paths or hidden state.
- Repeat real-provider acceptance on that supported install; credentials are
  user-provisioned and remain outside project config/evidence.
- Fresh independent release audit of the complete acceptance matrix.

**Pass:** fake and live acceptance evidence is complete and recovery/installation
work as documented, with no unresolved release-blocking findings.
**Fail:** blind recovery relaunch, lost/split state, stale-PID termination,
hidden-machine dependency, unresolved blocker, or exactly-once external claims.
**Block:** ambiguous execution, clean-install evidence, auth/quota, or release
review is unresolved. The Astro build stays blocked until this phase passes.
