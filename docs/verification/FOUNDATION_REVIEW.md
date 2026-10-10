# Foundation independent review

Date: 2026-10-07.
Scope: the initial local configuration, task ledger, and diagnostic CLI slice.
Reviewer: a separate read-only harness agent that did not author the runtime.
Review session: `ses_ee9bd89d8ffe6ISR3S11T4Cgfn`.

## Initial verdict: REVISE_FOUNDATION

The baseline 44 tests passed, but targeted review reproduced five defects.
The findings below require fixes and regression evidence before acceptance.

| ID | Severity | Observed defect | Required correction | Status |
| --- | --- | --- | --- | --- |
| RV01 | High | Selector initialization failure after process launch leaves the version-probe process and pipes alive | Guard the complete post-launch lifecycle and guarantee cleanup despite selector/stream errors | OPEN |
| RV02 | High | Implicit SQLite rowid collisions permit `INSERT OR REPLACE` to bypass explicit-key history protection | Remove rowid replacement surface and verify rejection for events and command records | OPEN |
| RV03 | Medium | An inaccessible existing ledger is reported as empty/missing on Python 3.14 | Distinguish filesystem absence from permission/I/O failure and return `STORAGE_ERROR` | OPEN |
| RV04 | Medium | A deeply nested TOML document returns an internal error instead of a config-validation error | Classify parser recursion/resource failures as `INVALID_CONFIG` | OPEN |
| RV05 | Medium | An oversized extracted version aborts doctor with `INVALID_REQUEST` | Treat malformed provider output as `invalid_output` and retain all provider results | OPEN |

## Evidence boundary

Review used isolated temporary ledgers and fake executables. It did not invoke
AI models or modify the project state. This review evaluates the foundation,
not the future scheduler, live provider adapters, or production readiness.

Resolution requires targeted regression tests, the full foundation checks,
and reviewer re-evaluation. Record final commands and outcomes in the foundation
verification report and the current project status.

## Re-review verdict: ACCEPT_FOUNDATION

Date: 2026-10-07.

All five findings were corrected and independently rechecked:

- RV01 diagnostic selector, capture, interruption, process-group, and pipe
  cleanup are covered by real fake-process fault tests.
- RV02 events and commands use SQLite `WITHOUT ROWID`; rowid and explicit-key
  replacement attempts are rejected, and schema 1 or mislabeled storage is
  rejected without migration.
- RV03 missing storage remains a non-mutating not-found result while permission
  and I/O failures return `STORAGE_ERROR`.
- RV04 deeply nested TOML is classified as `INVALID_CONFIG` with exit 2.
- RV05 oversized provider version output is classified per-provider as
  `invalid_output`; doctor continues and returns the complete result set.

Evidence:

- 62 tests pass on Python 3.11.14, 3.12.13, and 3.14.7.
- Python 3.14 verification passes with `ResourceWarning` treated as an error.
- `scripts/check.sh` passes and compiles 21 Python files in memory.
- A corrected readiness handshake removes the prior concurrent child-process
  test flake; one concurrent Python 3.11/3.12 run passes.
- Safe post-fix doctor probes all five installed CLIs successfully using only
  `--version`; authentication remains `NOT_CHECKED` and workflow execution is
  `UNAVAILABLE`.

This acceptance is limited to the local configuration, task-ledger, and
diagnostic foundation. It does not accept live model execution, adapters,
scheduling, Builder/Debugger workflows, recovery, clean installation, or
overall production readiness.
