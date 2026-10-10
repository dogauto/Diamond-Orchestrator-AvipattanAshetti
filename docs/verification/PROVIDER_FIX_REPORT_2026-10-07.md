# Provider blocker remediation report

Date: 2026-10-07.

## Gemini

The failure was diagnosed from the sanitized CLI error, not guessed:

```text
Gemini CLI is not running in a trusted directory. To proceed, either use
--skip-trust, set GEMINI_CLI_TRUST_WORKSPACE=true, or trust this directory.
```

The adapter now supplies `--skip-trust` for headless isolated execution. A
subsequent authorized run launched successfully and returned exit zero with a
session and a JSON response. The response shape is a multiline JSON object with
`session_id`, `response`, and `stats.models`; `results.py` now normalizes the
`response` field and reports the successful model from `stats.models`.

The Gemini response parser is covered by deterministic regression tests. A
post-fix live confirmation could not be completed because the Gemini daily
quota was reached. No more Gemini invocations will be attempted today.

Status: `FIXED_DETERMINISTICALLY; LIVE_QUOTA_BLOCKED_TODAY`.

## Antigravity CLI

Google's official Antigravity documentation defines `agy` as a separate
headless CLI. Its JSON envelope uses `response`, `conversation_id`, `status`,
and usage fields; plan mode is selected with `--mode=plan`, and terminal
sandboxing with `--sandbox`. The IDE application itself remains an interactive
surface and is not treated as a supervised subprocess.

The adapter and parser support this contract, and Planner/Researcher routing
now selects `antigravity` instead of Gemini CLI. The app and `agy` executable
are now installed. Diamond's safe doctor probe reports `agy` version `1.3.1`;
authentication and live model execution remain intentionally untested.

Status: `PASS_FOR_STREAM_AND_STRUCTURED_SMOKE`; detailed evidence is in
`docs/verification/ANTIGRAVITY_LIVE_REPORT_2026-10-07.md`.

## Codex

The failure was diagnosed from the structured Codex error:

```text
Model 'gpt-6.1-sol' is not in the catalog. Use 'auto' (or omit the 'model'
field) to auto-route, or call /v1/models for the available list.
```

The adapter now supplies `--model auto` when no model is explicitly requested,
while preserving explicit requested models. The current parser-bound smoke run
returned the exact marker, exit zero, a session ID, and enforced read-only
sandboxing. The reported model remains `UNKNOWN` because this Codex event did
not expose it in the normalized fields.

Status: `PASS_FOR_SMOKE`.

## Omnirush

Omnirush does not expose a CLI plan/read-only flag. The controller now closes
that gap on macOS by wrapping plan-mode Omnirush invocations in
`/usr/bin/sandbox-exec` with a deny-by-default profile. The profile permits
provider networking and process/runtime reads, isolates cache writes, permits
provider-owned configuration paths, and denies writes inside the task workspace
except for `.claude-flow` runtime metadata. The deterministic suite includes a
write probe that confirms a workspace write is denied.

The real sandboxed Omnirush smoke returned the exact marker with exit zero,
valid text normalization, and `permission_enforced: true`. Its model/session
identity remains `UNKNOWN`. The protection is scoped to workspace writes; the
later packet/tool policy gates still need to bind exact owned paths and
capabilities for production dispatch.

Status: `PASS_FOR_SMOKE; HOST_WORKSPACE_WRITES_PROTECTED`.

## Current provider-fix source digest

The SHA-256 of the concatenated current adapter, result, supervisor, skill, and
packet module bytes is:

```text
8cd9b4132c079e1c7276afde467619af4109ece343369c636d3468bb3719ef80
```

The Codex and Omnirush provider-fix runs used the current adapter/result/
supervisor bytes at execution time. Gemini remains blocked only on the external
daily quota gate, not on an unresolved invocation or parser defect.
