# Council M2 profile trial infrastructure

Date: 2026-10-12. Source baseline at start of this slice: `424a7b4`.
Status: **offline profile foundation and accounting infrastructure verified; live M2 pilot incomplete**.

## Delivered

- `config/agents/catalog.toml`: two source-digest-pinned Claude Builder
  profiles. Review provenance explicitly names coding-agent contract review,
  not human approval; performance remains unevaluated.
- `agent_profiles.py`: bounded strict catalog loading, canonical contained
  files, source/policy drift rejection, deterministic explicit tags, conflict
  rejection, and reasons for each alternative. No match uses legacy behavior.
- `agent_compiler.py`: binds advisory text to an existing response-only
  packet without extending ownership, tools, tests, output contract or limits.
  It retains profile, selection, base prompt, and effective prompt bytes.
- `packets.py`: explicit schema-2 profile identities and strict decoding;
  schema-1 serialization and no-match packet bytes remain identical. Verify
  checks profile/catalog drift. Authorization reconstructs the expected
  invocation and compares its actual arguments, not just a caller-set hash.
- `profile_cli.py`: read-only previews use either an unstarted workflow's
  real source/tests/skills or an explicitly labeled objective-only context.
  Providers remain fixed to the configured Claude Builder route.
- `trial_design.py`: read-only incomplete template and structural validator
  for preregistration, including blinded evaluation, all-assignment accounting,
  unknown outcomes, budgets, privacy/provider eligibility, owners, holdouts,
  economic hurdle, stopping rules and outcome maturation.
- Controller execution and dispatch launch acquisition explicitly reject
  profile packets until trial accounting/authorization exists. An offline
  dispatch fixture verifies identity retention without launching a worker.
  No profile provider-result or launch-intent evidence is claimed.
- Schema 9 adds immutable trial registrations, cancellation events, arm
  assignments, retry/outcome records, aggregate reservation accounting and
  explicit workflow schema-2 task tags/trial opt-in. The new CLI supports
  registration, assignment, status, cancellation and accounting-only outcome
  recording; every one reports provider launch as false.

## Verification

Focused cases cover source/catalog drift, profile and actual-argv tampering,
contradictory and overlapping rules, symlinks/oversize input, strict versioned
packet decoding, legacy compatibility, advisory permission escalation,
profile dispatch launch rejection, read-only CLI behavior, workflow context
preservation, incomplete/stale/duplicate-key trial-design rejection, schema-9
registration migration, idempotent assignments, reservation exhaustion,
cancellation fencing, retry/unknown accounting, stale identity rejection and
CLI dry-run accounting.

Full-suite result: **PASS**, 355 tests in 370.278 seconds on the installed
Python 3.14 interpreter. All 97 Python files compiled in memory; no bytecode
was written. This includes 25 M2 tests and the existing workflow, delivery,
publication, migration, dispatch, recovery and multiprocess stress suites.
`git diff --check` passed.

Verification command (normal repository checker plus a per-test watchdog):

```sh
DIAMOND_TEST_TMPDIR="/var/folders/7g/r534nhd54h9ff_xyfpy0t0240000gn/T/omnirush" \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests python3 -B -c \
'import faulthandler, unittest; from diamond.checking import main; start = unittest.TextTestResult.startTest; unittest.TextTestResult.startTest = lambda self, test: (faulthandler.dump_traceback_later(90, exit=True), start(self, test))[-1]; result = main(); faulthandler.cancel_dump_traceback_later(); raise SystemExit(result)'
```

Review corrections before this result: removed the preliminary caller-supplied
digest execution bypass; retained the full profile/prompt snapshot; pinned
source digests in the catalog; preserved schema-1 bytes; checked actual argv;
and distinguished structural trial validation from authorization. Early
prototype claims about profiled launch evidence are superseded by the explicit
preview-only controller and dispatch guards documented here.

## Remaining M2 gates

This is the Council-authorized offline slice, not a completed live pilot.
The two profiles have no measured advantage over fixed role instructions.

1. Operator/evaluation owner supplies and approves the real workload,
   evaluation procedure, budgets, ownership and stopping/maturation inputs.
   The template deliberately leaves these unknown rather than inventing them.
2. Connect approved assignments to a controller-owned dispatch path that binds
   registration, assignment, profile, prompt, provider, and result identity.
   The current assignment and outcome commands remain accounting-only.
3. Derive calls, elapsed time, cost, and unknown status from controller
   evidence, then preserve independent Integrator, QA, Reviewer, and Release
   stages around each accepted candidate.
4. Run the preregistered bounded live comparison. Inconclusive evidence keeps
   the incumbent fixed route.

Configuration schema remains 1 and database schema is now 9. Existing schema-8
ledgers require the explicit `migrate` command before trial tables are
available. Trial registration and accounting commands remain dry-run surfaces;
no provider launch is authorized by this slice.

## Remaining live-trial gates

The persisted layer prevents unregistered, over-budget, cancelled or stale
assignments from entering the accounting tables, but it does not yet grant
profiled dispatch authority. Before a live pilot:

1. The operator/evaluation owner must complete and approve the real workload,
   lineage split, holdouts, blinded acceptance, usefulness rule, evaluator
   version, privacy/provider eligibility, economic hurdle and stopping rule.
2. Trial registration must be created from that approved design, and workflow
   opt-in/assignment identity must be bound through packet, dispatch, provider
   result and independent Integrator/QA/Reviewer/Release evidence.
3. Live result accounting must derive calls, elapsed time, cost and unknown
   status from controller evidence rather than operator-entered outcome values.
4. Provider authentication, model identity, quota/eligibility and bounded
   live participation must be verified. Inconclusive evidence retains fixed
   routing.
