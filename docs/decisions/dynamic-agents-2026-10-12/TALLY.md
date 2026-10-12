# Final stance tally and protocol audit

The twelve final stance lines are in `round3-*.md`, not inferred from prose.

| Seat | Method | Stance | Base | Confidence factor | Vote | Dealbreaker |
| --- | --- | --- | ---: | ---: | ---: | --- |
| Socrates | elenchic-questioning | staged-B | 1 | 0.75 (med) | 0.75 | no |
| Feynman | first-principles-reconstruction | staged-B | 1 | 0.75 (med) | 0.75 | no |
| Sun Tzu | adversarial-simulation | staged-B | 1 | 0.75 (med) | 0.75 | no |
| Machiavelli | incentive-backward-induction | staged-B | 1 | 0.75 (med) | 0.75 | no |
| Ada | formal-stepwise-verification | staged-B | 1 | 0.75 (med) | 0.75 | no |
| Lao Tzu | via-negativa | staged-B | 1 | 0.75 (med) | 0.75 | no |
| Aurelius | negative-visualization | staged-B | 1 | 0.75 (med) | 0.75 | no |
| Torvalds | empirical-reduction-to-practice | staged-B | 1 | 0.75 (med) | 0.75 | no |
| Karpathy | gradient-empiricism | staged-B | 1 | 0.75 (med) | 0.75 | no |
| Sutskever | scaling-extrapolation | staged-B | 1 | 0.75 (med) | 0.75 | no |
| Kahneman | bias-audit-system2 | staged-B | 1 | 0.75 (med) | 0.75 | no |
| Meadows | causal-loop-mapping | staged-B | 1.5 | 0.75 (med) | 1.125 | no |

W_total = 12.5 (undiscounted base weights).
Consensus threshold = (2/3) * 12.5 = 8.333333….
staged-B = 11 * 0.75 + 1.5 * 0.75 = 9.375, clearing the threshold.
A-only = 0; C-first = 0; abstain = 0.

`staged-B` means repair current authorization/publication/evidence, retain fixed routing as control, permit a bounded instruction-profile experiment, then adopt further routing/composition only when justified. It does not mean that performance gains have been proven or that every later implementation phase is unconditionally approved.

## Enforcement

- Restatement gate: all twelve completed; shared brief preserves condensations and corrected execution metadata.
- Grounded round 1: all twelve <=400 words; shared facts supplied, no peer analyses supplied. Earlier four generic responses are preliminary and not counted as evidence or votes.
- Round 2: full anonymous A..L packet; twelve responses <=300 words, each engages two peers and adds a claim/test. Full anonymous and identity-restored audit transcripts retained.
- Dissent quota: satisfied through non-overlapping objections (contract proliferation, statistical versus recovery gates, unsupported economic thresholds, native-agent necessity).
- Novelty: satisfied, including extended restart/cancellation tests.
- Agreement >70%: two counterfactual enforcement dispatches, to Lao Tzu and Karpathy; both saved and supplied for round 3.
- Evidence labels: all round-2 responses label reasoning; predominantly mechanistic/strategic, one ethical component, few empirical components. No claim of new independent live benchmark evidence.
- Anti-recursion: no further prompt required.
- Final stances: all twelve explicit and parseable; no missing-stance call.
- Chairman: fresh native `general` task distinct from all panel tasks; host controls model, exact seat model UNKNOWN.

## Limitations and corrected provenance

This is a full-mode protocol with the twelve-member exploration-orthogonal panel, not an eighteen-member full panel. The original verbal reference to 'full Council' meant full deliberation and was imprecise about panel size.

Provider detector reported three available entries; native Anthropic support was assumed by that script, not established by this host. Initial native tasks were incorrectly labeled Anthropic/Opus in commentary. Sixteen external attempts (eight errored wrappers, eight empty responses) yielded no accepted analysis. Their stderr was suppressed, so exact provider failure causes are unknown. Nothing here claims those provider sessions were authenticated or their outputs represented in the vote.

All accepted grounded analyses and final stances used native tasks. Distinct reasoning methods and sessions are established; model-family diversity is not. Treat effective provider spread as one execution family. The transcript is a reasoning aid, not twelve independent empirical observations.

The current checkpoint also includes user/other-session commit `65dc97f`, which introduced health/routing/schema changes after the earlier review. The plan uses that source baseline and preserves those changes.

## Session metadata

```yaml
schema_version: 1
mode: full
profile: exploration-orthogonal
panel_size: 12
rounds_run: 3
preliminary_round: partial_non_voting
enforcement_calls: 2
enforcement_breakdown: {agreement_check: 2, dissent_quota: 0, novelty_gate: 0, anti_recursion: 0, missing_stance: 0}
tools_used: yes
detected_provider_count: 3
effective_execution_families: 1
provider_count: 3
verified_model_diversity: false
seat_model: unknown
chairman: fresh_native_general_task
fallbacks_triggered:
  - feynman: codex/gpt-5.4 -> native-host/unknown
  - ada: codex/gpt-5.4 -> native-host/unknown
  - torvalds: codex/gpt-5.4 -> native-host/unknown
  - sutskever: codex/gpt-5.4 -> native-host/unknown
  - sun-tzu: gemini/gemini-3-pro -> native-host/unknown
  - machiavelli: gemini/gemini-3-pro -> native-host/unknown
  - karpathy: gemini/gemini-3-pro -> native-host/unknown
  - meadows: gemini/gemini-3-pro -> native-host/unknown
input_tokens_estimate: unknown
output_tokens_estimate: unknown
duration_seconds: unknown
```
