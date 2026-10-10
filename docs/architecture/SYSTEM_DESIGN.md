# System design

**Design status:** specification reviewed; the bounded foundation slice is authorized.
Implementation and verification status are tracked in `../progress/STATUS.md`.

## Local architecture

Python 3.11+ is the foundation. Use standard-library `dataclasses`, enums,
`tomllib`, `sqlite3`, and `asyncio` initially; typed contracts also require
explicit runtime validation. Keep public interfaces typed and modules under
500 lines. No remote control plane or service decomposition is required.

| Module boundary | Responsibility |
| --- | --- |
| CLI/config | Parse commands, validate input/config, print structured diagnostics |
| MCP bridge | Thin newline-delimited JSON-RPC adapter over the CLI/controller; no direct provider or ledger bypass |
| Task domain | Contracts, legal transitions, dependency and correction rules |
| Ledger | Single-writer transactions, append-only events, projections, replay |
| Controller | Scheduling, dispatch claims, capacity, evidence and policy gates |
| Providers | Six capability-aware CLI adapters and result normalization |
| Supervisor | Async process lifecycle, output capture, deadlines, termination |
| Context/skills | Versioned plans, role packets, pinned advisory skill catalog, host prerequisites |
| Workspace/evidence | Path ownership, artifact digests, tests, review provenance |

The future flow is CLI request -> validation -> ledger command -> controller
dispatch -> adapter/supervisor -> evidence validation -> ledger transition.
The foundation exposes task records only; it does not execute this workflow.

## Validated contracts

Every external boundary validates types, required fields, enums, schema
version, identifier syntax, finite limits, and canonical paths before use.
Reject unknown fields rather than silently dropping them. Serialize durable
contracts as schema-versioned JSON inside SQLite, not executable objects.

| Contract | Required information |
| --- | --- |
| `TaskSpec` | Task ID, objective, acceptance conditions, role, dependencies, owned paths, allowed tools/skills, required tests, timeout, correction limit |
| `TaskSnapshot` | Task ID, state, monotonic revision, plan revision/digest, current attempt, correction rounds used, blocking reason |
| `PlanRecord` | Objective/tasks, dependencies, accepted revision, input/base digests, config and skill digests, owner acceptance provenance |
| `ExecutionAttempt` | Unique attempt ID, task/revision, provider/role, requested and reported model identities, executable/CLI version, provider session ID, workspace, dispatch/start/end times, process identity, outcome |
| `WorkerReport` | Schema version, attempt ID, claimed result, changed paths, artifact references, test claims, findings; all claims remain untrusted |
| `ArtifactRecord` | Attempt, canonical relative path, kind, byte size, SHA-256 digest, source revision, observation time |
| `TestEvidence` | Attempt/revision, exact command argv/cwd, relevant sanitized environment, start/end, actual exit code or signal, timeout, stdout/stderr references and digests |
| `ReviewRecord` | Reviewer role/provider/session, author provenance, exact artifact/base digest, findings with severity and resolution evidence |
| `LedgerEvent` | Event ID, schema version, task ID/revision, event type, timestamp, controller actor, correlation/command ID, validated payload |

The table describes complete execution contracts. A `CREATED` task may store
only ID/objective and explicitly unset execution fields; it cannot become
`READY` until those fields form a validated accepted packet. Mutable ledger
revision is the CAS token, distinct from frozen plan/packet revision. Reports
match the current attempt and frozen packet; controller mutations additionally
check the latest ledger revision, which advances as lifecycle facts are saved.

Store requested model identity separately from identity reported by the CLI.
Use `UNKNOWN` when either is unavailable; never infer a model from a role,
binary name, requested option, or marketing label. Record the identity source
and any explicit mismatch. An unreported version/session uses `UNKNOWN` with
a reason; never generate a value that looks provider-issued.

Artifact digests bind verification and review to exact bytes. Preserve the
checked revision until acceptance or invalidate its evidence on any change.
Tests have actual exit/output evidence; an unrun test has no exit code and
cannot be `PASS`. Redacted output retains exit semantics and notes redaction
or truncation. A successful process exit alone does not mean task acceptance.

## Configuration schema 1

`config/diamond.toml` is portable and contains no credentials or model IDs.

| Field | Validation/meaning |
| --- | --- |
| `schema_version` | Integer exactly `1`; reject unsupported versions |
| `project_name` | Nonempty string identifying Flawless Diamond Piece |
| `controller.max_workers` | Positive integer, default `2`, global worker cap |
| `controller.task_timeout_seconds` | Positive integer, default `900` |
| `controller.correction_round_limit` | Nonnegative integer, default `2` |
| `providers.<name>.executable` | Nonempty command name resolved through host `PATH` |
| `providers.<name>.enabled` | Boolean, all six initially `true` |
| `roles.<role>` | Provider-name string referring to an enabled provider |

Reject booleans masquerading as integers, missing/extra fields, malformed
TOML, and unknown provider/role names. Schema 1 requires the six provider
tables and nine model-backed role mappings. `controller` is software, never
a role-to-model mapping. Future schema changes require explicit migration.
Task-specific validated timeouts and correction limits override defaults
and are frozen in the accepted task packet; config changes cannot silently
alter an active attempt. Enabling a provider does not prove readiness.

## Event ledger, projections, and single writer

Use a local SQLite database at the project-relative path
`.diamond/diamond.sqlite3`; runtime state is separate from portable configuration.
The launcher resolves paths from the project location, not the caller's cwd.

- The controller is the only authoritative writer. Workers submit reports.
  The foundation CLI is a short-lived controller for local ledger commands.
- Mutations hold SQLite's transaction write lock with a finite busy timeout.
  Overlapping commands serialize, and CAS rejects stale revisions. A long-lived
  scheduler will additionally claim exclusive controller ownership before worker
  launch in a later slice. Read-only queries use a consistent snapshot.
- Each mutation uses `BEGIN IMMEDIATE`, checks `expected_revision`, validates
  the transition, appends its event, updates projections, then commits once.
- Creation starts at revision `1` with its creation event. Subsequent accepted
  mutations increment the task revision and record the corresponding event.
  Enforce uniqueness of `(task_id, revision)` and command deduplication IDs.
- On validation/CAS conflict or persistence failure, roll back the entire
  mutation. Never leave a projected state without its event, or vice versa.
- Events are immutable historical facts. Replay deterministically rebuilds
  task/attempt projections without executing commands or launching workers.
- Configure foreign keys, durable commits, busy handling, and migrations
  explicitly. Verify backup/recovery and replay before claiming durability.
  Do not assume SQLite alone resolves external subprocess ambiguity.

A repeated accepted command ID returns the original result if its payload
matches; a different payload with that ID is a conflict. This deduplicates
ledger commands, not external model execution.

## Task lifecycle

These are domain states, distinct from project milestone/gate statuses.

| From | Permitted destinations |
| --- | --- |
| `CREATED` | `READY`, `BLOCKED`, `CANCELLED` |
| `READY` | `RUNNING`, `BLOCKED`, `CANCELLED` |
| `RUNNING` | `VERIFYING`, `FAILED`, `BLOCKED`, `CANCELLED` |
| `VERIFYING` | `REVIEWING`, `READY`, `FAILED`, `BLOCKED`, `CANCELLED` |
| `REVIEWING` | `SUCCEEDED`, `READY`, `FAILED`, `BLOCKED`, `CANCELLED` |
| `BLOCKED` | Recorded resume state, `FAILED`, `CANCELLED` |
| `SUCCEEDED`, `FAILED`, `CANCELLED` | None; further work creates a linked task |

State adjacency is necessary but insufficient. `READY` requires an accepted,
current packet and satisfied dependencies. `RUNNING` requires an exclusive
dispatch claim and available capacity. Correction returns to `READY` only
after closing/reconciling the previous attempt, updating the plan if needed,
and incrementing the correction-round counter within its limit. Exhaustion
produces `FAILED` or an explicit unresolved `BLOCKED`, never extra retries.

`BLOCKED` records a reason, resume state, and evidence prerequisites. Recovery
must validate those prerequisites before resuming; it cannot jump to success.
`SUCCEEDED` requires verified artifacts/tests and accepted independent review.
Cancellation/timeout requires confirmed completion or termination of active
external work before the task becomes terminal; uncertain cleanup remains
quarantined in `BLOCKED`. No state transition is inferred from prose.
The first slice creates `CREATED` tasks and queries them. Internal blocked,
cancelled, and resume-to-created transitions are verified with CAS and reasons.
Execution-state transitions remain unavailable until accepted packets, dispatch
claims, and evidence guards exist. The foundation cannot mark work `SUCCEEDED`.

## Future subprocess and adapter behavior

Use `asyncio.create_subprocess_exec` with argv, explicit cwd, controlled
environment, and concurrent bounded stdout/stderr capture. No implicit shell
interpolation. Apply deadlines and output limits; record overflow and cleanup
errors. Use process groups and bounded graceful-then-forced termination on
macOS; verify tracked children have exited or block ambiguous cleanup.

Each adapter owns capability probes, safe version invocation, noninteractive
argv construction, output parsing, identity/session extraction, and normalized
outcomes. Never treat JSON flags as shared across providers. Current supplied
  help observations indicate noninteractive modes for all six; Omnirush has
print text, with no verified JSON mode. Its adapter must keep captured text
and validate a separately delimited report if supported; unsupported or
malformed output is a visible failure, never invented structured evidence.

Normalize auth/quota errors, invalid output, nonzero exit, timeout, signal,
missing artifacts, and ambiguous completion separately. Live tests establish
per-host behavior and exact supported versions. Future model smoke tests use
isolated workspaces, explicit initiation, and visibly logged execution.

`doctor` in the foundation resolves binaries and uses known safe version
probes only. It reports authentication `NOT_CHECKED`, not readiness. If no
safe version probe is known, report that limitation without opening a model
session. No config validation or task creation triggers provider execution.

## Dispatch and recovery

Persist attempt ID and dispatch intent before launching. Claims are unique
for the task revision, with a controller ownership generation. Persist
process-start observations immediately, including PID/start identity,
executable, workspace, process group, and reported session where available.

There is an unavoidable gap between intent, external launch, and recorded
completion. After interruption, reconcile incomplete attempts using durable
logs/artifacts, verified process identity, and supported provider session
inspection. A PID alone is unsafe because it may be reused. An absent local
process does not prove a provider operation never happened.

If the outcome is uncertain, keep `BLOCKED` with recovery evidence and a
recorded reconciliation decision. Do not relaunch until the previous attempt
is classified as completed, safely terminated, or proven not started. If it
cannot be classified, it remains blocked for explicit reconciliation.
Record late results and reject stale mutations via attempt/revision checks.
Local claim deduplication provides no exactly-once external execution promise.

## Workspaces, skills, and authorization

Route roles and needed skills through validated, versioned packets described
in [Roles and skills](ROLES_AND_SKILLS.md). The optional project-local catalog
at `config/skills/catalog.toml` vendors four MIT-licensed Markdown procedures
from pinned GitHub commits. `skill_catalog.py` verifies source containment and
SHA-256 provenance, resolves dependencies by role, and places bounded guidance
in provider context. Guidance is untrusted process data: it cannot authorize
commands or bypass the controller. Use separate task workspaces or Git
worktrees when the target supports them; integration owns merging.
Canonicalize owned paths and reject traversal, symlink escape, and writes
outside the permitted target. Check actual changed paths against ownership.

Path ownership and worktrees coordinate writes; they are not by themselves
sandboxes. Runtime tools require executable allowlists and verifiable host
permission enforcement. If a required restriction cannot be enforced, block
the task or use an explicitly approved constrained execution mechanism.
Do not grant permissions because an agent requests them in a report.

Strip secrets before persisting console, provider, test, or error output.
Use controlled environment disclosure, redact tokens/credential-bearing
values, and test redaction with synthetic secrets. Do not persist unredacted
fallback logs. Evidence records retain command structure and actual outcomes,
with sensitive argv/environment values replaced by explicit redaction markers.

## Design choices still requiring implementation evidence

Exact provider flags/output framing, supported-version policy, model/session
reporting, host permission mechanisms, package/installer approach, migrations,
backup procedure, and any future external skill-catalog migration must be
established in verified slices. The legacy Astro truncated controller is incident evidence,
not a reusable verified runtime or proof these contracts already work.
