For SQLite transaction work, trace the existing transaction boundary and
identify which invariants must hold before commit and after rollback. Check
foreign keys, immutable history, revision comparisons, duplicate command
handling, busy timeouts, and interrupted writes against the supplied code.
Distinguish recoverable filesystem work from database-atomic operations.

Preserve explicit migration and idempotency contracts. Return complete
replacement contents only for the controller-owned files, in its required
JSON format. You may explain a missing regression case, but cannot edit the
trusted tests or claim that a test ran. The controller executes verification.
