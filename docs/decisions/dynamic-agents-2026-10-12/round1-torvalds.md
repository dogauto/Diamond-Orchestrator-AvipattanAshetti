### Essential Question
What smallest deliverable improves accepted fixes per cost/time?
**Method:** empirical-reduction-to-practice.

### What Actually Works
**Facts:** In-memory probes reproduced undefined `config` (`routing.py:113`), configured Antigravity becoming Claude, schema-7 health UPDATE rejection, and analyzer deduplication dropping supporting references. Inspection also found persisted monotonic deadlines, unknown-as-healthy defaults, and unbounded analyzer reads without digest verification (`provider_health.py`; `daily_improve.py:248-268`). Routing has no execution-path callers.

### The Maintenance Cost
**Inference:** A (fixed routes, better evidence) is cheapest but cannot tailor capabilities. B (constrained profiles, deterministic routing) reuses contracts but adds eligibility, schema, and authorization maintenance. C (free-form teams, online learning) provides flexibility but multiplies interacting policies before outcome attribution works.

### The Boring Solution
First deliverable: controller-owned offline baseline report with project-scoped, digest-verified evidence retaining every reference. Reuse `resolve_provider`, `TaskPacket`, six adapters, `EventStore`, and four delivery gates. Then trial opt-in B; bind profile/policy hashes before dispatch. Preserve schema-1 behavior through explicit migration; freeze running manifests. Repair existing router/health code before integrating it; use append-only health observations with rebuildable state.

### Over-Engineering Check
Keep nine roles; compose bounded profiles, not another runtime. Profiles refine roles; providers execute them; model identities remain separate. Tests: health upserts/reboot expiry; unknown/stale quota; disabled-provider exclusion; analyzer lineage/digest/deduplication/resolution; legacy configuration; authorization tampering; fresh review sessions. Controller maintainer owns these regressions and protected routing policy. Publication must pass independent delivery gates and preserve intervening edits.

### Verdict
Ship A's evidence foundation first; evaluate B next. Reject C now.

### Confidence
**Medium. Assumption:** task diversity makes composition useful. **Unknowns:** authenticated capabilities, representative gains, maintenance budget.

### Where I May Be Wrong
**Objection:** evidence repairs could delay useful specialization.
**Falsification test:** within a two-week matched held-out trial, reject B if independently accepted changes per cost/time fail to beat repaired A, or any budget/authorization/review invariant fails.
