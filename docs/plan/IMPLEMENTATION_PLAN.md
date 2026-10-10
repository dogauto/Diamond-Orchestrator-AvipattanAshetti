# Implementation plan

**Plan status:** `PHASE_5_BOUNDED_SLICE_PASS; PHASE_6_NEXT`.
The documentation/configuration review is complete and the first foundation
slice has passed independent review. Current milestone evidence is recorded in
`../progress/STATUS.md`; the phase descriptions below are the baseline plan.

## Delivery method

Work in small slices: confirm contract -> bounded change -> meaningful tests
and build/package checks -> independent review -> record evidence -> next
slice. Fix blocking findings before expansion. Keep public interfaces typed,
runtime inputs validated, and files under 500 lines. Preserve human edits.

Default limits are two concurrent workers, a 900-second per-task timeout,
and two correction rounds. Task-specific validated limits may override
defaults before dispatch. Controller software enforces limits and records
consumption; providers do not set their own unlimited retry policy.

## Seven staged milestones

### 1. CLI compatibility and foundation

**Status:** `PASS` for the bounded foundation slice.

- Confirm macOS/Python 3.11+ baseline and project-local launcher design.
- Record executable resolution and safe version/help capabilities for all
  six providers, including distinct output formats and host prerequisites.
- Implement typed config parsing, `validate-config`, and diagnostic `doctor`.
- Implement the minimal task CLI and its persistent event-sourced core as
  specified below; this introduces the foundation of milestone 2.
- Exit only after foundation tests and fresh independent review pass.

`doctor` records binaries/versions and authentication `NOT_CHECKED`. Supplied
CLI help evidence is a compatibility starting point, not proven live readiness.

### 2. Persistent contracts and ledger

**Status:** `DISPATCH_RECOVERY_SLICE_PASS`; full workflow and recovery/install
acceptance remain for this milestone.

- Expand foundation task records into plan, attempt, artifact, test, review,
  and decision contracts with schema versions and boundary validation.
- Verify single-controller writer ownership, transactional event/projection
  updates, legal transitions, CAS, command deduplication, and pure replay.
- Add dependencies, blocked/resume prerequisites, correction budgets,
  migrations, and restart/integrity checks.
- Exit after deterministic concurrency/crash/persistence evidence and review.

### 3. Six live adapters

**Status:** `IN_PROGRESS`; deterministic invocation, supervision, result
normalization, provider-fix evidence, a macOS Omnirush workspace sandbox, and
the official Antigravity `agy` headless adapter exist. Gemini is quota-pending
for final confirmation, Codex passes its corrected smoke, Omnirush passes its
sandboxed smoke, and Antigravity passes its authenticated stream/structured
report smoke.

- Implement Gemini, Antigravity, Codex, Omnirush, Copilot, and Claude adapters separately.
- Normalize per-host noninteractive argv, safe versions, identity/session,
  captured text/JSON, exit/signal, auth/quota errors, and malformed results.
- Add asyncio supervision, deadlines, bounded output, and termination.
- Use deterministic fake executables first, then explicitly initiated isolated
  real CLI smoke tests with visible logs and sanitized durable evidence.
- Exit only with both adapter fault-injection and all-six live evidence.

Omnirush print text is known from supplied observations; JSON is unverified.
Do not assume a JSON mode or pretend all hosts share a response protocol.

### 4. Roles, skills, and context

**Status:** `ADVISORY_CATALOG_AND_PACKET_BINDING_PASS`; host capability and live routing evidence remain.

- Implement configurable role routing, verified skill provenance, and immutable
  accepted task packets.
- Current slice: `src/diamond/skills.py` and `src/diamond/packets.py` verify
  source digests, role applicability, dependency/prerequisite availability,
  stale plan/base/config digests, workspace ownership, symlink containment, and
  dependent artifact size/digest.
- Reference installed skills by path/version/digest/scope; the vendored
  `config/skills/catalog.toml` loads the pinned Superpowers and wshobson
  Markdown procedures by role, with no tool/delegation authority.
- Validate dependency context, plan/base digests, stale-plan rejection,
  workspace ownership, and actual permission enforcement limits.
- Support actual selective Council protocol and Task Observer observations
  without using either as the authoritative scheduler or task database.
- Exit after fake/live routing checks, stale-context tests, and review.

### Controller-owned evidence persistence — initial slice complete

**Status:** `DISPATCH_RECOVERY_SLICE_PASS`; full production recovery gates remain.

Schema-4 attempt/evidence/claim records now persist provider identity,
conversation/session IDs, requested/reported model, argv/cwd, timestamps,
exit/signal, normalized result status, structured output, stdout/stderr
digests, artifact-reference slots, and redaction metadata. The explicit
controller runner persists launch intent before external execution, start state
before `run`, completion facts after execution, and ambiguous cancellation or
exception states without blind relaunch.

Verified checks: immutable evidence, command deduplication, stale-attempt
rejection, restart reads, transaction rollback, bounded redacted output
digests, explicit schema-2 migration, and no synthetic credential persistence.
One-shot dispatch acquisition, worker caps, unresolved-attempt quarantine,
late-result retention, exact artifact verification, and backup/reopen checks
now pass. Bounded correction budgets are verified in milestone 5. Full workflow dependencies, installed skills,
clean installation, and independent release acceptance remain.

### 5. Builders and debuggers

**Status:** `BOUNDED_WORKFLOW_PASS`. Schema-5 workflow contracts, immutable
events/CAS/replay, controller-validated owned-file responses, trusted test argv,
failure reproduction, source-bound Debugger packets, and durable correction
limits are implemented. Each correction uses an explicit fresh task/session
and provider route. The current 227-test suite passes on Python 3.14 and the
checker compiles 62 Python files in memory.

Live candidate `63fdaa79a5a7eef748f741f2fef5b7b9df4d1ea70c10a611bd8dfc365a4bbf7e`
passes a direct Claude build and one Codex correction of a separately recorded
controller-injected off-by-one defect. Both final artifacts pass the same
22-case required test and fresh-process reads. Independent code/evidence
review and schema-5 backup/replay pass; the report and remaining scope are in
`../progress/STATUS.md`.

The slice remains response-based and uses trusted controller test commands.
Workflow state is separate from foundation task state. Blocked workflows
retain ownership/capacity; recovery-backed release/resume and dependency-aware
coordination are still required. Filesystem publication plus SQLite events
are not a single crash-atomic transaction.

- Dispatch bounded Builder packets with objective/files/tools/skills/tests.
- Drive Debuggers from actual failure packets through reproduction, supported
  root cause, bounded fixes, and discriminating regression checks.
- Enforce worker caps, timeouts, correction budgets, dependency order,
  artifact validation, ownership conflicts, and cancellation.
- Exit after deterministic fault scenarios and bounded live build/debug cases.

### 6. Integration, review, and acceptance

**Status:** `BOUNDED_LIVE_PASS`; full production release audit remains.

- Integrate exact base/patch revisions, resolving conflicts explicitly. The
  bounded live acceptance driver now passes rejected-upstream and corrected
  candidate scenarios through Integrator, QA, Reviewer, and Release, including
  replay, fresh-process, backup, and restored-certificate checks.
- Run independent QA on integrated artifacts and fresh Reviewer sessions.
- Track author lineage: no production-code author is its sole final reviewer.
- Bind test/review/release findings to candidate digests; invalidate stale
  evidence when bytes change and enforce all release-blocking findings.
- Exit after fake and live end-to-end acceptance on a bounded sample project.

### 7. Production recovery and install hardening

**Status:** `NOT_STARTED`.

- Reconcile launch-intent/start/completion gaps and ambiguous external work
  before relaunch; verify process identity, late results, and cleanup behavior.
- Verify ledger backup/replay/migration and clean macOS install/uninstall
  behavior appropriate to the chosen packaging approach.
- Run the complete acceptance matrix against the delivered revision and
  supported CLI versions; retain fake and real evidence independently.
- Exit only with no unresolved release-blocking findings and fresh independent
  release review. Only then authorize the separately scoped Astro project.

## First implementation slice after parent authorization

This session's first authorized implementation slice is deliberately local.
It does not start models, execute workflows, or automatically smoke-test AI.

| Surface | Required behavior |
| --- | --- |
| `doctor` | Resolve each command through `PATH`; bounded known-safe version probes; show missing/unsupported probes and auth `NOT_CHECKED` |
| `validate-config` | Parse schema 1 TOML; reject invalid types/keys/limits/routing; no provider calls or ledger writes |
| `task create` | Validate nonempty objective and optional supplied fields; persist a `CREATED` task plus event atomically; return ID/revision |
| `task list` | Read a deterministic task snapshot, including empty results; no mutation or dispatch |
| `task show <id>` | Read exact task state/revision; missing/invalid IDs produce clear nonzero diagnostics |
| `task events <id>` | Read ordered durable events with revisions; distinguish missing task from empty list |
| Ledger core | Legal non-execution transitions and expected-revision CAS; event/projection transaction; finite SQLite write locking; command deduplication and pure replay |
| Launchers | Project-relative, quoted paths; Python >=3.11 check; reliable argument/exit-code forwarding; no provider auto-run |

Creation saves an incomplete execution packet in `CREATED`. The foundation
rejects advancement into execution states until accepted-packet, dispatch, and
verification contracts exist. The initial CLI exposes creation and reads only.
Domain tests exercise blocking, cancellation, guarded resume-to-created, and
CAS. Scheduler ownership and external dispatch are later implementation work.

Required meaningful checks:

- Config negatives: malformed TOML, unsupported schema, bool-as-int, invalid
  limits, extra/missing keys, unknown/disabled role targets, and credentials
  rejected as unsupported configuration fields.
- CLI black-box create/list/show/events on a temporary ledger; verify a
  second process reads the same task/events after restart.
- Direct domain checks for illegal transitions and stale expected revisions,
  with unchanged event/projection state after rejection.
- Fault-injected transaction rollback between event append and projection
  update; compare persisted state and replay after restart.
- Competing writer/duplicate-command cases produce no double mutation.
- Fake version executables and a no-provider-call sentinel prove foundation
  commands do not launch model sessions; doctor uses only safe probes.
- Launcher behavior from a different cwd and a path with spaces; Python
  version failure is explicit; packaging/compile checks match selected design.

Use temporary project/test directories for verification, with deterministic
fake executables and standard-library tests where sufficient. Do not add
dependencies solely to satisfy a preferred framework. Record exact test
commands, actual exits/output, revision, and independent review findings.
Production readiness is not the exit condition of this foundation slice.

## Future live smoke protocol

Each real smoke test is initiated explicitly, identifies the provider and
intended operation visibly, uses an isolated target, and captures sanitized
argv, version, requested/reported identity, provider session, actual outputs,
exit/signal, artifacts, and cleanup. Authentication and quota results remain
separate from parse or workflow success. Failure or missing evidence blocks
that provider's live gate; it never triggers a hidden fallback or launch loop.

## Legacy evidence and handoff

The legacy Astro truncated controller is incident evidence: it motivates
termination, incomplete-output, verification, and recovery requirements.
The inspected source is `/Users/avipattan/new gen astro agents /engine/src/governance/controller.py`;
syntax parsing failed at line 407. Its incident record is
`/Users/avipattan/new gen astro agents /.ai/governance/INCIDENT-2026-10-02-CONTROL-PLANE-BUILDER-001.json`.
It is not reusable verified runtime.

The next implementation work is candidate-bound integration/QA/Reviewer/Release
coordination, host capability/tool enforcement, recovery-backed quarantine
handling, clean installation, and Gemini confirmation after an explicitly
confirmed quota reset. Foundation, packet, workflow, evidence, and smoke acceptance do not
establish overall production readiness.
