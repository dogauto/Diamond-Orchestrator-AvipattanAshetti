### Essential Question
Which option increases accepted-change throughput while preserving control over effective execution?

### Terrain Map
**Facts:** Diamond's Copilot adapter lacks `--agent` (`src/diamond/adapters.py:135-142`); exact tool selection is Claude-only (`src/diamond/packets.py:290-295`). BRIEF fact 13 reports personal-profile shadowing and ambient instructions/MCP.
**Assumption:** No adversary established; simulate controller/runtime/configuration conflicts.

### Position Assessment
**Inference:**
- **A:** Cheapest attributable baseline; fixed routes still inherit configuration drift and outages.
- **B:** Bounded composition permits situation-specific skills and deterministic eligibility, but requires per-provider verification and schema migration.
- **C:** Flexible teams might discover better approaches; sparse, mixed evidence makes online optimization vulnerable to rewarding easier tasks or weaker reviewers.

### The Decisive Point
Bind role, profile origins/hashes, effective prompts/tools/MCP, CLI version, requested/observed model, project lineage, and routing reason through packet, dispatch, authorization, and evidence. A repository hash alone cannot establish effective execution. Isolate discovery or reject unverifiable native profiles.

### Adversarial Response
**Simulation:** A personal same-ID profile broadens tools; quota failure triggers fallback inheriting different instructions. Require predeclared eligible fallbacks, fresh authorization/session, unchanged budgets, and four delivery gates. Unknown health cannot establish eligibility; ambiguous completion requires reconciliation before retry. Fresh sessions do not prove model-family independence.

### Verdict
Adopt A now; pilot B after execution-boundary verification and publication repair, comparing held-out accepted changes per cost/time against A. Keep C experimental.

### Confidence
**Medium. Unknowns:** Installed CLI isolation, actual model identity, and composition benefit remain unverified.

### Where I May Be Wrong
**Objection:** B's control overhead may outweigh composition gains.
**Proposed falsification test:** Adapter maintainer injects profile shadowing, ambient MCP, and quota failure in a sandbox. Any unrecorded capability or reused authorization falsifies B's containment premise; retain A.
