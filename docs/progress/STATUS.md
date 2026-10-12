# Project status

## Current state

### Council foundation update, 2026-10-12

The [M0/M1 implementation](../verification/COUNCIL_M0_M1.md) is verified:
330 Python tests pass, 90 Python files compile in memory, and the macOS client
build passes. Self-improvement verification, independent delivery review,
publication, cancellation, and recovery are distinct operations. Schema 8
adds durable publication/disposition records and bounded evidence indexes;
existing ledgers require explicit migration. Daily analysis retains scope,
lineage, citations, and uncertainty. The M2 profile pilot and later adaptive
routing remain planned. This checkpoint supersedes older test counts below;
the historical live-provider and production-readiness limitations still apply.

| Item | State |
| --- | --- |
| Project | Flawless Diamond Piece |
| Documentation package | `DOCS_REVIEWED` |
| Foundation | `FOUNDATION_ACCEPTED` |
| Documentation review/implementation authorization | Complete; user requested building after documentation |
| Runtime implementation/build/tests | `CONTROLLER_AND_MAC_CONSOLE_VERIFIED`; hybrid MCP bridge verified; final release gates remain |
| Authentication/model-live readiness | `PARTIAL_BLOCKED`; current smoke evidence exists, Gemini final confirmation remains quota-blocked |
| Production readiness | `RELEASE_CANDIDATE; NOT_PRODUCTION_CERTIFIED` |
| Astro project | Blocked until orchestrator production acceptance and separate brief |

The documentation package is complete and reviewed. The bounded foundation,
adapter/supervisor, pinned advisory skill catalog/role packet binding, durable
dispatch/evidence, and response-based Builder/Debugger slices are implemented
and verified. The schema-5 workflow checkpoint below includes actual failure
reproduction and one Codex correction. The controller JSON contract,
workflow/delivery inspection screens, explicit recovery action, native macOS
build, and hybrid MCP stdio bridge are now verified. Production certification
still requires live authentication evidence, crash-safe publication/recovery,
clean-install packaging, and an independent release gate.

## Implementation phases

| Phase | Initial state | Runtime evidence |
| --- | --- | --- |
| 1. CLI compatibility and foundation | `PASS` | 62 tests pass on Python 3.11/3.12/3.14; check script and safe doctor pass |
| 2. Persistent contracts and ledger | `BOUNDED_LEDGER_PASS` | Schema-5 workflow events/CAS/replay/correction budgets plus dispatch claims, one-shot launch, evidence, backup/reopen; broader recovery remains |
| 3. Six live adapters | `IN_PROGRESS` | Historical schema-4 five-provider marker/restart/duplicate checks; current schema-5 Claude/Codex workflow acceptance; Gemini quota confirmation blocked |
| 4. Roles, skills, and context | `ADVISORY_CATALOG_AND_PACKET_BINDING_PASS` | Four pinned MIT skill sources are provenance-verified, resolved by role, included as untrusted provider guidance, and bound into Builder/Debugger packets; host capability checks remain |
| 5. Builders and debuggers | `BOUNDED_WORKFLOW_PASS` | Direct Claude build and recorded controller-injected bug corrected by Codex; both pass 22 actual assertions, restart reads, and independent scoped review |
| 6. Integration, review, and acceptance | `BOUNDED_LIVE_PASS` | Live candidate integration, QA, Reviewer, Release, replay, fresh-process, and backup checks pass; production release audit remains |
| 7. Production recovery and install hardening | `IN_PROGRESS` | 227-test regression pass, project-status/MCP contracts pass, macOS SwiftPM build pass, and zero-finding security scan; packaging and crash-atomic publication remain |

Acceptance requirements remain release-blocking until production recovery,
clean installation, live provider authentication, and independent release
gates pass. The current release candidate is safe for bounded local use and
macOS monitoring/control, but it is not certified for unattended production
operation.

## Earlier readiness gate

- `scripts/check.sh`: **PASS** — 227 tests, 62 Python files compiled in
  memory, no bytecode files written.
- `project status --json`: **PASS** — schema-versioned response with six
  provider records, four verified advisory skills, one persisted `VERIFIED`
  workflow, and zero deliveries.
- `examples/DiamondMac`: **PASS** — SwiftPM macOS client builds successfully.
- macOS recovery control: **PASS** — blocked workflow reconciliation requires
  confirmation and records through the Python controller; it never performs a
  blind provider relaunch.
- Backup/restore, replay, and certificate matching remain covered by the
  existing delivery, execution, and storage suites.
- Current blockers: provider authentication is not certified, Gemini remains
  quota-blocked for live confirmation, filesystem publication and SQLite
  updates are not globally crash-atomic, and install/package/upgrade
  acceptance has not been completed.

## Last-known environment observations

Last-known platform: **macOS**. Required controller baseline: Python 3.11+;
the current deterministic suite passes on installed Python 3.11, 3.12, and 3.14.
Provider observations below retain their checkpoint scope; Claude 2.1.292 and
Codex 0.159.3 were also used in the schema-5 workflow run. Portable config uses
command names instead.

| Provider | Command | Previously observed path | Version | Auth | Live gate |
| --- | --- | --- | --- | --- | --- |
| Gemini CLI | `gemini` | `/opt/homebrew/bin/gemini` | `0.46.0` | `UNKNOWN` | `FIXED_DETERMINISTICALLY; LIVE_QUOTA_BLOCKED_TODAY` |
| Antigravity CLI | `agy` | `/Users/avipattan/.local/bin/agy` | `1.3.1` | `AVAILABLE` | `PASS_FOR_STREAM_AND_STRUCTURED_SMOKE` |
| Codex CLI | `codex` | `/Users/avipattan/.local/bin/codex` | `0.159.3` | `UNKNOWN` | `PASS_FOR_SMOKE` |
| Omnirush CLI | `omnirush` | `/opt/homebrew/bin/omnirush` | `2.2.1` | `UNKNOWN` | `PASS_FOR_SMOKE; HOST_WORKSPACE_WRITES_PROTECTED` |
| GitHub Copilot CLI | `copilot` | `/opt/homebrew/bin/copilot` | `1.0.91` | `gpt-5.6-luna` | `PASS_FOR_SMOKE` |
| Claude Code | `claude` | `/Users/avipattan/.local/bin/claude` | `2.1.292` | `UNKNOWN` | `PASS_FOR_SMOKE` |

Provider help was rechecked on 2026-10-07. Gemini supports `--approval-mode`
and JSON output; Codex supports `--sandbox read-only`, `--skip-git-repo-check`,
`--ephemeral`, and JSONL; Copilot supports plan mode and JSONL; Claude supports
plan mode and JSON; Omnirush remains text-only for this contract. Help support
is not authentication or successful model invocation proof.
The planned foundation `doctor` will report auth `NOT_CHECKED`.

## Pending choices and next checkpoint

- Integrate exact candidate/base revisions with independent QA, Reviewer,
  Integrator, and Release workflows and dependency-aware scheduling.
  The latest live run passed both the rejected-upstream scenario and the
  corrected candidate through all four stages; its report is retained under
  `docs/verification/delivery-live-2026-10-08/run-545659e2079b46e9ad986a8bc6d558c2/`.
- Extend installed skill coverage only when a reviewed capability is needed;
  host capability checks, internal MCP-client integration/packaging beyond the
  thin stdio bridge, and provider-tool policy beyond Claude's verified
  tool/MCP disabling remain open.
- Implement evidence-backed release/resume of quarantined workflows;
  `BLOCKED` currently retains ownership and worker capacity.
- Complete crash recovery around filesystem publication plus SQLite events,
  clean installation, packaging, and independent release acceptance.
- Confirm Gemini quota reset explicitly before initiating its final live gate.
- Define Astro product requirements only after orchestrator acceptance.

## Foundation evidence

- Independent review: `docs/verification/FOUNDATION_REVIEW.md`, verdict
  `ACCEPT_FOUNDATION` after RV01-RV05 recheck.
- Python 3.11.14: 62 tests, exit 0.
- Python 3.12.13: 62 tests, exit 0.
- Python 3.14.7 with `-W error::ResourceWarning`: 62 tests, exit 0.
- `scripts/check.sh`: 62 tests, 21 Python files compiled in memory, exit 0.
- `scripts/diamond.sh validate-config --json`: valid schema-1 configuration,
  exit 0.
- `scripts/diamond.sh doctor --json`: Gemini 0.46.0, Codex 0.159.3,
  Omnirush 2.2.1, Copilot 1.0.91, Claude 2.1.292; all version probes exit 0.
  Every provider remains `auth_status: NOT_CHECKED` and
  `workflow_execution: UNAVAILABLE`.
- Foundation database schema at acceptance: 2. Portable config schema: 1. No project ledger or Python
  bytecode was left by verification.

## Adapter and supervisor slice

- Provider-specific invocation construction exists for Gemini, Antigravity,
  Codex, Omnirush, Copilot, and Claude without invoking them.
- Async supervision covers bounded stdout/stderr capture, deadlines,
  process-group cleanup, output limits, cancellation, and normalized outcomes.
- Deterministic adapter, supervisor, result-parser, skill catalog/packet,
  macOS sandbox, Antigravity stream/structured-report, evidence, migration,
  controller-recording, and MCP stdio contract tests are included in the
  current 227-test suite.
- `ruflo security scan` reports 0 critical, high, medium, or low findings.
- Gemini quota confirmation, full internal provider-tool enforcement, and
  production recovery remain incomplete.

Historical smoke evidence is in `docs/verification/LIVE_SMOKE_REPORT_2026-10-07.md`.
Intermediate current-candidate evidence is in
`docs/verification/LIVE_SMOKE_REPORT_2026-10-07_RERUN.md`; final source-bound
evidence is in `docs/verification/LIVE_SMOKE_REPORT_2026-10-07_FINAL.md`.
Provider remediation evidence is in
`docs/verification/PROVIDER_FIX_REPORT_2026-10-07.md`. Gemini is fixed but
quota-blocked for today's final confirmation; Codex passes its fixed smoke.
Omnirush is smoke-valid with host-enforced workspace write protection; exact
tool/path packet policy and production readiness remain unestablished.
Antigravity stream/structured smoke evidence is in
`docs/verification/ANTIGRAVITY_LIVE_REPORT_2026-10-07.md`.

## Controller-owned evidence persistence

- Schema 4 adds immutable dispatch claims and one-shot launch acquisition to
  attempt/evidence storage; explicit migration supports schema 2 and 3.
- `src/diamond/controller.py` records launch intent before external execution,
  start state before `run`, normalized completion facts, ambiguous cancellation/
  exception states, redacted output digests, session/model identity, and result
  evidence.
- CLI reads are available through `task attempts <id>` and
  `task evidence <id>`; `migrate` is explicit and does not load provider config.
- Covered by CAS, stale-attempt, deduplication, restart-read, immutable SQL,
  redaction, rollback, migration, and real fake-process recording tests.
- Artifact size/digest/ownership, scheduler claims, worker capacity, recovery
  quarantine, cancellation/late-result retention, and backup/reopen checks pass
  for the bounded slice. Recovery observations never signal a possibly reused
  PID or authorize blind relaunch.

## Historical schema-4 persisted real acceptance checkpoint

- Candidate: `fc8a127249caa93d38f5cd3460cd669c7764c6c549da14c5139193ba70489e9d`.
- Report: `docs/verification/dispatch-live-2026-10-07/smoke-77906ff64ad44a21a18af8aee06cc9c6.json`.
- Antigravity 1.3.1, Codex 0.159.3, Omnirush 2.2.1, Copilot 1.0.91, and
  Claude 2.1.292 all return exact markers, reject duplicate launch requests,
  survive restart reads, and leave isolated workspaces unchanged. Copilot
  reports `gpt-5.6-luna`; other model fields remain explicitly UNKNOWN.
- Claude's duration utility passes all 22 actual assertions. Retained artifact:
  `dispatch-live-2026-10-07/duration-0f8f50b06c3c44d3a81df1015f6542b1.py`;
  SHA-256 `82075b5ed9f23ef79f93d65f0ae208a404a8c7da61410d0d2c75fbd938a03e2a`.
- Failed pre-fix runs remain in the ledger. Antigravity's loopback/log grants
  and Claude's variadic-argument separator, safe-mode, tool disabling, and JSON
  contract were fixed and then rechecked on the final candidate.
- `118` tests pass on Python 3.11/3.12/3.14; Python 3.14 uses ResourceWarning
  as an error. Checker compiles `37` Python files in memory. `ruflo` scan has
  zero findings; the AGENTS.md command `@Codex-flow/cli` returns npm 404.
- Independent scoped review resolved all seven dispatch/evidence findings.
- Backup `dispatch-live-2026-10-07/verified-backup.sqlite3` reopens with all
  `15` tasks/attempts replayed and `60` evidence records matching the original.

## Schema-5 Builder/Debugger acceptance checkpoint

- Current source candidate:
  `63fdaa79a5a7eef748f741f2fef5b7b9df4d1ea70c10a611bd8dfc365a4bbf7e`.
  Its 47-file manifest covers source, tests, all scripts, and configuration.
- Evidence directory:
  `docs/verification/workflow-live-2026-10-08/run-6d96031d14db4a01a6e9276750c3cbbd/`.
  Report: `workflow-acceptance-6395e5d8b3844a6f9750f614b30bc143.json`.
  Deterministic/review/backup checkpoint: `validation-checks.json`.
- Direct build workflow `65a61a3a1e1d4aa8b97d5cfd8f660b07`: Claude 2.1.292
  builds the duration utility, passes all 22 assertions, and reaches `VERIFIED`
  with zero corrections. Final source SHA-256:
  `04a3257cc786bd80035f3fbebbd36ff2138ade4fbbe1930e3d493b7b6539421b`.
- Debug workflow `c6ea23cab6f04eb2b5412d7f83475830`: a recorded controller
  fixture injection replaces the original Claude response with an off-by-one
  candidate. The required test fails on `0m0s`, then fails again on the exact
  candidate before the Codex 0.159.3 invocation. Codex cites the persisted
  failure digest and actual failing test, identifies the extra second, removes
  `+ 1`, and passes the identical 22-case command. One correction is consumed.
  Final source SHA-256:
  `f21ee9f4ea8ebff471f573602598acb5b188eec4265224e50a7416ad1f82fd80`.
- Both workflows match event replay and fresh CLI-process reads. Three real
  provider attempts have versions/session IDs, exit 0, and proven cleanup;
  requested/reported model names remain `UNKNOWN`. Four actual required-test
  executions retain individual launch/process/result facts. Original provider
  output, injection hashes, failed candidate, diagnosis, and final artifacts
  are separately evidenced in the ledger.
- `227` tests pass on Python 3.14. The checker compiles `62` Python files in
  memory and enforces the 500-line ceiling without writing bytecode.
- Independent reviewer `ses_ee7f92ba9ffeg54GHQqOnD6bKh`: scoped code/fake-only
  `PASS`, source-contract refinement `PASS`, and final real-receipt `PASS`.
  Findings fixed include ownership/publication races, unproven test cleanup,
  per-test evidence gaps, standalone module harness ownership, serialized
  context limits, UUID-order-dependent assertions, and truncated diagnostics.
- Supervisor cleanup now waits for an absent process group plus the async
  returncode notification, closes owned transports, and completes bounded
  cleanup despite repeated cancellation. Regressions cover flooded pipes,
  ignored TERM, leader-exit descendants, and retained partial output.
- `verified-backup.sqlite3` passes schema/integrity/foreign-key checks and
  matches every row in all 10 ledger tables: 2 workflows, 3 tasks/attempts/
  claims, 11 workflow events, and 32 evidence records. All task, attempt, and
  workflow replays match. Semantic row digest:
  `cfcbf698d13de3cad3341c0b356125d86af7200b9ba93e1c3be3359511839130`.
- `ruflo security scan` reports zero findings; its AgentDB controller-index
  warning remains recorded. The requested `@Codex-flow/cli` package previously
  returned npm E404; the installed `ruflo` scanner was used.

Earlier workflow failures remain in the parent evidence directory: the first
run exposed Unicode digit acceptance and a non-discriminating planted-bug
instruction; the next responses were quarantined for module-level regex
compilation/private attributes. The final fixture states its source shape
explicitly, validates both roles, records deterministic injection, and uses a
fresh run ledger. These records are historical evidence for earlier candidates.

Publication and SQLite updates are not one globally crash-atomic operation.
Partial/uncertain publication is quarantined; automatic repeat, release, and
resume are not implemented. Workflow state is separate from the foundation
task states. Dependency coordination, host capability/tool enforcement,
release roles, recovery/install acceptance, and Gemini confirmation remain
open.
The scoped checkpoint does not establish overall production readiness or
authorize Astro work.
