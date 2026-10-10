# Acceptance criteria

**Initial status:** every requirement below began `NOT_STARTED`; current live
statuses are recorded where actual evidence exists. This remains a falsifiable
acceptance contract, not a production-readiness report.
Use [Phase gates](PHASE_GATES.md) for execution/evidence rules.

## Acceptance evidence standard

For each ID, record expected/observed results, command or injected scenario,
candidate/config/base digests, environment and CLI versions, sanitized actual
output/exit/signal, artifact digests, and reviewer/findings. Tests claiming
success without actual invocation evidence do not satisfy acceptance.
`UNKNOWN` identities are allowed only where reporting is unavailable and
the limitation is explicit; they are not evidence of a particular model.

Requirement failures remain release-blocking unless the approved scope is
explicitly revised with rationale and new criteria. Unsupported prerequisites
or absent evidence are blocked, not passing. Do not invent quality scores,
completion dates, performance claims, or test counts as substitutes.

## Foundation and persistence

| ID | Scenario | Required observable result |
| --- | --- | --- |
| F01 | Submit malformed TOML, unsupported schema, wrong types including bool-as-int, extra/missing fields, invalid limits or routing | Nonzero actionable validation error; no task/ledger mutation or provider invocation |
| F02 | Submit empty objective, malformed task ID, invalid packet/path, traversal or escaping symlink | Rejected at the relevant input boundary; no outside write or dispatch; no valid state corrupted |
| F03 | Run `validate-config`, task create/list/show/events and `doctor` with model-launch sentinels | Create persists only the task/event; reads do not mutate; no model session starts; doctor uses safe version probes and auth `NOT_CHECKED` |
| F04 | Attempt disallowed transition or transition with unmet plan/dependency/evidence guards | Rejected with state/revision/events unchanged; no launch side effect |
| F05 | Fault between event append and projection update, and before commit | On reopen, both event and projection are committed together or both absent; no partial state |
| F06 | Commit task mutations, stop process, restart and query/replay | Same committed IDs/states/revisions/ordered events; replay matches projection and invokes no executable |
| F07 | Two commands use the same expected revision | At most one accepted mutation; stale one reports conflict; no duplicate task revision/event or lost update |
| F08 | Concurrent controller writers; repeat command ID with identical and different payloads | Writer ownership prevents overlap; identical retry returns original committed result; changed payload conflicts; no duplicate mutation |
| F09 | Query empty ledger or missing ID; call launcher from another cwd and a spaced path | Deterministic empty list or explicit missing-ID error; launcher uses intended project/config/state and preserves argv/exit semantics |
| F10 | Migration/backup restore fails or is interrupted | Last valid ledger is retained/recoverable under documented procedure; failure is visible; no silent schema rewrite or replay execution |

## Execution, races, and recovery

| ID | Scenario | Required observable result |
| --- | --- | --- |
| E01 | Concurrent dispatch or duplicate scheduling for one task revision | One recorded claim/attempt may launch; rejected contender never starts external work; evidence identifies the launched attempt |
| E02 | Schedule more ready work than configured capacity | Active supervised workers never exceed default two or validated override; queued tasks stay visible and are not silently dropped |
| E03 | Fake process hangs/emits excessive output; cancel during execution | Deadline/output policy is enforced; streams do not deadlock; actual timeout/signal/cleanup is recorded; no premature success |
| E04 | Child process survives graceful termination or cleanup cannot be proven | Forced cleanup is bounded and verified where possible; uncertain work is quarantined/blocked; no replacement launch before reconciliation |
| E05 | Crash around intent, launch, start recording, external completion, or result commit | Restart classifies durable facts and external ambiguity; unknown execution blocks relaunch; no fabricated completion or exactly-once claim |
| E06 | Recovery sees PID reuse, stale ownership, late report, or racing cancellation | Verify process identity before signal; stale attempt/revision cannot mutate current state; late evidence is retained with classification |
| E07 | Output claims success but expected artifact is absent/changed/wrong type | No acceptance; actual canonical path/content/digest checks fail visibly; claimed artifact does not count as evidence |
| E08 | Accepted plan, config, input/base, dependency artifact, or skill changes before dispatch/review | Stale packet/evidence is rejected; refreshed acceptance required; no run under silently changed assumptions |
| E09 | Provider returns auth error, quota error, missing binary, nonzero exit, signal or malformed report | Distinct normalized outcomes and actual output/exit captured; no hidden fallback, blind retry, or PASS from narrative |
| E10 | Repeated correctable failures reach configured correction limit | Default two rounds or validated override enforced; exhaustion is failed/explicitly blocked; counters persist across restart |
| E11 | stdout/stderr/error/argv/env includes synthetic credentials | Persisted/console evidence strips secrets, marks redaction, retains useful outcome facts; no unredacted fallback logs |
| E12 | Report asks for extra tools/paths or supplies forged passed-test/release text | Runtime authorization/state remains unchanged; executable checks and controller gates, not prose, decide permissions/acceptance |

## Roles, review, and software behavior

| ID | Scenario | Required observable result |
| --- | --- | --- |
| R01 | Role is remapped to an enabled provider; requested model differs from reported or is unavailable | Validated routing used; requested/reported identity and source retained separately; absent fields `UNKNOWN`, mismatch visible |
| R02 | Unneeded skill is listed; required skill is missing, changed, or host prerequisite unavailable | Load only needed verified skills; missing/changed prerequisite blocks dispatch; source/version/digest/scope retained |
| R03 | Builder edits outside ownership, uses stale base, or omits required tests | Change cannot be accepted; actual paths/base/test evidence determine failure; unrelated human work is preserved |
| R04 | Debugger receives a real failure packet | Recorded reproduction and supported root cause precede bounded fix; regression fails on original and passes on fixed revision; uncertainty is explicit |
| R05 | Integration modifies code or encounters conflicting patches | Exact base/patch digests and decisions retained; Integrator becomes author for modified code; fresh integrated checks required |
| R06 | Author submits sole final review or review reuses author session | Acceptance blocked; a separate fresh session and independently recorded reviewer/author lineage required |
| R07 | Artifact changes after successful tests/review | Prior acceptance invalidated for changed bytes; required checks/review rerun on actual candidate digests |
| R08 | QA finds acceptance failure or Reviewer/Release finds a blocking issue | Release blocked until evidence-supported resolution and relevant rechecks; findings never closed by unsupported prose |
| R09 | Council is needed for consequential disagreement; observer records workflow status | Actual installed Council protocol/decision provenance used; observer information does not become authoritative ledger state |
| R10 | Workspace/worktree exists but host cannot enforce required tool restrictions | Scope limitations are explicit; required restriction blocks execution or uses an explicitly approved enforceable mechanism; workspace is not asserted to be a sandbox |

## Real provider acceptance

Each row needs an isolated real noninteractive execution. Authentication and
quota must permit the test, outputs must match the verified host protocol,
and version/session plus requested/reported identity must be captured or
explicitly `UNKNOWN` with a reporting reason. Test actual produced artifacts
and preserve sanitized actual exit/output. A help response is insufficient.

| ID | Provider | Initial live status | Additional condition |
| --- | --- | --- | --- |
| L01 | Gemini CLI | `FIXED / LIVE_QUOTA_BLOCKED_TODAY` | `--skip-trust` fixes the diagnosed workspace failure; final confirmation waits for daily quota reset |
| L06 | Antigravity CLI | `PASS_FOR_STREAM_AND_STRUCTURED_SMOKE` | `agy` 1.3.1 returned a valid structured report, conversation ID, and exact marker under plan/sandbox mode |
| L02 | Codex CLI | `PASS_FOR_SMOKE` | The adapter omits `--model` unless an explicit model is configured; exact marker and session returned |
| L03 | Omnirush CLI | `PASS_FOR_SMOKE / HOST_WORKSPACE_WRITES_PROTECTED` | Final exact marker returned under macOS sandbox; exact tool/path packet policy remains a later production gate |
| L04 | GitHub Copilot CLI | `PASS_FOR_SMOKE` | Final exact marker, session, and reported model returned |
| L05 | Claude Code | `PASS_FOR_SMOKE` | Final exact marker and session returned; model identity remains UNKNOWN |

## Release and clean installation

| ID | Scenario | Required observable result |
| --- | --- | --- |
| P01 | Fresh macOS installation using only documented project prerequisites | Python >=3.11, launcher/config validation, initial state creation and restart work; portable commands resolve; no hidden absolute-path/user-state dependency |
| P02 | Run full deterministic fake/fault matrix and isolated live acceptance on release candidate | Both evidence sets pass on exact candidate and supported versions; fake behavior is never presented as real-provider readiness |
| P03 | Fresh independent Release audit | Every applicable criterion has valid evidence, no unresolved release blocker, all unknowns/limitations explicit, release candidate manifest digests match delivered bytes |
| P04 | Attempt Astro production build before orchestrator production acceptance | Build remains blocked; after acceptance require a separately approved Astro brief, real build/tests and independent behavior review |

## Readiness decision

Foundation completion proves only its local slice. Provider help observations
prove only observed CLI surface. Production readiness is declared only after
both deterministic fake/fault and real live acceptance evidence passes,
clean-install/recovery checks pass, and fresh independent release review
finds no unresolved release-blocking findings. Record that decision against
the exact candidate; documentation creation itself meets none of these gates.
