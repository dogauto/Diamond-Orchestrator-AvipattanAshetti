### Essential Question
Can Diamond improve accepted changes per cost by removing failure paths before adding agents?

### The Intervention Audit
**Facts (source inspection):** `src/diamond/routing.py:54–68,113` ignores configured primaries and references undefined `config`. `src/diamond/cli.py:325–338` overwrites rollback targets and publishes on VERIFIED without independent delivery gates. **Brief-reported facts:** routing is unwired; representative performance evidence is absent. No regressions executed here.

### What Happens If We Do Nothing
**Inference:** fixed routing avoids migration cost, but publication/rollback defects remain. No-change is defensible for team generation, not these defects.

### What Can Be Removed
Remove the promotion shortcut and unsupported health assumptions. Avoid compulsory optional teams; preserve four delivery gates. A role contract need not require another worker; a profile need not become another agent class.

### The Minimum Effective Intervention
- **A:** lowest added complexity; repair publication/recovery and evidence provenance while retaining fixed routes.
- **B:** enables situation-specific eligibility but adds identity, authorization, migration and maintenance costs. Start with deterministic selection; require evidence before optional teams.
- **C:** adds unvalidated generation and online-learning costs to inadequate attribution; reject now.

**Assumption:** useful work can proceed with existing role contracts.

### Verdict
Choose **A now**; make B earn adoption against A. Count independently accepted useful changes per execution dollar and wall-clock time, including failures, coordination and reviewer effort.

### Confidence
**Medium. Unknowns:** provider readiness, effective instructions/tools, quotas, workload mix and comparative gains.

### Where I May Be Wrong
**Objection:** unavailable fixed providers may waste enough time to justify B immediately.

**Falsification test:** controller maintainer runs a preregistered, project-scoped paired holdout within two weeks. Switch to B if acceptance-per-cost improves ≥20% (proposed threshold), latency does not worsen, and authorization/recovery tests pass.
