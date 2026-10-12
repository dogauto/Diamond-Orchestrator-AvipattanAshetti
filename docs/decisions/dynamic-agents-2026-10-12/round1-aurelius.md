### Essential Question
Which option preserves trust through restarts, uncertain execution, cancellation, and concurrent human edits?

### The Control Boundary
**Assumption:** local Python/SQLite remains authoritative. Control authorization, budgets, evidence, and publication; accept that timeout/cancellation cannot prove an external operation stopped.

### The Clear-Eyed Assessment
**Facts (BRIEF, source facts 3, 7–11):** gains are unmeasured; publication bypasses delivery gates; rollback overwrites intervening edits; routing is disconnected; health persistence conflicts with schema.

**Inference—negative visualization:**
- **A:** simplest baseline, but fixed routes alone cannot prevent duplicate execution after restart.
- **B:** bounded profiles and deterministic eligibility support accountability; stale health and unbound profile identities can defeat those bounds.
- **C:** free-form teams and online learning multiply ambiguous outcomes and attribution failures before benefits are established.

### The Duty
Preserve human work. Report uncertain completion honestly rather than treating cancellation as successful containment.

### The Resilient Path
Persist invocation identity and frozen profile/provider authorization before dispatch. Reconcile uncertain attempts before replay/fallback. Durably revoke publication authority on cancellation; fence late results. Require immutable candidates, all four fresh delivery gates, and journaled publication recovery. Detect live-file conflicts; rollback must preserve intervening edits. Keep these controls inside the monolith.

### Verdict
Retain A as baseline; evaluate B after recovery repairs. Reject C presently. Sacrifice adaptation speed for attributable recovery.

### Confidence
**Medium. Unknowns:** provider cancellation/receipt semantics, quota readiness, and B’s incremental benefit.

### Where I May Be Wrong
**Objection:** A may deliver equal value with less machinery.

**Falsification test:** before enabling B, the controller maintainer must inject crashes, cancellation, lost acknowledgements, and concurrent edits. Any unreconciled redispatch, unauthorized publication, or lost human edit defeats readiness; no held-out improvement over A defeats expansion.
