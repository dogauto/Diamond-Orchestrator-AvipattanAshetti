### Essential Question
Which typed compilation boundary permits situational agents while preserving frozen authorization?

### Computational Skeleton
**Facts—BRIEF items 1, 6, 8–11:** configuration is strict; four delivery gates are mandatory; routing is disconnected; packet authorization incompletely checks tools.

**Inference—proposed versioned contracts:**
1. `SituationV1`: project/task lineage, base/config hashes, required capabilities, privacy, budget.
2. `ProfileV1`: immutable identity/digest, role, effective instructions/skills, tools, output schema.
3. `BlueprintV1`: dependency DAG, ownership, mandatory gates, aggregate resource limits.
4. `RouteV1`: provider/model/version, eligibility proof, health timestamp/expiry, policy digest, selection reason.

### What Can Be Mechanized
Controller validators should check capabilities, DAG validity, ownership and budgets; bind contract digests through packet, dispatch claim, authorization and evidence. Treat unknown health explicitly. Re-routing requires newly authorized packets.

Version configuration and serialized manifests explicitly; use transactional SQLite migrations, resolve health-trigger/DML conflicts, preserve frozen legacy records, reject unsupported versions. Lifecycle: proposed → authorized → dispatched → completed/failed/cancelled/unknown. Reconcile unknown outcomes before retry; journal publication, enforce fresh review sessions and compare live hashes before publishing or rolling back.

### What Cannot Be Mechanized
Task relevance, instruction quality and genuine review independence require judgment; typed declarations cannot prove them.

### Abstraction Assessment
**Inference:** A minimizes migration risk and establishes evidence, but cannot express reusable situational composition. B offers bounded, testable composition. C combines unvalidated generation and learning, expanding both authority and attribution uncertainty.

### Verdict
Choose staged B after A’s evidence/authorization repairs. Keep evaluators and policy controller-owned.

### Confidence
**Medium. Assumption:** stable profiles cover recurring needs. **Unknowns:** effective native instructions, model readiness, representative baseline, operational cost.

### Where I May Be Wrong
**Objection:** B may add machinery without useful throughput.
**Falsification test:** controller maintainer compares A/B on held-out, project-stratified tasks. Reject B if accepted changes per cost/time do not improve, or replay admits an unauthorized transition.
