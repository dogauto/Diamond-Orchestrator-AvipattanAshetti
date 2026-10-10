# Flawless Diamond Piece: project aim

## Objective

Build a standalone local orchestrator for Gemini CLI, Antigravity CLI (`agy`),
Codex CLI, Omnirush CLI, GitHub Copilot CLI, and Claude Code. It must coordinate their work to
produce verifiable, production-ready software with traceable decisions,
bounded execution, reproducible checks, independent review, and recovery.

The delivery order is mandatory:

1. Establish a production-ready orchestrator on macOS.
2. Use that verified orchestrator to build an actual Astro project against
   a separately approved product brief and acceptance criteria.

The project name expresses an aspiration. Readiness is determined by
recorded evidence, not the name, an agent's confidence, or generated code.

## How the objective will be achieved

- A Python 3.11+ local controller owns scheduling and state transitions.
- Provider-specific adapters normalize real CLI behavior without assuming
  identical options, output formats, authentication, or model reporting.
- A transactional SQLite event ledger records work and derives queryable
  task projections; workers never directly write authoritative state.
- Explicit role contracts and only-needed skills give workers bounded
  objectives, paths, tools, context, and required verification commands.
- Separate integration, QA, review, and release checks evaluate exact
  artifact versions. Failures enter bounded correction or remain blocked.
- Small implementation slices pass deterministic checks and independent
  review before expansion. Live provider checks are deliberate and logged.

## Concrete success criteria

The orchestrator is production-ready only when all applicable requirements
in [Acceptance criteria](../verification/ACCEPTANCE_CRITERIA.md) pass:

- A clean macOS install can run the documented launcher and validate config.
- All six adapters have isolated, recorded real noninteractive executions
  demonstrating authentication, result capture, and identity reporting or
  an explicit `UNKNOWN` identity with its reporting limitation recorded.
- Invalid requests and illegal/stale transitions cannot mutate valid state.
- Events and projections stay consistent across restart and injected faults.
- Concurrency limits, timeouts, termination, cancellation, and correction
  limits are enforced by software and evidenced by actual execution results.
- Ambiguous external execution is reconciled before another launch; recovery
  does not silently duplicate work or claim exactly-once provider execution.
- Every accepted result has artifact digests, actual test exit/output
  evidence, and a fresh final review that is not solely by its code author.
- Logs strip secrets; configuration contains no credentials; worker prose
  cannot grant permissions, mark tests passed, or authorize release.
- Both deterministic fault-injection and live acceptance gates pass, with
  no unresolved release-blocking findings.

The subsequent Astro project succeeds only when its approved features work,
its real build and relevant tests pass, and independent review accepts the
same delivered revision. Its product scope is not defined by this document.

## Scope

In scope: local CLI, TOML configuration, typed validated contracts, SQLite
persistence, subprocess supervision, role/skill routing, isolated working
directories or worktrees, artifact/test evidence, integration, review,
bounded corrections, recovery, and macOS install/launch verification.

Initial implementation scope: `doctor`, `validate-config`, task
`create/list/show/events`, legal transitions with optimistic concurrency,
persistence tests, and launcher scripts. Creating a task does not start AI.

Out of scope: cloud orchestration, microservices, distributed consensus,
hosted dashboards, automatic credential setup, unbounded retry loops,
autonomous publishing, and an Astro build before orchestrator acceptance.
Workspaces and path ownership alone do not provide a security sandbox.

## Current boundary

The documentation and TOML configuration have been reviewed, and the bounded
foundation slice has passed independent review. Runtime completion,
authentication, live model readiness, and overall production readiness require
their own evidence. See [Status](../progress/STATUS.md) for the current
evidence boundary.
