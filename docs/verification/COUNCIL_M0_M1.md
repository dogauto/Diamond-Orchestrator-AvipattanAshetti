# Council M0/M1 implementation

Date: 2026-10-12. Initial source baseline: `65dc97f`. During implementation,
external commits `3962820` and `9880d31` recorded the Council documents and
implementation checkpoints; the continuation preserves those commits. This
agent has not created a commit or migrated a live ledger.

## Implemented boundaries

- `self_improve_cli.py` replaces the large CLI branch. `run` ends at verified
  isolated source; `review` creates an isolated four-stage delivery target;
  `publish` requires the exact current independent acceptance.
- `improvement_publication.py` binds project, workflow revision, delivery,
  candidate, config, source dependencies, and trusted test inputs. Publication
  intent commits before filesystem replacement. Durable original/candidate
  bytes and modes support fresh-process recovery after partial replacement or
  a missing completion receipt. Conflicting human edits remain untouched.
- Cancellation persists a promotion fence. Existing workflow/delivery
  cancellation, late-result and ownership checks still apply. Pending or
  conflicted publication reserves the project in the authoritative ledger.
- `improvement_store.py` captures bounded source/policy/test inventories and
  replays publication transitions. Schema 8 adds immutable checkpoints,
  cancellation, publication intents/events, dispositions, and evidence indexes.
  Upgrade is explicit and transactional; historical metadata is not invented.
- `daily_improve.py` and `improvement_evidence.py` validate evidence digests,
  use bounded indexed snapshots, retain scope/lineage/citations, distinguish
  reviewer claims, preserve later passing observations, and record version-
  scoped negative evidence and other operator dispositions with expiry.
- `supervisor.py` handles transient macOS process-group permission errors by
  continuing bounded cleanup observation. Persistent denial is still failure;
  a terminal parent alone does not prove its process group has disappeared.
- Backup acceptance includes the current schema's tables. The macOS console's
  legacy event fallback reads historical workflow events, while new improvement
  state uses the normal persistent project ledger.

## Verification record

- Before repairs, regressions reproduced immediate unreviewed publication,
  unguarded low-level promotion, and rollback erasing a newer human edit.
- Focused publication/CLI checks passed after repair, followed by evidence,
  scope, digest-corruption, query-plan, truncation, disposition, migration-
  rollback, and backup checks.
- A 1,200-record fixture verifies the evidence-window index, absence of a
  temporary sort, a 100-row read limit, and citation retention without inflating
  incident counts.
- The previous subprocess stress timeout did not reproduce in isolation or
  in the subsequent complete test run. The first new full-run watchdog was a
  total-suite deadline and expired during ordinary integration tests; the
  next run used a per-test watchdog and completed all 327 tests, with one
  intermittent existing cleanup failure. A repeated local probe identified
  transient `EPERM` during process-group exit. Dedicated regressions cover
  both transient and persistent denial after the repair.
- Final controller checks: **PASS**, 330 tests in 343.041 seconds on Python
  3.14.7; all 90 Python files compiled in memory, with no bytecode written.
  The run used `diamond.checking.main()` and a 90-second per-test traceback
  watchdog. The actual abrupt-exit regression calls `os._exit(91)` after the
  first replacement, then recovers through a fresh CLI process.
- Native build: **PASS**, `swift build --package-path examples/DiamondMac
  --scratch-path /var/folders/7g/r534nhd54h9ff_xyfpy0t0240000gn/T/omnirush/diamond-council-m0m1-build`.
  The initial 120-second attempt timed out; the retry completed in 9.20 seconds.
  This verifies compilation/linking, not interactive UI behavior.
- Documentation/code whitespace: `git diff --check` passed.

Full controller-check invocation used:

```sh
DIAMOND_TEST_TMPDIR="/var/folders/7g/r534nhd54h9ff_xyfpy0t0240000gn/T/omnirush" \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests python3 -B -c \
'import faulthandler, unittest; from diamond.checking import main; start = unittest.TextTestResult.startTest; unittest.TextTestResult.startTest = lambda self, test: (faulthandler.dump_traceback_later(90, exit=True), start(self, test))[-1]; result = main(); faulthandler.cancel_dump_traceback_later(); raise SystemExit(result)'
```

## Scope and next milestone

This implements the first repaired fixed-routing foundation, not measured
adaptive-agent performance. Promotion recovery is scoped to the new
self-improvement service and its authoritative ledger; generic workflow and
delivery publication retain their existing quarantine/reconciliation behavior.
Publication is recoverable, not globally atomic across files and SQLite.

The next feature is M2's two-profile, fixed-provider pilot. Its live experiment
requires a frozen representative workload, blinded acceptance procedure,
total call/time budget, useful-outcome criteria, maintenance owner, economic
hurdle, and stopping rules. Native provider agents, M3 routing repairs,
parent daily-run orchestration, and learned policy remain planned.
