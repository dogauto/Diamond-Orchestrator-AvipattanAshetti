# Council review: M2 readiness

Date: 2026-10-12  
Source commit: `40c6b42`  
Review scope: whether Diamond should authorize profiled live dispatch after the
offline M2 profile and trial infrastructure slice.

## Council Verdict

Keep real profiled live dispatch blocked. Authorize only a controller-owned
fake-provider vertical slice that tests evidence ownership without relaxing the
existing schema-2 controller and dispatch guards.

## Composition and method

Panel: Sutskever, Aurelius, Socrates.  
Mode: focused three-member Council with independent analysis, anonymized
cross-examination, and final crystallization.  
Domain-weight seat: Sutskever, 1.5x, because the decision concerns whether
live AI execution has enough safety and attribution evidence.  
Chairman: independent synthesis agent, separate from the panel.

Dedicated council provider detection was unavailable in this installation, and
the dedicated council agent files were absent. The panel used the available
general reasoning agents with the selected Council roles supplied explicitly.

## Evidence considered

- `40c6b42` adds two digest-pinned Claude Builder profiles, strict profile and
  prompt binding, preview, schema-9 trial registration, assignment budgets,
  cancellation fences, accounting-only outcomes, 355 passing tests, and 97
  compiled Python files.
- The controller and dispatch paths explicitly reject schema-2 profile
  execution. Profile assignment and outcome commands report no provider launch.
- `TrialStore.record_outcome` accepts status, calls, elapsed time, cost, and
  unknown reason as command inputs. It preserves an assertion but does not yet
  derive those facts from controller-owned launch and result evidence.
- The profiles remain unevaluated. The catalog claims contract review by a
  coding agent and explicitly makes no human approval claim.
- The repository does not yet contain an approved real workload, lineage split,
  holdout procedure, blinded acceptance process, usefulness rule, evaluator
  version, provider eligibility decision, economic hurdle, stopping rule, or
  controller-owned live result path.

## Competing options

### Option A: `blocked-fake-slice`

Keep real dispatch blocked and build a controller-owned fake-provider vertical
slice. The slice must create the assignment, packet, attempt, synthetic launch
evidence, synthetic result evidence, and derived `TrialOutcome` through the
normal controller path. It must preserve cancellation, retry, budget, identity,
and unknown-result behavior.

### Option B: `blocked-design-first`

Keep real dispatch blocked and require the evaluation owner to complete and
approve the preregistered workload and trial design before implementing the
evidence vertical slice.

No panel member supported authorizing real profiled provider execution now.

## Cross-examination and objections

The panel agreed that offline fencing proves restraint, not live usefulness or
provider readiness. Sutskever and Aurelius objected that design approval alone
does not repair the missing observation mechanism. Socrates agreed that
operator-entered accounting is not evidence, but preferred design approval
first so the synthetic slice has a fixed evaluation target.

The minority objection is valid as a sequencing concern. The synthetic slice
must not become an unbounded engineering exercise. Its test contract should be
derived from the trial-design fields already required by `trial_design.py`, even
if the real workload remains operator-owned and unapproved.

## Weighted tally

- `blocked-fake-slice`: **2.5**
  - Sutskever: 1.5x domain seat, high confidence
  - Aurelius: 1.0, high confidence
- `blocked-design-first`: **1.0**
  - Socrates: 1.0, high confidence
- Total base weight: **3.5**
- Consensus threshold: **2.333**
- Result: `blocked-fake-slice` clears the threshold.

## Concrete next action

Build and test a controller-owned fake-provider vertical slice that derives
`TrialOutcome` only from synthetic controller evidence while preserving the
live-dispatch guard.

## Kill criteria

Stop the slice and authorize no live dispatch if:

- a real provider launch occurs while the schema-2 guard remains active;
- a `TrialOutcome` is accepted without matching assignment, packet, launch, and
  result identity;
- usage, cost, elapsed time, or status comes from operator assertions instead
  of synthetic controller evidence;
- evidence identity is missing, inconsistent, duplicated, or silently
  reconciled; or
- the established 355-test baseline falls, or Python compilation or the native
  macOS build fails.

## Unresolved questions

The Council does not know whether the controller can correlate assignment,
packet, launch, provider result, and derived outcome identity end to end. It
does not know whether a profile improves accepted outcomes, whether the
operator will approve a representative workload, or whether provider
authentication, model identity, quota, privacy, and bounded eligibility will be
available for a real trial.

## Recommended sequence after this decision

1. Build the fake-provider provenance slice and its regression tests.
2. Require an operator-approved trial design and real workload.
3. Bind controller-derived usage and result evidence to the registered trial.
4. Preserve independent Integrator, QA, Reviewer, and Release evidence.
5. Reconsider real dispatch only after every gate passes. Inconclusive evidence
   retains the fixed route.

## Follow-up

Review the slice artifact, test results, evidence lineage, and guard behavior.
The decision should be revisited only when those records provide new evidence
about the unresolved questions above.

## Session metadata

```yaml
schema_version: 1
mode: triad
panel_size: 3
rounds_run: 3
enforcement_calls: 0
enforcement_breakdown: none
chairman_failed_fallback: no
tools_used: yes
provider_count: unknown
fallbacks_triggered: none
```
