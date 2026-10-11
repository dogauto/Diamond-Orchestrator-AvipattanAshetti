# Diamond Orchestrator: Usage and Status

Status captured: 2026-10-11

## What this project is

Diamond is a local AI workflow orchestrator. It coordinates provider processes,
trusted tests, source ownership, workflow revisions, evidence, delivery gates,
and release decisions through a SQLite event ledger.

The core execution model is:

```text
Goal
  -> bounded plan
  -> persisted workflow
  -> builder provider
  -> deterministic tests and evidence
  -> debugger correction when allowed
  -> verification
  -> delivery and release gates
```

The provider is allowed to propose work. The controller decides whether the
proposal is valid, whether the workspace changed, whether tests actually pass,
and whether the result may advance.

## Project interfaces

- `scripts/diamond.sh`: primary command-line interface.
- `src/diamond/dashboard.py`: local web dashboard.
- `examples/DiamondMac/`: native macOS console.
- `src/diamond/mcp_server.py`: MCP stdio transport.
- `src/diamond/shell.py`: interactive shell.
- `.diamond/diamond.sqlite3`: local runtime ledger and event history.
- `config/diamond.toml`: provider routing and controller limits.

## Starting point

Run these commands from the repository root:

```bash
cd /Users/avipattan/flawless-diamond-piece

./scripts/diamond.sh --json validate-config
./scripts/diamond.sh --json doctor
./scripts/diamond.sh --json project status
```

`validate-config` checks the TOML configuration without launching providers.
`doctor` performs bounded provider `--version` probes. It does not prove
authentication or model access. `project status` shows providers, skills,
workflows, and deliveries from the controller.

## Dashboard

Start the local dashboard:

```bash
./scripts/diamond.sh dashboard --port 8765
```

Open `http://127.0.0.1:8765`.

The dashboard displays:

- provider probes and versions;
- verified, blocked, correction, and created workflows;
- queued task records and roles;
- schema version;
- refreshable API-backed state.

The current persisted dashboard data at the time of this status capture is:

```text
Providers probed:       6/6
Verified workflows:    17
Queued tasks:          29
Schema version:         1
```

The provider metric is intentionally named `Providers Probed`, because a
successful version probe does not establish authentication. Provider auth is
shown separately as `NOT_CHECKED` until an explicit live check is performed.

## Creating and running a workflow

Use a disposable workspace for the first run:

```bash
mkdir -p /tmp/diamond-demo/src /tmp/diamond-demo/tests
```

Create a trusted test:

```bash
cat > /tmp/diamond-demo/tests/check.py <<'PY'
from src.value import value

assert value() == 42
print("PASS")
PY
```

Create an initial source file:

```bash
cat > /tmp/diamond-demo/src/value.py <<'PY'
def value():
    return 0
PY
```

Create a workflow:

```bash
./scripts/diamond.sh --json workflow create \
  --objective "Implement value() so it returns 42" \
  --workspace /tmp/diamond-demo \
  --owned-path src/value.py \
  --test '{"name":"value regression","argv":["/usr/bin/python3","-B","tests/check.py"],"timeout_seconds":30,"output_limit":65536}' \
  --command-id demo-workflow-001
```

The response contains a workflow ID. Run one bounded step:

```bash
./scripts/diamond.sh --json workflow run WORKFLOW_ID \
  --expected-revision 1
```

Inspect the result and immutable event history:

```bash
./scripts/diamond.sh --json workflow status WORKFLOW_ID
./scripts/diamond.sh --json workflow events WORKFLOW_ID
```

Expected successful status:

```text
VERIFIED
```

If the provider writes an implementation that fails the trusted test, the
controller records the failure and may place the workflow in `NEEDS_CORRECTION`.
The debugger route can then run within the configured correction budget. A
workflow that cannot be safely corrected becomes `EXHAUSTED` or `BLOCKED`.

## Bounded autonomous workflow

Autonomy is implemented in `src/diamond/autonomy.py`. It is manually started,
finite, ledger-backed, and stoppable. It is not an unrestricted background
daemon.

### Plan a goal

```bash
./scripts/diamond.sh --json autonomy plan \
  --goal "Build and verify a value utility" \
  --workspace /tmp/diamond-demo \
  --owned-path src/value.py \
  --test '{"name":"value regression","argv":["/usr/bin/python3","-B","tests/check.py"],"timeout_seconds":30,"output_limit":65536}' \
  --command-id autonomy-demo-001
```

Planning asks the configured planner provider for a bounded list of objectives.
If that provider is unavailable or returns an invalid plan, Diamond uses a
deterministic fallback splitter. Planning creates validated workflows but does
not immediately publish source changes.

### Run the queue

Start conservatively:

```bash
./scripts/diamond.sh --json autonomy run \
  --max-steps 1 \
  --max-failures 0
```

The runner advances only workflows in `CREATED` or `NEEDS_CORRECTION` state.
Every step still uses provider supervision, workspace manifests, output
contracts, required tests, evidence, revision checks, and the existing
workflow verification gates.

Each run writes a report under the selected state directory as:

```text
autonomy-<run-id>.json
```

### Stop autonomy

From another terminal:

```bash
./scripts/diamond.sh --json autonomy stop
./scripts/diamond.sh --json autonomy clear-stop
```

Autonomy stops when it reaches its step budget, exceeds its failure budget,
receives a durable stop request, encounters an unavailable provider, detects a
workspace conflict, or reaches a blocked/quarantined workflow.

## Hallucination-resistance model

Diamond does not treat provider prose as proof. The controller verifies:

- exact response schemas;
- declared owned paths;
- workspace manifests and source digests;
- trusted test commands;
- actual process exit status;
- output parsing and artifact digests;
- provider session and attempt identity;
- cleanup completion;
- workflow revision consistency;
- delivery requirements and findings;
- immutable evidence and event history.

Provider claims cannot override failing executable tests or stale evidence.
Conflicting writers are blocked by workflow and dispatch ownership checks.
Publication failures restore changed files where possible and retain quarantine
state when recovery is not proven.

## Reverify comparison

The external project inspected for this work is:

<https://github.com/2akouwu/reverify>

Reverify follows a complementary model:

```text
Model proposes a claim
  -> deterministic tool checks ground truth
  -> VERIFIED / REFUTED / INCONCLUSIVE
  -> evidence is recorded in a durable ledger
```

Its notable ideas are:

- facts are stored only after deterministic verification;
- refuted claims are retained as negative memory;
- verified facts survive context resets;
- facts have strength tiers such as proven, tested, verified, observed, and
  analysis-derived;
- MCP tools expose the verification loop to agents;
- context hand-offs label model notes as unverified instead of treating them as
  facts.

### Integration status

Reverify is **not installed or integrated into Diamond yet**. Diamond currently
uses its own controller, evidence, test, artifact, and delivery gates.

The natural future integration is a Diamond grounding adapter that turns
provider assertions about source behavior, files, APIs, or binaries into
deterministic verification requests. Only `VERIFIED`, `TESTED`, `PROVEN`, or
explicitly `OBSERVED` results with evidence should be eligible for the grounded
fact ledger. `REFUTED` and `INCONCLUSIVE` results should block or return work
for correction rather than being summarized as facts.

Reverify is especially valuable for binary claims, byte-level claims,
behavioral equivalence, typed reads, disassembly, and other assertions that
ordinary unit tests do not cover. Diamond's current required-test and artifact
gates remain the source-code verification path until that adapter exists.

## Verification completed

The following checks passed during the current implementation session:

```text
Full controller suite:       306 tests passed
Stress suite:                 18 tests passed
Python files compiled:        79
Swift DiamondMac build:       passed
Shell syntax checks:          passed
Dashboard HTTP/API checks:    passed
git diff --check:             passed
```

The stress coverage includes:

- duplicate command retries;
- concurrent task and workflow contention;
- cross-workflow file ownership races;
- dispatch capacity races;
- SQLite lock timeouts;
- killed writers and transaction rollback;
- process timeout, cancellation, nonzero exit, and output limits;
- immutable evidence tampering attempts;
- restart reconciliation and quarantine;
- workflow and delivery publication rollback;
- preservation of intervening human edits.

## Current limitations

The project is operational for deterministic local and test-backed paths, but
it is not yet a fully live production service.

1. Provider authentication and paid/live model execution remain unverified.
2. Reverify has been analyzed but is not yet a Diamond dependency or adapter.
3. SQLite and filesystem publication cannot be made fully crash-atomic by the
   current in-memory rollback alone. Quarantine and reconciliation handle
   uncertain interruption.
4. Sequential autonomous workflows sharing one workspace must respect fresh
   workspace manifests. Diamond refuses stale edits instead of overwriting
   newer source, so multi-step plans may require explicit refresh and
   acceptance between changes.
5. The documented Music Player example remains unavailable because its
   `examples/MusicPlayer` directory is missing.
6. Real MCP-client interoperability, clean-install verification, and live
   authentication still need environment-specific checks.

## Useful inspection commands

```bash
./scripts/diamond.sh --json workflow list
./scripts/diamond.sh --json delivery list
./scripts/diamond.sh --json task list
./scripts/diamond.sh --json project status
./scripts/diamond.sh --json autonomy --help
./scripts/check.sh
```

The runtime state is local to the selected state directory. Use
`--state-dir /tmp/some-isolated-state` for experiments that should not touch
the repository's `.diamond` ledger.
