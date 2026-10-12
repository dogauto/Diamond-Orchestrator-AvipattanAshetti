# Flawless Diamond Piece documentation

**State: `PHASE_6_NEXT`.** The requested documentation and TOML
configuration are complete, the bounded foundation slice passed independent
review, and deterministic adapter/packet slices are implemented. See
`progress/STATUS.md` for actual results.

## Navigation

| Document | Purpose |
| --- | --- |
| [Project aim](aim/PROJECT_AIM.md) | Objective, delivery order, scope and success criteria |
| [System design](architecture/SYSTEM_DESIGN.md) | Python modules, contracts, SQLite state, adapters and recovery |
| [Roles and skills](architecture/ROLES_AND_SKILLS.md) | Assignments, bounded work, skill provenance and independent review |
| [Implementation plan](plan/IMPLEMENTATION_PLAN.md) | Seven milestones and first authorized implementation slice |
| [Dynamic agents plan](plan/DYNAMIC_AGENTS_PLAN.md) | Council-advised profile composition, provider routing, daily improvement, and acceptance milestones |
| [Dynamic agents Council record](decisions/dynamic-agents-2026-10-12/README.md) | Verdict, tradeoffs, unresolved questions, and recorded deliberation |
| [Phase gates](verification/PHASE_GATES.md) | Purpose, checks, evidence, pass/fail/block rules per phase |
| [Acceptance criteria](verification/ACCEPTANCE_CRITERIA.md) | Falsifiable foundation, fault, live and release requirements |
| [Status](progress/STATUS.md) | Initial state, supplied environment observations and unknown evidence |
| [Configuration](../config/diamond.toml) | Portable schema 1 defaults and provider/role mappings |

Read aim -> design/roles -> plan -> verification -> status/config before
implementation. Production delivery order is macOS orchestrator acceptance,
then the separately approved actual Astro project.

## Project materials and ownership

- `docs/`: planning, decisions, verification summaries, progress; no root docs.
- `config/diamond.toml`: portable configuration, no credentials or model IDs.
- `config/skills/`: pinned, MIT-licensed advisory skill sources and provenance
  catalog; changed source bytes block packet preparation.
- `src/`, `tests/`, `scripts/`, `examples/`: runtime and verification code.
- Runtime state: `.diamond/diamond.sqlite3` and an evidence area
  beside it. These are runtime-generated materials, not created by these docs.

The controller is deterministic software. Role assignments select CLI
providers; requested/reported model identities remain distinct. Neither role
text nor skill instructions authorize executable tools or release acceptance.

## Local launch instructions

The easiest local entry point is:

```sh
cd /Users/avipattan/flawless-diamond-piece
./scripts/local.sh
```

It checks the controller, builds the native macOS console, and opens it. The
same launcher provides the useful maintenance commands:

```sh
./scripts/local.sh status
./scripts/local.sh doctor
./scripts/diamond.sh --json project status
./scripts/local.sh demo
./scripts/local.sh music-player
./scripts/local.sh check
```

The macOS console is a local operations surface. It reads and changes state
through the Python controller; it does not call provider CLIs directly.
Do not start it with `swift run DiamondMac` when you want your terminal back;
that command intentionally keeps the terminal attached to the app process.

The console's **Logs** section reads immutable workflow events directly from
the controller. Select a project workflow or paste any workflow ID, including
a gated self-improvement workflow ID, to inspect its revisions, timestamps,
commands, and recorded transition details without opening a terminal.

`./scripts/local.sh demo` creates a disposable `/tmp/flawless-diamond-demo`
workspace, records one workflow, and attempts one bounded provider step. It
does not modify this repository. If provider authentication is unavailable,
the workflow remains recorded and the command reports the provider error.

`./scripts/local.sh music-player` builds and opens the separate local SwiftUI
Music Player example. It plays audio files you explicitly import from your
Mac; it does not scan folders or upload media.

## Agent launch through MCP

The controller also exposes a thin Model Context Protocol stdio adapter. MCP
is an interface to the controller, not a replacement for its validation,
SQLite ledger, evidence checks, or provider supervision. The adapter exposes
inspection tools plus explicitly confirmed workflow creation, execution, and
reconciliation tools.

Configure an MCP client to launch:

```sh
/Users/avipattan/flawless-diamond-piece/scripts/diamond.sh \\
  --project /Users/avipattan/flawless-diamond-piece mcp --stdio
```

The transport is newline-delimited JSON-RPC. The client must complete the MCP
`initialize` handshake before using tools. Read-only tools inspect project
status, workflows, immutable events, deliveries, and configuration. Mutating
tools require `confirm: true` and remain subject to the same controller gates;
client-side human approval is still required before provider execution.

## Gated self-improvement

The orchestrator now exposes a gated self-improvement path. It can ask the
configured Builder and Debugger roles to change existing Python source, but
the controller rejects traversal, non-Python files, tests, configuration,
launchers, the ledger, provider diagnostics, and supervisor safety code.
Each request snapshots source, dependencies, policy, test inputs, and file
modes into an isolated workspace. Its durable journal uses the project's
`.diamond/` ledger by default; `--state-dir` selects another persistent ledger.

Example:

```sh
./scripts/diamond.sh --json self-improve create \
  --objective "Improve the workflow status explanation" \
  --owned-path src/diamond/workflow_cli.py \
  --test '{"name":"controller suite","argv":["/bin/sh","scripts/check.sh"],"timeout_seconds":900,"output_limit":65536}' \
  --command-id improve-status-1
```

The response includes a workflow ID and checkpoint path. Run one bounded,
test-gated improvement step with the returned ID:

```sh
./scripts/diamond.sh --json self-improve run WORKFLOW_ID --expected-revision 1
```

`run` stops at a verified isolated candidate. Create an independent review,
then advance the returned delivery ID through Integrator, QA, Reviewer, and
Release using the current revision returned after each step:

```sh
./scripts/diamond.sh --json self-improve review WORKFLOW_ID --command-id improve-review-1
./scripts/diamond.sh --json delivery step DELIVERY_ID --expected-revision REVISION
./scripts/diamond.sh --json delivery certificate DELIVERY_ID
./scripts/diamond.sh --json self-improve publish WORKFLOW_ID --delivery-id DELIVERY_ID
```

Only `publish` can promote the exact independently accepted bytes. It checks
that live dependencies, tests, policy, and source still match their accepted
base, then records publication intent before replacing any file.

Inspect, cancel, recover, or explicitly roll back:

```sh
./scripts/diamond.sh --json self-improve status WORKFLOW_ID
./scripts/diamond.sh --json self-improve events WORKFLOW_ID
./scripts/diamond.sh --json self-improve cancel WORKFLOW_ID --reason "Stop this improvement"
./scripts/diamond.sh --json self-improve reconcile WORKFLOW_ID
./scripts/diamond.sh --json self-improve rollback WORKFLOW_ID
```

`status` shows the current state, isolated workspace, checkpoint, and publication journal.
`events` shows the immutable execution log, including provider-step starts,
workspace file application, verification results, and failure details. Both
inspection commands include publication events. Cancellation persistently
revokes promotion; it does not pretend an already running provider has exited.

After an interrupted publication, `reconcile` restores the journaled originals.
Rollback restores only files whose bytes and modes still match the installed
candidate. Newer edits are preserved and the operation remains blocked for
explicit reconciliation. An unpublished, unchanged candidate rolls back as a
no-op. Filesystem replacement and SQLite are not one atomic transaction; the
journal records and recovers the intermediate state.

Schema 8 adds immutable improvement records and evidence indexes. Existing
ledgers require the explicit `./scripts/diamond.sh --json migrate` command
(include `--state-dir` for an external ledger). No automatic migration occurs.
Historical checkpoints in `/tmp/flawless-diamond-self-improve-state` remain
inspectable through `workflow show/events` after explicit migration of that
ledger; they require a new improvement request before promotion because they
lack retained project/base identity. New checkpoints use durable project state.

This is not unrestricted self-training. Learning is retained as workflow
evidence and verified source changes. The controller never grants the model
permission to rewrite its own safety boundary or silently publish a failing
change.

## Daily improvement analysis

The read-only analyzer verifies persisted evidence digests and schemas, reads
an indexed row/byte-bounded snapshot, and scopes evidence to this project or
its recorded improvement workspaces. It retains all citations while counting
attempts and known task lineages separately:

```sh
./scripts/diamond.sh --json daily-improve analyze \
  --window-hours 168 \
  --max-candidates 5 \
  --max-records 1000 \
  --max-bytes 8388608
```

Report schema 2 includes source identity, cutoff/watermark, excluded scope,
truncation, provider version/model observations, and historical candidates.
Scores and `evidence_support` are heuristic prioritization, not confidence
probabilities. Reviewer findings remain unadjudicated claims. Later passing
tests are retained as observations without erasing the original failures.

Record a candidate decision using identifiers from that report:

```sh
./scripts/diamond.sh --json daily-improve disposition \
  --signature SIGNATURE --source-digest SOURCE_DIGEST \
  --status deferred --reason "Await a reproducible baseline" \
  --evidence-id EVIDENCE_ID --cooldown-hours 24 --command-id candidate-decision-1
```

Statuses are `resolved`, `inapplicable`, `failed_experiment`, and `deferred`.
Dispositions are immutable, evidence-cited, idempotent by command ID, and
scoped to the current source digest. Cooldown expiry or source changes permit
requalification. Discovery and dispositions do not launch providers or publish
source; automated Researcher/Planner orchestration remains a later milestone.

## M2 profile preview

Two digest-pinned instruction profiles are available for the fixed Claude
Builder route: Python parsing and SQLite transactions. These are project-
authored, coding-agent contract-reviewed instructions with **unevaluated**
performance. They specialize one worker; they grant no additional tools,
ownership, test authority, or workers.

Preview against an existing, unstarted workflow:

```sh
./scripts/diamond.sh --json profile preview \
  --workflow-id WORKFLOW_ID --tag parser --tag python
```

This reuses the workflow's accepted source, required tests, pinned skills,
fixed provider and limits without changing its revision. Use `--tag sqlite
--tag transaction` for the other profile. Contradictory/ambiguous selections
are rejected. No match preserves the exact legacy packet and prompt.

For an objective-only preview, use a bounded workspace:

```sh
./scripts/diamond.sh --json profile preview \
  --objective "Inspect parser boundary handling" \
  --workspace /absolute/path/to/bounded-workspace --tag parser --tag python
```

The result includes selection/rejection reasons, catalog/profile digests,
effective prompt bytes and digest, packet digest, and limits. The objective-
only form has no owned-source context. Both forms are read-only and make no
provider calls. Profiled packets use schema 2; schema-1 bytes and historical
dispatch hashes remain unchanged. Catalog or source drift invalidates a
compiled profile, and authorization compares actual provider arguments with
the bound prompt/tool contract.

Prepare the operator-owned trial design:

```sh
./scripts/diamond.sh --json profile trial-template
./scripts/diamond.sh --json profile trial-validate --file /absolute/path/to/trial-design.json
```

Copy the template's `result.design` object into your JSON file and fill its
empty/null values. It includes the hypothesis, real workload, lineage split,
holdout/assignment procedure, blinded acceptance, evaluator identity,
all-assignment/unknown-outcome accounting, privacy/provider eligibility,
assignment/call/time budgets, maturation period, stopping rules, economic
hurdle, and owners. The catalog digest pins the two interventions.

`structurally_valid: true` means the required fields and identity checks pass;
it does not validate the study's quality or authorize execution. Live profile
dispatch remains blocked until the approved design is registered and provider
result identity/accounting is connected. The persisted dry-run surfaces are:

```sh
./scripts/diamond.sh --json profile trial-register \
  --file /absolute/path/to/trial-design.json --command-id trial-register-1
./scripts/diamond.sh --json workflow create \
  --objective "Bounded parser task" --workspace /absolute/path/to/workspace \
  --owned-path src/parser.py --test '{"name":"smoke","argv":["/usr/bin/python3","-c","assert True"],"timeout_seconds":30,"output_limit":4096}' \
  --task-tag parser --task-tag python --trial-opt-in TRIAL_ID \
  --command-id workflow-trial-1
./scripts/diamond.sh --json profile trial-assign TRIAL_ID \
  --workflow-id WORKFLOW_ID --lineage-key LINEAGE-1 --arm builder-python-parser \
  --reserved-provider-calls 2 --reserved-wall-clock-seconds 300 \
  --command-id assignment-1
./scripts/diamond.sh --json profile trial-show TRIAL_ID
```

Registration, assignment, cancellation and outcome commands are accounting-only
and always report provider launch as false. The current outcome command is a
local fixture/operator accounting surface; live authorization must derive usage,
cost, elapsed time and unknown status from controller evidence. No live trial
has run.

## Foundation launch instructions

The commands below operate the verified foundation slice. They do not launch AI
workflows. Examples assume the project directory is the working directory; the
launcher also supports invocation from another current directory.

Prerequisites are a verified Python 3.11+ environment and project-local
launcher. Providers are resolved by command names on `PATH`, not saved
machine-specific locations. Installer/package commands are undecided.

```sh
./scripts/diamond.sh --config config/diamond.toml validate-config
./scripts/diamond.sh --config config/diamond.toml doctor
./scripts/diamond.sh --config config/diamond.toml task create --objective "Record a bounded example task"
./scripts/diamond.sh --config config/diamond.toml task list
./scripts/diamond.sh --config config/diamond.toml task show <task-id>
./scripts/diamond.sh --config config/diamond.toml task events <task-id>
./scripts/diamond.sh --json provider configure freellmapi --url http://127.0.0.1:31415
./scripts/diamond.sh --json provider models freellmapi
```

Replace `<task-id>` with the ID returned by `task create`; angle brackets
are notation, not literal shell arguments. The launcher forwards arguments
and exit status, validates Python, and resolves project-relative state paths.
Success is exit zero; validation/lookup/conflict failures are nonzero with
clear diagnostics. Exact machine-readable CLI output is finalized in slice 1.

`doctor` checks binary availability and safe version probes only; authentication
is `NOT_CHECKED`. `validate-config` and task operations do not execute AI.
FreeLLMAPI is configured separately because it is an HTTP gateway rather than a
local executable. `provider configure` stores only its loopback URL, selected
credential environment-variable name, and optional model under `.diamond/`;
the credential is never written to the Diamond ledger or portable TOML. The
local gateway authentication contract uses the `X-API-Key` header. Model
discovery is a bounded authenticated `GET /v1/models`; a live smoke request
requires an explicit `--confirm`.

The upstream FreeLLMAPI npm CLI configures Claude Code. Prefer supplying a
rotated key through `FREELLMAPI_API_KEY` rather than `--api-key`, review the
settings file it updates, and run the requested setup command:

```sh
npx freellmapi setup-claude --url http://127.0.0.1:31415
```

Do not pass a real key in shell history, command output, or screenshots.
Diamond intentionally does not publish a package named `freellmapi` or shadow
the upstream command. The upstream CLI discovers the gateway catalog and owns
its Claude configuration. Diamond's Python controller remains the authority
for its own non-secret gateway config, bounded `/v1/models` discovery, explicit
smoke confirmation, workflow dispatch, evidence, and release decisions.
Creating a task stores it as `CREATED`; it is not a workflow launch command.
Packet validation and provider smoke evidence are partial milestone slices;
workflow dispatch, recovery, and release operations remain later gates.

### Foundation verification

The foundation uses meaningful standard-library unit and black-box checks:

```sh
./scripts/check.sh
```

Do not treat an empty discovered suite as acceptance. The current supported check runs the repository's discovered tests on Python 3.11+ and compiles every Python source file in memory.
The independent `ACCEPT_FOUNDATION` review covers the foundation. Build/package checks are selected with
the implementation layout, not presumed to be npm commands for this Python
controller. Refer to the remaining phase gates for the unimplemented runtime.

## Readiness and next action

Installed CLI paths/help are prior observations with no proven authentication
or model-live readiness. Deterministic fake executables and real CLI tests
serve different verification purposes. Both, plus production recovery and
clean-install evidence, are required before orchestrator production readiness.

Next: install/authenticate and verify the Antigravity `agy` adapter, then complete the six-provider live gates, subprocess recovery,
role capability checks, integration review, and packaging/release gates. The Astro project remains blocked until those later gates pass. The
legacy Astro truncated controller remains incident evidence rather than
reusable runtime.
