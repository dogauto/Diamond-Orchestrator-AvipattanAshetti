# Dynamic agents and provider routing: Council-advised plan

Date: 2026-10-12. Source baseline: `65dc97f`.
Status: **M0/M1 first implementation slice verified; M2–M6 remain planned.**
Decision: [Council verdict](../decisions/dynamic-agents-2026-10-12/VERDICT.md).
Evidence and uncertainty: [brief](../decisions/dynamic-agents-2026-10-12/BRIEF.md).
Protocol, execution limitations and votes: [tally](../decisions/dynamic-agents-2026-10-12/TALLY.md).

Implementation follow-up: [M0/M1 implementation and verification](../verification/COUNCIL_M0_M1.md).
The baseline findings and Council transcript below retain their original scope;
the follow-up identifies the repairs, commands, migration, and current checks.

## 1. What Diamond will do

Diamond will compose a bounded task-specific worker from an existing role, a reviewed instruction profile, relevant evidence/skills, explicit permissions and a selected eligible provider. Initially this is a deterministic compiler into the existing `TaskPacket`, not another agent runtime. A profile can specialize one worker without creating another worker.

The recommended progression is fixed-routing repairs -> a narrow inline-profile trial -> evaluated provider routing -> bounded optional role composition. Learning and native nested agents earn adoption through separate evaluations. Fixed routing remains a valid final solution if adaptation does not repay its cost. The Council vote approves this sequence as advice, not a claim that it already improves outcomes.

### Separate five decisions

| Decision | Meaning | Authority |
| --- | --- | --- |
| Role | Builder, Debugger, QA, etc.; what output is required | Existing controller role contracts |
| Instruction profile | Specialization within a role; e.g. Python parsing vs SQLite diagnosis | Reviewed, versioned profile catalog |
| Task overlay | Current source, dependency excerpts, relevant prior evidence and objective | Controller-built packet; model analysis remains labeled as a claim |
| Provider/model | Which CLI/runtime and requested/reported model perform the role | Eligible route frozen in controller authorization |
| Team/sequence | Whether extra research, architecture or parallel workers are useful | Bounded plan with dependencies and budgets; mandatory delivery gates preserved |

Changing profile, provider and team simultaneously prevents attribution. Experiment with one first, then explicit interactions. Different CLIs may serve the same model; UNKNOWN model identity stays unknown.

### Proposed execution flow

```text
Source-scoped, verified evidence
  -> candidate + applicability + uncertainty
  -> research/plan where needed (structured advisory output)
  -> trusted baseline and acceptance criteria
  -> profile compiler + eligibility/router
  -> frozen packet and persistent dispatch claim
  -> existing Builder / reproduced-failure Debugger workflow
  -> isolated delivery target: Integrator -> QA -> Reviewer -> Release
  -> candidate-bound promotion with durable publication journal
  -> observed outcomes, regressions, failed experiments and versioned lessons
```

Council sits at consequential design/policy decisions or unresolved disagreement, not on every routine task. Its verdict cannot authorize source publication, change trusted tests, or count as executable evidence.

## 2. Benefits to test

| Expected benefit (hypothesis) | Mechanism | Evidence needed |
| --- | --- | --- |
| More appropriate specialization | Relevant bounded instructions/skills instead of generic role text | Matched profile-only trials on independently accepted task outcomes |
| Less avoidable downtime | Eligibility excludes demonstrably unavailable/unsupported routes | Outage fixtures and real blocked-time measurements; include abstentions/waiting |
| Fewer repeated failed corrections | Retrieve version-scoped negative evidence and sufficient failure excerpts | Reduced repeated failure on held-out task lineages, without weakening tests |
| Lower total execution cost | Smallest sufficient worker set, shared budgets, no unnecessary architecture stage | Total calls/time/review/maintenance cost per useful accepted change |
| Reproducible decisions | Route/profile/effective prompt hashes and rejected-alternative reasons | Replays, tamper tests, packet-to-invocation-to-result trace |
| Better recovery | Reconcile uncertain attempts before retries; journal publication | Crash, late-result, cancellation and human-edit preservation tests |
| Explainable operations | One report explains why a worker/route was chosen or rejected | CLI/MCP/macOS inspection against the same ledger revision |

No measured percentage gain, provider leaderboard, or calibrated routing confidence is available yet.

## 3. Existing foundation and immediate corrections

These findings must inform implementation; presence of a module is not proof it is operational.

| Existing location | Finding | Required correction |
| --- | --- | --- |
| `cli.py:334-339`, `self_improve.py` | VERIFIED shadow is published without independent delivery | Separate verification, review acceptance, and promotion |
| `cli.py:314-329` | Explicit rollback unconditionally copies old bytes | Compare against installed candidate; preserve newer edits and quarantine conflicts |
| `self_improve.py:61-105` | Publisher is not bound to a retained verified candidate manifest | Revalidate exact reviewed bytes and base dependencies immediately before promotion |
| `daily_improve.py:248-268` | Unbounded query, no digest validation/project lineage | Bounded indexed snapshot reads, retained scope and evidence validation |
| `daily_improve.py:60-68,200-224` | Dedup drops citations; multiple attempts treated as confidence | Separate all citations from incident counts and independent task-lineage counts; label heuristic support |
| `daily_improve.py:173-188` | Reviewer findings treated as critical opportunities without adjudication | Preserve claim versus verified defect; account for resolutions, later outcomes and source relevance |
| `routing.py:54-68,113` | Ignores supplied role config; references undefined config | Explicit validated inputs; configured primaries preserved; pure selection returns a decision record |
| `provider_health.py`, `schema.py:139-151` | Health UPDATE/upsert conflicts with unconditional SQL triggers | Append-only observations plus controller-managed rebuildable projection through explicit migration |
| `provider_health.py` | Persistent monotonic times, implicit writes on reads, missing health treated as healthy | Persist UTC/expiry/source; monotonic only within process; read-only queries; unknown/stale explicit |
| `routing.py:128-132` | Cooldown excludes a recently successful provider | Separate provider backoff, capacity reservation and switch hysteresis |
| `diagnostics.py`, `docs/RATE_LIMIT_ROUTING_PLAN.md` | Quota command/shape claims lack acceptance evidence | Per-version verified capability probes; unavailable quota/auth = UNKNOWN |
| `autonomy.py` | Manifests for dependent steps frozen before predecessors run | Bind successors to accepted predecessor artifacts; never silently refresh stale work |
| `autonomy.py:107-129,218` | Planner calls `supervisor.run` directly before workflow creation/idempotency | Persist planning intent, claim and shared budget before provider execution; deduplicate before launch |
| `delivery_cli.py:60-61`, `delivery.py:182-183`, `live.py:104-105` | Static Gemini veto remains outside the router | Replace only with fresh verified eligibility gates; pin explicit smoke-provider requests |
| `workflow.py:92-125` | Debugger gets very short error excerpts | Budget meaningful assertion/trace context with exact evidence references |
| `self_improve.py:9-35` | New control-plane modules are not all on the protected list | Protect routing, health, evaluator, profile/compiler and policy sources explicitly |

Evidence: brief source citations plus the Feynman/Torvalds round-1 probe reports. Full current suite and live acceptance are not claimed; earlier full suite timed out at a subprocess stress case. Address baseline blockers before using test outcomes as an acceptance gate.

## 4. Where to implement

All paths below are implementation targets; new paths are proposed, not current capabilities.

| Boundary | Existing files to reuse | Small additions when needed |
| --- | --- | --- |
| Candidate discovery | `daily_improve.py`, `evidence.py`, `store.py` | `improvement_store.py` for parent experiment records and outcome events |
| Situation and profile specialization | `domain.py`, `skills.py`, `skill_catalog.py`, `packets.py` | `agent_profiles.py`; `config/agents/catalog.toml` plus reviewed Markdown profiles |
| Eligibility and route decision | **Existing** `routing.py`, `provider_health.py`, `diagnostics.py`, `config.py` | Typed capability/decision records inside these boundaries; no competing router |
| Instruction compilation | `workflow.prompt`, `delivery.prompt`, `TaskPacket` | Small `agent_compiler.py` only if shared construction warrants extraction |
| Execution/authorization | `adapters.py`, `packets.py`, `controller.py`, `dispatch.py`, `supervisor.py`, `occupancy.py` | Profile/route binding, effective-invocation evidence, shared budget reservations |
| Parent improvement orchestration | `autonomy.py`, workflow/delivery stores and CLI handlers | `improvement.py` delegates stages; references child IDs instead of duplicating child state machines |
| Promotion/recovery | `self_improve.py`, `workspace.py`, workflow/delivery evidence | Publication intents/receipts and reconciliation in the authoritative ledger |
| User operations | `cli.py`, `shell_commands.py`, `mcp_server.py`, `dashboard.py`, `examples/DiamondMac/.../main.swift` | Thin inspection/command adapters over controller, after CLI contract stabilizes |
| Council | Existing installed skill; `docs/architecture/ROLES_AND_SKILLS.md` | Project-local decision artifacts and later read-only decision-report import |

### Extend contracts only for missing invariants

- Profile: immutable ID/version/content hash, role applicability, specialization text/skill references, provenance/license, evaluation status. Declared capability requests are not grants.
- Selection decision: project/task lineage, situation tags, candidate/base/config/policy hashes, eligible alternatives and rejection reasons, health/capability evidence IDs and expiry, selected route, requested model or UNKNOWN, aggregate budget, independence requirements.
- Packet binding: profile hash, effective prompt/context hash and routing decision hash, validated through `TaskPacket.authorize_invocation`, dispatch claim, controller launch intent, and result evidence. Putting them on `Invocation` alone is insufficient.
- Result identity: actual CLI version, reported model/session or UNKNOWN, actual output, test evidence, review/candidate digests and profile/route identity. A provider claim cannot authenticate its own evidence.
- Parent experiment: input candidate lineage, baseline/candidate manifests, trusted test/benchmark version, child workflow/delivery IDs, all assigned runs, outcomes/unknowns/costs, publication receipt and delayed observation events.

Reuse existing canonical JSON and validation style. Do not add four competing copies of task permissions or a second scheduler. Retain bytes or content-addressed artifacts needed for future inspection, not just hashes of unavailable prompts.

## 5. Milestones and dependencies

### M0. Establish trustworthy acceptance and recovery

**Owner:** controller maintainer. **Depends on:** current source baseline only.

1. Add meaningful failing regressions for review-bypass promotion, candidate tampering, rollback after a human edit, cancellation followed by late results, and restart after partial multi-file publication.
2. Extract self-improvement operations from the large CLI handler into a controller service. `run` produces an isolated verified candidate; independent delivery acceptance and promotion are distinct controller operations. Document the behavioral change; do not silently preserve an unsafe legacy auto-publish mode.
3. Journal publication intent before replacements, including base/candidate paths, hashes and modes. Persist completion/rollback receipts, fsync as appropriate, and reconcile partial operations before a new writer. SQLite plus files is not globally atomic: model the uncertain state explicitly.
4. On rollback, restore only bytes known to belong to the installed candidate; never overwrite intervening human edits. Verify full relevant dependency/config/test identity before promotion, not just owned-file equality.
5. Run candidate verification in a fresh interpreter pointed at the candidate sources. Do not let the controller that is evaluating its replacement reload changed code during the active run.
6. Resolve the existing full-suite timeout with a bounded reproduction; record whether it is environmental or a code defect. Do not use an old passing count as current acceptance.

**Pass:** a VERIFIED candidate without independent delivery evidence cannot touch live files; crash/cancel/late-result tests preserve ownership, revisions and human edits; no duplicate unresolved external execution. A fresh current baseline check is recorded.

### M1. Make candidate evidence fit for decisions

**Owner:** evidence/evaluation maintainer. **Depends on:** can start offline beside M0; required before a live trial.

- Validate evidence payload digest/schema and source linkage using existing decoders. Use one bounded snapshot with explicit cutoff, watermark, row/byte limits, indexes and truncation reporting; verify query plans on a realistic ledger.
- Retain project/workspace/base identity, task/workflow lineage, provider/model/CLI/profile version and test identity. Temporary examples, fixtures and unrelated client projects remain labeled and excluded from routing evaluation by default. Ambiguous origin yields UNKNOWN applicability.
- Count independent tasks separately from attempts/reproductions. Preserve every supporting citation when coalescing an incident. Normalize incidental durations without merging distinct semantic failures or unrelated paths.
- Distinguish real controller test results from provider diagnoses/reviewer claims. Trace later corrections, resolutions and stale source versions. A manifest hash differing from a file hash is not itself a defect.
- Replace uncalibrated high/medium confidence wording with explicit heuristic evidence support and limitations. Persist candidate dispositions, failed experiments, resolution events and cooldown/dedup keys.
- Output no-change/insufficient-evidence when appropriate. Provider auth/quota issues should produce operational investigations, not assumed source patches.

**Pass:** duplicated evidence increases citations, not independent sample size; corrupted digests fail; unrelated workspace failures cannot target this repo; resolved findings remain historical; read-only analysis does not initialize state or launch providers.

### M2. Narrow dynamic-profile pilot (first agent-composition feature)

**Owner:** controller/profile maintainer. **Depends on:** M0/M1 and the trial-design gate below for execution; offline preview can precede them.

**Trial-design gate, required before the first live M2 call:** evaluation owner freezes the hypothesis, task-lineage split, paired/randomized assignment scheme, blinded acceptance procedure, useful-outcome criteria, evaluator version, all-assignment cost/unknown accounting, total pilot budget, stopping rules and proposed economic hurdle. Blinded acceptance means evaluators do not see the tested profile/route label or author persuasive rationale. This small gate precedes data collection; M6's learned-policy infrastructure is not a prerequisite. Sparse samples produce diagnostic or inconclusive results, not superiority claims.

- Two reviewed specializations within one existing role and fixed provider. Example pilot: a Claude Builder receiving either Python parser-focused or SQLite transaction-focused guidance, both under the same Builder replacement-file contract. Exact workload choice depends on representative tasks.
- Select deterministically using explicit validated task tags. No match yields documented legacy profile; contradictory tags fail. Do not auto-create another worker for each specialization.
- Compile profile + applicable pinned skills + bounded source/evidence context into the existing task packet. Effective permissions are the intersection of accepted task policy and provider-enforceable capabilities. Profiles cannot grant tools, redefine trusted tests, broaden source ownership or change output contracts.
- Preview profile choice, reasons, effective prompt digest and budget. Bind the same profile/config/route identity through authorization, dispatch and evidence before enabling execution.
- Native `.github/agents` or Claude subagent files are not required for this pilot. A provider-neutral inline prompt already matches the current response-only workers.

**Pass:** deterministic compilation, conflicting tags rejected, no-match legacy compatibility, altered profile rejected after acceptance, text requests cannot grant tools, mandatory delivery stages retained. Profile performance is measured only after these structural tests pass.

### M3. Repair and integrate capability-aware provider routing

**Owner:** routing/adapter maintainer. **Depends on:** M0/M1; distinct experimental axis from M2.

- Refactor existing `routing.py` into pure selection over validated config/capabilities/fresh health snapshots. Honor current role assignments as cold-start defaults; exclude disabled providers and incompatible output/tool/session contracts before ranking.
- Repair `provider_health.py` and SQL trigger/write mismatch with migration, immutable observation history and rebuildable projections. Read paths stay read-only; corruption/unknown/stale health remains visible. Use UTC deadlines with source/TTL, monotonic elapsed time only in-process, and reboot/clock-adjustment tests.
- Probe binary availability, authentication, quota and capability separately. `doctor` stays non-model-executing and invokes provider `--version` probes internally. Do not implement quota probes from unverified CLI names. UNKNOWN quota is neither zero nor unlimited; policy decides whether a bounded attempt is allowed under a known budget.
- Replace the static Gemini vetoes in `delivery_cli.py`, `delivery.py` and `live.py` with the same verified eligibility contract, only when evidence permits. Explicit `smoke --provider` remains pinned to the requested provider and reports unavailable/blocked instead of substituting another provider and mislabeling the result. Test that fresh eligible evidence reaches the requested delivery route, and stale/unknown eligibility remains explicit.
- Separate backoff after rate limits, concurrency reservation and switch hysteresis. Include account/model scope where the provider exposes it. Reserve shared capacity/budget transactionally; profile factories cannot bypass `max_workers`.
- Select before authorization. A planned route change creates a superseding accepted revision/claim; preserve original route history. After external launch, reconcile before fallback, create a linked fresh attempt, and consume remaining parent budget. Never silently switch provider inside an already authorized attempt.
- Preserve fresh QA/Reviewer/Release sessions and author lineage. Isolate their context from author persuasion; use evidence packets, not a universal shared author conversation. Requested/observed model-family distinction matters where diversity is required.

**Pass:** unavailable/disabled/stale/incapable candidates excluded predictably; unknowns yield explicit decisions; two contenders cannot overspend capacity; fallback races/cancellation/late results cannot resurrect revoked authority. Legacy fixed-route workflows replay identically.

### M4. Connect the complete improvement lifecycle

**Owner:** workflow/controller maintainer. **Depends on:** M0–M3.

- Persist a parent improvement record linked to a scoped candidate and all child workflows/deliveries. Distinguish discovered, inapplicable/no-action, planned, baselined, executing, review-ready, accepted, publishing, published, observing, rejected, blocked and rolled-back outcomes. Reuse child states rather than mirroring every child transition.
- Researcher/Planner outputs are bounded proposals. Validate source applicability and acceptance criteria; suggested tests/commands must resolve to controller-approved commands. New regression tests require an independent test-definition step before freezing the evaluation inputs. The implementer cannot rewrite its own evaluator.
- Replace `autonomy.plan_goal`'s direct `supervisor.run` call with a persisted planning attempt through the controller/dispatch path. Resolve command deduplication before a provider call, reserve aggregate budget/capacity, record prompt/profile/route identity and failure/fallback provenance, and reconcile interrupted planning before relaunch. New Researcher/Architect stages use this same accounted path. A deterministic splitter fallback must be visible in evidence, not mistaken for an agent-produced plan.
- Run Architect only for a demonstrated multi-component design need. Bind dependent steps to accepted upstream artifacts. A fresh manifest is new authorization, never an automatic overwrite of the original one.
- Benchmark baseline and candidate on the same frozen tests and environment. Allow reproduced-bug fixes and separately defined efficiency experiments; unchanged passing source is not measured improvement. Flaky or infra-broken baselines yield INCONCLUSIVE rather than reward.
- Delegate verified components into the existing four-stage delivery pipeline. Use isolated target snapshots and the same authoritative ledger references; promote only exact accepted bytes using M0.
- Add a finite one-shot daily run with UTC run key, source/config/policy watermarks, idempotency, stop/resume/reconcile, aggregate call/time/cost limits, and a no-change outcome. Scheduling is a separate operator integration after reliable one-shot behavior; no watcher/launch agent/daemon is introduced by this plan.

**Pass:** complete fake-provider lifecycle from evidence to published receipt and observed outcome; duplicate plan commands and crash/restart cannot double-launch Planner or reset its budget; deterministic planning fallback is labeled; fresh-process replay and backup agree; source/policy/test/route changes invalidate old authorization.

### M5. Native provider profiles, only where beneficial

**Owner:** provider adapter maintainer. **Depends on:** packet/profile identity binding in M2/M3; separate live acceptance.

- Copilot: verify installed-version `--agent` semantics, file discovery and effective tool/custom-instruction/MCP configuration. Generated exports such as `.github/agents/diamond-qa.agent.md` derive from the catalog; they are not a second authority. Detect same-ID user profile overrides; use isolated discovery or decline native mode if effective identity cannot be established.
- Claude: verify its actual installed profile/subagent interface, context/tool inheritance and response-only output behavior before adding flags. Continue inline profiles until tests prove native mode preserves Diamond's contract. No promise that GitHub's agent format executes unchanged in Claude.
- Codex/Antigravity/Gemini/Omnirush: retain existing packets; enable a native feature only after its own capability/permission/output/session acceptance. Do not present `AGENTS.md` as a spawn API.
- Distinguish top-level custom agent from nested children. Native nested mode requires observable child lineage, accountable shared budgets, enforceable permissions/cancellation and retained outputs. Otherwise use controller-scheduled workers.
- Verify native/inline equivalence at the acceptance-contract level; include costs for hidden calls. Explicit fallback must remain in the ledger, not silently relabel native execution as inline.

**Pass:** profile-shadowing/ambient-instruction/MCP injection fixtures, profile-drift invalidation, correct session/model evidence, unchanged trusted files, and truthful blocked/unsupported behavior. Native mode must demonstrate a practical advantage to justify maintenance.

### M6. Earn adaptive routing and team selection

**Owner:** independent evaluation owner with operator budget owner. **Depends on:** attributable M4 outcomes.

- Extend M2's preregistered trial-design gate for each new intervention: task strata, randomization/selection probabilities, blinded acceptance, useful outcome definition, exact evaluator version, economic hurdle, power/sample analysis, total budget and stopping rule are frozen before data collection. Count every assigned task, cost and unresolved outcome; duplicates/retries are not extra task samples.
- Use isolated paired snapshots where appropriate; partition holdouts by task/project lineage and time. Separate profile, provider/model and team effects, including interaction tests. Synthetic outage or adversarial fixtures establish mechanism, not representative population gain.
- Compare accepted useful changes per total cost and elapsed-time distribution while preserving quality/recovery requirements. Include generation, corrections, independent review, failed/unknown runs, quota use, maintenance and delayed regressions. Unknown pricing/usage stays unknown; fall back to enforceable call/time limits rather than inventing dollar costs.
- Promote only when a preregistered uncertainty bound exceeds the economically justified worthwhile effect. Inconclusive trials preserve the incumbent; a bound below the worthwhile hurdle rejects that intervention for the tested workload. No fixed 10%, 20% or 30-task requirement is asserted here.
- Retain negative memory scoped by task/profile/provider/model/CLI/evaluator version, with append-only resolutions and explicit requalification after drift. Only final independently assessed outcomes qualify for reward.
- At first, learned policy produces shadow recommendations. Enable a bounded canary only after offline acceptance; freeze policy snapshots per attempt, keep a last-known-good rollback, and prevent oscillation. Generated workers cannot modify the router/evaluator. Free-form profile proposals may compete in sandbox trials, but remain frozen and controller-authorized before execution.

**Pass:** baseline superiority claims survive lineage/unknown-outcome checks; invalid patches that pass weak tests do not earn rewards; drift invalidates unjustified statistics; policy rollback and stop work. If economics favor A-only, stop expanding B.

## 6. Compatibility and data migration

- Configuration schema 1 currently rejects extras. Choose an explicit schema-2 opt-in with a versioned loader/migration command; preserve exact schema-1 fixed-route interpretation. Do not silently reinterpret the current TOML or override Antigravity planner/researcher assignments with routing defaults.
- SQLite is schema 7 at this baseline. Allocate the next migration after checking current HEAD again; do not assume a fixed future number. Reconcile health projection triggers, add required indexes and new immutable records transactionally. Test schema 7 and all existing supported upgrade chains on fixture copies, backup/replay equality and injected migration failure. No live ledger migration occurs during this planning task.
- Stored workflow/delivery/packet JSON uses exact field checks. Support old versions via explicit decoders; additive fields are not automatically backward compatible. Preserve legacy canonical bytes/digests for active and historical attempts; prohibit rewriting accepted snapshots.
- API/CLI envelopes stay schema-versioned; shell, MCP, dashboard and macOS use the same controller. New statuses/commands must have unknown-version behavior. UI receives explanations and IDs, never duplicate authorization logic.
- State for improvements must be durable and project-keyed outside editable shadows. Provide explicit discovery/import for old temporary checkpoints; never copy mixed global temporary state into a project's learning set without provenance.
- Update the protected-path policy for new control-plane modules and keep regression tests, criteria, profiles/catalog and route policy outside generated worker ownership. Ordinary maintainer-reviewed changes can evolve these components.

## 7. Relationship to the existing rate-limit plan

`docs/RATE_LIMIT_ROUTING_PLAN.md` remains a historical proposal. This plan should govern these contested implementation points:

| Older proposal | Council-advised resolution |
| --- | --- |
| Reselect inside `run_step`, retry automatically on quota error | Select/freeze before dispatch; reconcile launched attempt before a newly authorized fallback |
| One shared conversation across all providers | Share grounded handoff artifacts; fresh reviewers must not inherit author reasoning |
| Persist monotonic reset/quarantine timestamps | UTC with provenance/expiry across processes; monotonic for local elapsed time |
| Quota available for all six providers | Report support truthfully; UNKNOWN is a supported state |
| `claude --usage`, `codex status` assumed | Verify actual installed CLI contract before use |
| Route a Controller/Confirmation model role | Controller remains deterministic; existing role enum is not extended by prose |
| A green old test count establishes success | Require current tests, relevant fault cases and explicit live evidence per supported route |

## 8. Coverage and acceptance matrix

Owners below are responsibilities, not newly launched agents. Implementation may use current Claude Builder/Codex Debugger routes; independent review must remain separate. Dynamic selection is the feature being built, not an assumed capability of this task.

| Angle | Required evidence / decision | Owner | Milestone |
| --- | --- | --- | --- |
| User value and A-only option | Useful outcomes and total-cost break-even, no promised gains | Operator/evaluation | M1,M6 |
| Source/workspace relevance | Project/base/task lineage and unknown-origin exclusion | Evidence | M1 |
| Claim validity | Digests checked; reviewer hypotheses adjudicated; later resolutions linked | Evidence/evaluation | M1 |
| Agent/profile/role identity | Profile compiles within existing role contract; effective prompt bound | Controller | M2 |
| Model versus provider | Requested/observed identity, capability per CLI version, UNKNOWN maintained | Adapter | M3,M5 |
| Context quality/privacy | Relevant dependency excerpts, redacted evidence, no secrets in profiles/logs; project locality controls | Controller/evidence | M1,M2 |
| Prompt/tool/MCP injection | Profile cannot grant rights; ambient profile precedence verified | Adapter | M2,M5 |
| Review independence | Session lineage, clean context, author-persuasion fixture; family diversity not invented | Delivery/evaluation | M0,M3 |
| Fallback/quotas | Fresh observations, true UNKNOWN, bounded reauthorization | Routing | M3 |
| Cost and capacity | Shared transactional reservations including retries and descendants | Controller/operator | M3,M4 |
| Clock/restart | UTC persistence, clock-skew and reboot tests | Storage | M0,M3 |
| Concurrent writers | CAS/claims, supersession and late-result races | Controller | M0,M3 |
| Publication/rollback | Reviewed digest, durable journal, human-edit preservation | Controller | M0 |
| Dependencies | Accepted predecessor manifest, failed-parent blocks descendants | Workflow | M4 |
| Test independence | Criteria frozen before implementation; no evaluator rewrites | Evaluation | M0,M4 |
| Sparse data/bias | Lineage splits, all-task denominator, bounds on unresolved outcomes | Evaluation | M6 |
| Negative memory/drift | Version-scoped outcomes, requalification trigger, no permanent unjustified exclusion | Evaluation | M6 |
| Migrations/old clients | Exact JSON/schema loaders, backup/replay, no silent route changes | Storage/interface | M2–M4 |
| Native agents | Effective profile resolution, child accounting, explicit unsupported result | Adapter | M5 |
| Daily operations | One-shot run dedup, report/status/stop/resume, no new daemon | Controller/interface | M4 |
| Interfaces/UX | Why selected/rejected, costs, provenance, waiting/blocked/unknown states | Interface | M4 |
| Council/provenance | Pinned source/license, full rounds/dissent/tally, advisory boundary | Maintainer | Current decision |
| Maintenance/abandonment | Named upkeep responsibility, total pilot budget and kill criteria | Operator | M2,M6 |

No analysis can guarantee all unknown unknowns are covered. This matrix makes the covered boundaries and remaining evidence obligations explicit.

## 9. Proposed operator experience

Current implemented command: `daily-improve analyze`.

Existing `workflow show`, `workflow status`, `workflow events`, `delivery show`, and `delivery status` commands remain available. Proposed later surfaces (not runnable commands yet): candidate show/disposition; profile preview; route explain; parent improvement plan/baseline/run-one-step; parent publish/reconcile; outcome report. Extend existing delivery inspection with linked improvement/profile/route IDs. Each surface returns stable IDs, revisions, selected/rejected routes and reasons, evidence links, budget/unknown fields, and actual execution status. Read-only preview/inspection never triggers a model call or initializes health state.

The first human-facing pilot should show: selected role, specialization, provider, source scope, test/evaluator identity, why the choice was made, what is unknown, and whether source promotion is authorized. A profile preview should make it obvious that a composed role is still one worker.

## 10. Rollout decision and open inputs

Immediate implementation priority after instruction: **M0's publication/recovery regression and repair slice**, with M1 evidence fixes alongside it. Offline M2 profile preview may proceed without live model execution. This does not require all M6 statistical infrastructure to be built first.

Before live trials, operator/evaluation owner must complete M2's trial-design gate, including blinded evaluation and frozen assignment/holdout/stopping rules, and set workload, budget, privacy/provider eligibility, usefulness criteria, maintenance ownership, economic hurdle and outcome-maturation window. These are configuration inputs, not numbers the agent should invent. Code/model freshness and actual native profile capability remain evidence requirements.

Within 28 days of the first pilot, apply the Council stop rules. Any permission/evaluator mutation, unauthorized publication, lost human edit or budget breach immediately halts expansion; identity/attribution gaps block affected routes. Insufficient evidence preserves fixed routing without declaring adaptation ineffective. Do not extend a pilot simply to obtain significance.
