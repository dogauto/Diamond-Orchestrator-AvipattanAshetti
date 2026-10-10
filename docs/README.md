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
Every request uses a separate state directory, snapshots owned files first,
and requires the existing workflow test and evidence gates.

Example:

```sh
./scripts/diamond.sh --json self-improve create \
  --objective "Improve the workflow status explanation" \
  --owned-path src/diamond/workflow_cli.py \
  --test '{"name":"controller suite","argv":["/usr/bin/python3","-m","unittest","discover","-s","tests"],"timeout_seconds":120,"output_limit":65536}' \
  --command-id improve-status-1
```

The response includes a workflow ID and checkpoint path. Run one bounded,
test-gated improvement step with the returned ID:

```sh
./scripts/diamond.sh --json self-improve run WORKFLOW_ID --expected-revision 1
```

Inspect or explicitly roll back the owned files:

```sh
./scripts/diamond.sh --json self-improve status WORKFLOW_ID
./scripts/diamond.sh --json self-improve events WORKFLOW_ID
./scripts/diamond.sh --json self-improve rollback WORKFLOW_ID
```

`status` shows the current state, temporary workspace, and checkpoint path.
`events` shows the immutable execution log, including provider-step starts,
file publication, verification results, and failure details.

This is not unrestricted self-training. Learning is retained as workflow
evidence and verified source changes. The controller never grants the model
permission to rewrite its own safety boundary or silently publish a failing
change.

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
