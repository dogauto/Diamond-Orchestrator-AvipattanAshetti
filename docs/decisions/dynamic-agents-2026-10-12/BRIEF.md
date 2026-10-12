# Decision brief: Diamond dynamic agents and provider routing

Date: 2026-10-12. Inspected project revision: `65dc97f`.
Requested deliverable: Council advice and a project-specific implementation plan, including benefits, tradeoffs, failure modes, and acceptance criteria. Runtime implementation is a subsequent task.

## Decision

Where and how should Diamond compose situation-specific agents, choose providers (including Claude, Codex, Copilot, Antigravity, Gemini, and Omnirush), and connect that capability to daily improvement? Compare (A) retain fixed routes and improve evidence, (B) constrained profile composition and deterministic eligibility/routing with evaluated adaptation, and (C) free-form agent/team generation and online routing learning. Do not assume the largest architecture or a preferred option wins. A complete review means explicit coverage and unknowns, not a guarantee that every possible angle is known.

## Goal and constraints

Goal: more independently accepted useful changes per unit of execution cost/time, with attributable results and recoverable execution. Benefits are hypotheses until measured against fixed routing.

Keep the existing local Python 3.11+/SQLite controller, six provider adapters, native macOS/web/CLI/MCP interfaces, explicit role contracts, finite budgets, trusted tests, and append-only evidence. User wants agents selected/composed for the situation. Council is an advisory decision protocol, not the executor or a release certificate. Do not install an OS watcher/daemon, launch live improvement jobs, commit, or publish during this decision task.

## Source-grounded facts (FACT unless noted)

1. `config/diamond.toml:35-44`: Planner/Researcher=Antigravity; Architect/Builder/Integrator=Claude; Debugger/Reviewer=Codex; QA/Release=Copilot. All six providers enabled. `config.py:50-90` requires exactly nine roles and rejects unknown config fields. Proposed `[routing]` or `[agent_profiles]` fields cannot simply be added to schema 1.
2. `daily_improve.py`: read-only heuristic analysis exists. It clusters failed test/provider/delivery evidence and emits scores. It does not research, persist experiment outcomes, launch agents, compare baseline/candidate, or schedule daily runs. Its query does not select/verify the evidence digest, uses unbounded `fetchall`, does not partition by workspace/project/task lineage/provider version, ignores later success/resolution, and deduplication discards supporting evidence references. `confidence=high` from two attempts/two candidate digests is an uncalibrated recurrence heuristic, not proof of diagnosis, usefulness, or provider superiority.
3. Earlier observed ledger sample: 45 provider attempts, 28 workflow verifications, 2 debugger diagnoses; mixed temporary/example workspaces. Three math-test attempts belong to one failed workflow, not three independent representative tasks. One QA finding compared a manifest digest with a file digest; those legitimately differ. A stored reviewer claim is not a verified defect. No representative routing dataset or provider success percentages have been established.
4. `autonomy.py:101-129` asks the configured Planner for 1..8 objectives with an offline splitter fallback. `plan_and_enqueue` freezes multiple workflow manifests upfront and gives them the same owned paths/tests. `run_queue` advances ordinary workflows, not the whole self-improvement/delivery/promotion cycle. Researcher/Architect have role definitions but no dedicated orchestrated stages here.
5. `workflow_cli.py` freezes Builder/Debugger providers in `WorkflowSpec`. `workflow.py` checks accepted config and workspace, runs a response-only Builder/Debugger, reproduces the exact failure for debugging, applies source through the controller, and records real tests. Prompt currently provides owned source/test names; debugger excerpts are capped at about 128 serialized characters per stream. Structured replacement-file output and budgets must survive profile changes.
6. `delivery_contracts.py` fixes four serial stages: Integrator, QA, Reviewer, Release. `delivery.py`/`delivery_gates.py` bind candidate/test digests and reject reused author/stage sessions. Fresh sessions are enforced; different CLI labels are not proof of model-family independence. Optional role-team planning cannot silently skip these four delivery gates.
7. `cli.py:334-339` immediately copies a VERIFIED self-improvement shadow into the live project. It does not call independent delivery gates first. `self_improve.py:61-105` checks live owned files against checkpoints but does not bind shadow publication to a retained verified manifest. Explicit rollback at `cli.py:314-329` unconditionally copies checkpoint files and does not preserve intervening edits. File publication and SQLite are not crash-atomic. Self-improvement defaults to shared temporary state.
8. Latest commit added `routing.py`, `provider_health.py`, and schema 7. They are not imported into the workflow/delivery execution paths (search found routing usage only within routing.py). `routing.select_provider:113` references undefined `config`. `load_routing_policy` ignores its config argument and hardcodes primaries (including Claude planner, contradicting configured Antigravity). Capability fit, output/session requirements, and routing-decision evidence are absent.
9. `provider_health.py` writes through `EventStore`, but schema 7 unconditionally forbids `UPDATE`/`DELETE` on `provider_health` while store methods issue UPDATE/upsert. Persistent timestamps use `time.monotonic`, unsuitable across reboot; default unknown health is treated as healthy; reads call write-oriented `_ensure_table`; malformed quota is silently dropped. Selection applies a 300-second cooldown after success rather than tracking route-switch hysteresis. These are inspection findings, not yet newly executed regression tests.
10. `diagnostics.py` version probes cannot establish authentication; `RateLimitInfo` exists but end-to-end quota reporting is not established. `docs/RATE_LIMIT_ROUTING_PLAN.md` suggests automatic fallback within a run, global transcript reuse, assumed `claude --usage`/`codex status` commands, and monotonic persisted deadlines. These proposals need reconciliation with frozen authorization, uncertain external outcomes, fresh-review sessions, and real CLI contracts. Treat the older plan as input, not implemented truth.
11. `TaskPacket` already includes role/provider/model request, plan/base/config hashes, skills, ownership, output schema, tools and budgets. `authorize_invocation` compares provider/model/executable/cwd and only verifies exact provider-tool lists for Claude. Any new profile/routing identity must be bound through packet, dispatch claim, controller authorization, and persisted evidence, not only added to Invocation.
12. `adapters.py` sends bounded prompts; Copilot has no `--agent` field today. Claude Builder/Integrator are response-only with tools disabled. Native nested subagents are not equivalent to controller-managed workers: they may hide calls, budgets, lineage and tools. No native dynamic agent execution has been validated across these providers.
13. Official GitHub docs describe `.github/agents/*.agent.md`, Copilot `--agent`, tool lists and `include-custom-instructions`. Personal profiles can override same-ID repository profiles; discovery/environment defaults may add instructions/MCP. Reading a repo file hash does not prove those exact effective instructions executed. Claude's native subagent interface must be verified against the installed CLI before claiming support. Provider-neutral inline task packets are already possible. `AGENTS.md` is repository guidance, not an agent-spawn API.
14. Previously run 4 analyzer tests and 35 surrounding tests passed before current decision. Earlier full checks timed out (600s), last output at a subprocess stress test. Do not claim current full-suite or live provider acceptance. Installed/version-probed is not authenticated/model-ready.
15. Self-improvement excludes core policy/evidence/workflow/supervision/config/tests. Any new router/profile/learning/control-plane modules need explicit policy ownership; generated agents must not edit their evaluator or permission boundary.

## Coverage required

Assess product benefit and opportunity cost; project scoping/provenance; profile versus role versus provider versus underlying model; supported capabilities; dynamic team selection versus dynamic provider routing; state/serialization/config migration; deterministic fallbacks and unknown health; quotas/costs/privacy; prompts/tools/MCP and profile shadowing; fresh independent review; concurrency/capacity/timeouts/cancellation; crash recovery/human edits/publication; baseline/evaluation/holdouts/selection bias; negative memory/version drift; developer UX/CLI/MCP/macOS visibility; Council integration and license/provenance; maintenance and phased delivery. Identify any gap with an owner and test. Never assert measured gains that have not been observed.

## Council protocol and execution facts

Canonical source supplied by user: https://github.com/0xNyk/council-of-high-intelligence . Observed upstream main SHA: `bb42b4b58fd86b9058f2b3385d692937280d901c` (GitHub API). Installed skill is loaded; personas at `/Users/avipattan/.agents/skills/council/agents/council-{name}.md`. Use full three-round protocol with the 12-member exploration-orthogonal panel, not all 18 members. Domain weight locked before original analysis: Meadows=1.5; others=1.0.

Distinct methods: Socrates elenchic-questioning; Feynman first-principles-reconstruction; Sun Tzu adversarial-simulation; Machiavelli incentive-backward-induction; Ada formal-stepwise-verification; Lao Tzu via-negativa; Aurelius negative-visualization; Torvalds empirical-reduction-to-practice; Karpathy gradient-empiricism; Sutskever scaling-extrapolation; Kahneman bias-audit-system2; Meadows causal-loop-mapping.

All twelve completed the initial restatement gate: capability selection, minimal competent teams, constrained execution graphs, evidence-driven learning, incentives, uncertainty and recovery. No materially divergent interpretation required user clarification.

Earlier preliminary Round 1 yielded four generic native-host analyses. Eight external Codex/Gemini wrappers errored on zsh's read-only `status`; a second eight-call batch returned no usable stdout while stderr was suppressed. Neither batch is a valid analysis, and exact external failure causes remain UNKNOWN. The native Task tool does not expose seat model selection: earlier Anthropic/Opus labels were not verified and are corrected here.

Resume all twelve with a fresh, grounded independent Round 1 from this brief; archive preliminary material as non-voting. All subsequent seats use native `general` tasks, per-seat model UNKNOWN. Effective provider diversity is unverified (treat as one execution family), despite detector provider_count=3. A fresh native chairman session, separate from all deliberating seats, is locked now; it will synthesize only after cross-examination, enforcement, and final stances. No claim of Claude/Opus chairman. Full verbatim outputs will be saved; Round 2 receives anonymous A..L labels without the mapping.

## Initial restatements (coordinator condensation)

- Socrates: Is dynamic routing the real bottleneck? Select auditable capabilities, not maximum agents.
- Feynman: Find the simplest observable architecture and smallest competent team per experiment.
- Sun Tzu: Locate decisive leverage in an auditable control plane.
- Machiavelli: Expose authority, maintenance incentives, cost, failure and credit.
- Ada: Compile situations into constrained, auditable execution graphs.
- Lao Tzu: Remove unnecessary orchestration before adding adaptive machinery.
- Aurelius: Preserve trust and recovery through provider uncertainty.
- Torvalds: Ship boring, testable orchestration with visible selection decisions.
- Karpathy: Compare deterministic selection with measured learning on held-out tasks.
- Sutskever: Explore bounded composition without erasing accountability.
- Kahneman: Counter anchoring and overconfidence in team choice.
- Meadows: Design feedback so optimization does not degrade its own evidence.
