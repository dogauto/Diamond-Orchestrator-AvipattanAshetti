# Current-candidate live provider smoke report

Date: 2026-10-07.
Authorization: user-approved current-candidate smoke rerun for all five
providers, plus one bounded diagnostic retry for Gemini and Codex after a
failed smoke attempt.

Candidate digest:

```text
70d85fd15111a109ef8e75c7e1ecc4e1e6ede1fb09f3feb9871d7ddb80fffc5d
```

After this run, deterministic Copilot chunk-reassembly hardening was added to
the result parser and the suite was rerun successfully. That post-run source
change means this report remains valid historical evidence for the recorded
attempts, but an exact-final-candidate smoke rerun is still required before
claiming live acceptance.

This digest is the SHA-256 of the concatenated bytes of the current adapter,
result, supervisor, skill, and packet modules at execution time. Each attempt
used an isolated temporary workspace, a 45-second deadline, a 256 KiB combined
output limit, process-group cleanup, and no project or Astro edits. Raw provider
output is not persisted.

Prompt contract:

```text
Reply with exactly the text FLAWLESS_DIAMOND_PIECE_SMOKE_OK and nothing else.
Do not use tools, edit files, or access the network.
```

## Current-candidate smoke results

| Provider | Invocation flags after executable | Execution | Exit | Parse | Marker | Permission | Session | Reported model | Evidence |
| --- | --- | --- | ---: | --- | --- | --- | --- | --- | --- |
| Gemini | `-p --output-format json --approval-mode plan` | nonzero | 55 | execution_failed | no | enforced | no | UNKNOWN | stdout 0 bytes; stderr SHA-256 `4b1f64d84586577c28b6194c35672c52ac96c61d67e10ae7f77fb981d85419cf` |
| Codex | `exec --json --sandbox read-only --skip-git-repo-check --ephemeral` | nonzero | 1 | execution_failed | no | enforced | no | UNKNOWN | stdout 796 bytes; stderr 39 bytes; stderr SHA-256 `1aa26269eb1cc57f86b235a03cda53c004edb5b1e9fc99d4da4f00843293d721` |
| Omnirush | `-p` | succeeded | 0 | valid | yes | not enforceable by CLI | no | UNKNOWN | stdout 32 bytes; SHA-256 `21bdbb1d709c71244a48722385bb628e9a0f0248f78a728a4654224834e62b06` |
| Copilot | `-p --output-format json --no-ask-user --mode plan` | succeeded | 0 | valid | no | enforced | yes | `gpt-5.6-luna` | stdout 25,035 bytes; SHA-256 `307fb98c76a5814beedaa77adfc59a016654ea9304f5480879f6ff943096348d` |
| Claude Code | `-p --output-format json --no-session-persistence --permission-mode plan` | succeeded | 0 | valid | yes | enforced | yes | UNKNOWN | stdout 1,599 bytes; SHA-256 `f9b3ddd5ec7789f7db2b83a0764debd1bc54992df213d133091da7f06896146d` |

## Bounded diagnostic retries

Gemini and Codex each received one retry using the same current-candidate
invocation and prompt. Both repeated the same execution status, exit code, and
stderr digest as their first attempt. The classifier labels are heuristic only;
they do not establish authentication, quota, network, or provider-cause facts.

| Provider | Retry execution | Exit | Heuristic class | Retry evidence |
| --- | --- | ---: | --- | --- |
| Gemini | nonzero | 55 | `workspace_or_sandbox` | same stderr SHA-256 as smoke; stdout 0 bytes |
| Codex | nonzero | 1 | `unclassified` | same stderr SHA-256 as smoke; stdout SHA-256 `6e14fafd12eb7451ff045660f3e5bdd259b9ee746d28e0662342ad1aac42830d` |

## Gate interpretation

- **Gemini: BLOCKED.** Repeated exit 55 with no stdout. The bounded evidence
  does not prove the underlying cause, so no automatic fallback or relaunch is
  authorized.
- **Codex: BLOCKED.** Repeated exit 1 with structured-looking stdout but no
  accepted execution result. The underlying cause remains unclassified.
- **Omnirush: PASS_FOR_SMOKE; BLOCKED_FOR_PRODUCTION_RESTRICTION.** The exact
  marker returned, but the installed CLI does not expose an enforceable plan or
  read-only mode in the verified command surface.
- **Copilot: BLOCKED.** The current nested-event parser produced a valid result,
  session, and reported model, but the required marker was not present. This is
  either a provider response mismatch or an incomplete final-content contract;
  the result is not accepted as the smoke contract.
- **Claude Code: PASS_FOR_SMOKE.** The exact marker and session returned. Model
  identity was not reported and remains `UNKNOWN`.

The deterministic suite and current-candidate evidence do not establish overall
production readiness. Gemini, Codex, and Copilot remain blocked; Omnirush lacks
the required host-enforced restriction for production dispatch; and model
identity reporting is incomplete for Claude and Omnirush.
