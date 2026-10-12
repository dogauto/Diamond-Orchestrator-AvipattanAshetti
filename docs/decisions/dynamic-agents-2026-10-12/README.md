# Dynamic agents: Council decision record

Date: 2026-10-12. Project baseline: `65dc97f`.

## Read these first

1. [Independent Chairman verdict](VERDICT.md)
2. [Implementation plan and coverage matrix](../../plan/DYNAMIC_AGENTS_PLAN.md)
3. [Shared source-grounded brief](BRIEF.md)
4. [Counted votes and execution limitations](TALLY.md)

Decision: **staged-B**. Repair the fixed-route execution/evidence foundation, test a narrow bounded specialization layer, and make later routing/team adaptation conditional on demonstrated value. A no-change/fixed-routing result remains valid.

Council source supplied by user: [0xNyk/council-of-high-intelligence](https://github.com/0xNyk/council-of-high-intelligence), observed upstream commit [`bb42b4b58fd86b9058f2b3385d692937280d901c`](https://github.com/0xNyk/council-of-high-intelligence/commit/bb42b4b58fd86b9058f2b3385d692937280d901c). Installed protocol/persona resources were used; this task did not install or vendor a new Council runtime. Preserve its MIT attribution if a later implementation vendors those resources.

## Deliberation evidence

- `round1-{member}.md`: twelve independent grounded analyses.
- [Anonymous round-1 packet](round1-anonymous.md): full peer text supplied to cross-examiners without member mapping.
- `round2-{member}.md`: twelve cross-examinations, each engaging two other positions.
- [Anonymous round-2 transcript](round2-anonymous.md) and [identity-restored audit](round2-audit.md).
- Counterfactual enforcement: [Lao Tzu](counterfactual-lao-tzu.md), [Karpathy](counterfactual-karpathy.md).
- `round3-{member}.md`: final positions and explicit confidence/stance/dealbreaker lines.
- [Tally](TALLY.md): base-weight denominator, locked 1.5× seat, enforcement counts and execution metadata.

All accepted analyses used native-host tasks. Exact seat models and cross-provider diversity were not verified. Earlier external calls yielded no accepted output; preliminary analyses were excluded. The Council's confidence weights are protocol mechanics, not statistical confidence in a performance gain.

## Plan verification

Independent documentation review identified and corrected four gaps before handover:

1. Preregistered, blinded evaluation and complete assignment accounting now precede the first live profile trial, rather than waiting for adaptive-learning infrastructure.
2. Planner's direct, non-persisted provider call is explicitly assigned to M4 controller-dispatch integration, with command-deduplication and interrupted-planning tests.
3. Existing hardcoded Gemini vetoes are included in eligibility migration; explicitly requested smoke providers stay pinned.
4. CLI descriptions distinguish actual `doctor`/delivery inspection from proposed improvement commands.

Final checks: all 13 relative links in the plan and this index resolved during independent review; the command inventory was checked against `cli.py`; all twelve final stance lines match the tally. Whitespace checks passed for the tracked documentation diff and every new decision/plan file.

This deliverable changes documentation only. Runtime findings need their listed regressions and fixes; prior passing checks do not certify the proposed architecture. No live provider/model acceptance or adaptive-performance benchmark is claimed.

## Follow-up ownership

- Coordinator: plan and decision evidence delivered in this record.
- Controller maintainer: first implementation slice is M0 publication/recovery tests and repair.
- Evidence/evaluation maintainer: M1 source/lineage/digest correctness; M2 preregistration before live experimentation.
- Operator: provider eligibility/privacy, affordable budgets, usefulness criteria, maintenance ownership and desired operating horizon before trials.
- Review point: 28 days after the first pilot starts, or immediately on an invariant breach. Append confirmed/revised/reversed/inconclusive outcomes; preserve this original rationale.
