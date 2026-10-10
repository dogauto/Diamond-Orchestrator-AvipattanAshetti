# Roles and skills

**Status:** contracts/default routing and the pinned advisory skill catalog are
implemented; role-specific packet loading is verified; no worker or scheduler
is running.

## Distinct concepts

- **Role:** responsibility and expected deliverable, such as Builder or QA.
- **Provider:** CLI host selected for the role, such as Claude Code.
- **Model:** requested and reported inference identity, recorded separately;
  unavailable identity is `UNKNOWN`, not inferred from the provider.
- **Skill:** referenced procedural guidance, with version/digest and scope.
- **Executable tool:** a concrete permitted command or host capability.
- **Controller:** deterministic software enforcing contracts and evidence.

A skill does not grant a tool permission. A role assignment does not prove
model availability. Text saying a test passed is not test execution evidence.

## Role contracts and routing

Assignments are configurable in `config/diamond.toml`. The initial string
mapping selects one primary provider per role; alternatives below require
an explicit validated mapping change, not an undocumented automatic fallback.

| Role | Primary / configurable alternative | Required output and boundary |
| --- | --- | --- |
| Planner | Antigravity CLI / Gemini CLI | Accepted task/dependency plan, scope, criteria, inputs and risks; cannot authorize execution through prose |
| Researcher | Antigravity CLI / Gemini / Claude | Cited findings, source/version/date, uncertainties and applicability; does not invent capabilities |
| Architect | Claude | Bounded design/contracts, tradeoffs and validation approach; records unresolved choices |
| Builder | Claude / Omnirush | Owned-path patch, artifact manifest and actual checks for one bounded objective |
| Integrator | Claude | Reconciled base/patch digests, conflict decisions, integrated artifacts and fresh checks |
| Debugger | Codex / Omnirush | Reproduction evidence, supported root cause, bounded fix and regression evidence |
| QA | GitHub Copilot | Requirement-based functional/adversarial checks, actual outputs, findings and coverage gaps |
| Reviewer | Codex | Fresh-session review of exact revision, author independence, blocking findings and resolutions |
| Release | GitHub Copilot | Acceptance evidence audit and release candidate manifest; no implicit publish/commit permission |
| Controller | Deterministic software | Validation, state/CAS, dispatch, limits, provenance and evidence gates; no model mapping |

Omnirush remains enabled as a selectable Builder/Debugger provider even
though the initial mappings use Claude and Codex respectively. Antigravity is
the configured Planner/Researcher provider through its official `agy` headless
CLI; the Antigravity IDE app is an interactive surface, not the supervised
subprocess. No model IDs
are prescribed. Switching a provider invalidates capability assumptions and
requires its host prerequisites to be checked.

## Common task/context packet

The controller provides a schema-versioned packet containing task/attempt
IDs, objective, accepted plan revision/digest, target/base revision, acceptance
criteria, dependency artifacts and digests, role/provider, requested model,
owned paths, allowed tools/skills, required tests, timeout/correction budget,
output/report contract, and relevant findings. Missing inputs block dispatch.

Include only necessary excerpts and artifacts, each with source and digest.
Do not carry unrelated conversation, credentials, or unverified prior claims
as authoritative context. Any plan/base/skill/config change affecting the
packet makes it stale until explicitly refreshed and accepted.

Reports reference the packet revision and attempt. The controller validates
actual paths/artifacts/tests, rejects stale or malformed claims, and records
failure reasons. Reports cannot expand ownership, tool allowances, budgets,
or permission levels. Only validated controller commands change policy.

The current deterministic implementation is in `src/diamond/skills.py` and
`src/diamond/packets.py`. It hashes skill sources, checks role applicability and
declared capabilities, resolves dependencies, verifies current plan/base/config
digests, constrains owned paths to the workspace, and matches dependent
artifact size/digests. It is a packet contract slice, not dispatch, scheduling,
or host sandbox enforcement.

## Builder discipline

Before dispatch, define:

1. One bounded objective and explicit exclusions.
2. Exact owned files/directories and base artifact/revision digests.
3. Executable tools and host-enforced permissions needed for that objective.
4. Only relevant, verified skills and required prerequisites.
5. Required test commands, expected behavior, and acceptance artifacts.
6. Deadline and correction budget; no uncontrolled follow-on work.

The Builder submits a bounded change and evidence. Unexpected changed paths,
missing artifacts, failed checks, or stale context prevent acceptance. A
Builder cannot appoint itself sole final reviewer or substitute its narrative
for QA. Parallel Builders need nonconflicting ownership and integration.

## Debugger discipline

Use the sequence **failure packet -> reproduction -> supported root cause
-> bounded fix -> regression**.

- The actual failure packet contains command/argv, cwd, relevant redacted
  environment, inputs, base/plan digests, actual exit/signal, output, and
  expected versus observed behavior. An agent summary alone is insufficient.
- Reproduce in a controlled workspace and retain the failing evidence. If
  reproduction fails, record uncertainty and request specific missing data.
- Support the root cause with traces, a discriminating test, or inspected
  code plus observed behavior. A plausible explanation is still a hypothesis.
- Fix only the supported cause within assigned files; scope expansion needs
  a revised packet. Consume correction rounds according to controller policy.
- Run a regression that fails on the original defect and passes on the fix,
  plus relevant existing checks. Record exact artifacts and actual outputs.

At limit exhaustion or ambiguous execution, stop in a visible failed/blocked
state. Do not reset counters by opening an undocumented replacement attempt.

## Independent review and release

Final review uses a separate, fresh session with no inherited author chat.
Provide requirements, design decisions, exact diff/base digests, independently
captured test evidence, and known findings. Record the reviewer provider,
requested/reported model identity, CLI version, session ID, and author lineage.

No production-code author may be the sole final reviewer of its own change,
including under another role name. Where Integrator changes production code,
it joins the author lineage. A different CLI alone is not proof of independence;
verify session separation and author provenance. If independent review cannot
be established, acceptance is blocked.

Resolve findings with evidence and fresh checks on changed bytes. Release
audits all applicable gates, exact candidate digests, and unresolved findings.
Controller software enforces release blocking; agent approval text cannot
override it. Publishing or committing requires a separate explicit request.

## Installed advisory skill catalog

`config/skills/catalog.toml` is the project-local allow-list for vendored
Markdown guidance. Each entry pins the upstream URL and commit, Git blob ID,
MIT license, local SHA-256, scope, applicable roles, dependencies, and required
capabilities. `src/diamond/skill_catalog.py` resolves only relative files that
remain inside the project, re-hashes them before packet construction, rejects
catalog/source drift, and resolves dependencies through `SkillRegistry`.
Missing catalogs are a safe empty registry for projects that have not opted in;
malformed or changed installed catalogs block the affected workflow.

The selected GitHub sources are intentionally small and advisory:

| Skill | Pinned source | Runtime roles |
| --- | --- | --- |
| systematic-debugging | `obra/superpowers@8ca22dba9a94f28898bbce59f2537ff4d87c747d` | Debugger |
| test-driven-development | `obra/superpowers@8ca22dba9a94f28898bbce59f2537ff4d87c747d` | Builder, Debugger, QA |
| verification-before-completion | `obra/superpowers@8ca22dba9a94f28898bbce59f2537ff4d87c747d` | Builder, Debugger, Integrator, QA, Reviewer, Release |
| code-review-excellence | `wshobson/agents@46891e7e60da0e52baf1050b7b6391b64e84c6d9` | Architect, Integrator, QA, Reviewer, Release |

The controller loads only the role's resolved skills into the response-only
provider context and labels their text as untrusted process material. Skill
text cannot grant tools, execute commands, change files, expand ownership,
override evidence, or approve release. Provider packets still contain the
verified `SkillRecord` digests, and controller-owned tests remain authoritative.

Trail of Bits `skills` was reviewed but not vendored: its repository is
CC BY-SA 4.0, and the strongest candidate (`post-patch-validation`) executes
local code, writes artifacts/worktrees, and assumes tool/delegation capabilities
that do not fit the response-only provider boundary. It remains a design
reference only.

## Council and Task Observer

Council uses the actual installed Council of High Intelligence protocol when
needed. Routine low-impact choices proceed directly. Escalate consequential
architecture/governance/systemic failure decisions, or broad disagreement,
selectively. Preserve context, competing options, objections, uncertainties,
recommendation, and final decision in project-local decision records. Do not
replace the protocol with an improvised vote or invent a deliberation result.

Task Observer captures workflow observations, corrections, rejected approaches,
and reusable-method issues according to its installed contract. It is not
the authoritative task database, scheduler, event ledger, or readiness gate.
Its state must not replace SQLite task/attempt/evidence records.

## Workspace and tool boundary

Ownership rules and separate workspaces/worktrees coordinate edits and preserve
human work. They do not themselves constrain process access. Distinguish
declared tool scope from host-enforced restrictions, verify prerequisites,
and block capabilities whose required restriction cannot be enforced.
Only the controller writes the authoritative ledger; all worker output is
untrusted until validated against permissions, artifacts, and actual checks.
