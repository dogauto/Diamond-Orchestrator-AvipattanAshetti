# Final current-source live provider smoke report

Date: 2026-10-07.
Authorization: user-approved final current-source smoke pass for all five
providers. No diagnostic retry was run in this final pass; the earlier bounded
Gemini/Codex retries are retained in the rerun report.

Candidate digest:

```text
3b03fe738c0a57b616b2f6e38b83d29ace58b2cc42c7bf7bfed922008129d486
```

This digest is the SHA-256 of the concatenated bytes of the adapter, result,
supervisor, skill, and packet modules at execution time. Each provider ran once
in an isolated temporary workspace with a 45-second deadline, a 256 KiB
combined output limit, process-group cleanup, and no project or Astro edits.
Raw provider output is not persisted.

Prompt contract:

```text
Reply with exactly the text FLAWLESS_DIAMOND_PIECE_SMOKE_OK and nothing else.
Do not use tools, edit files, or access the network.
```

| Provider | Invocation flags after executable | Execution | Exit | Parse | Marker | Permission | Session | Reported model | Evidence |
| --- | --- | --- | ---: | --- | --- | --- | --- | --- | --- |
| Gemini | `-p --output-format json --approval-mode plan` | nonzero | 55 | execution_failed | no | enforced | no | UNKNOWN | stdout 0 bytes; stderr SHA-256 `4b1f64d84586577c28b6194c35672c52ac96c61d67e10ae7f77fb981d85419cf` |
| Codex | `exec --json --sandbox read-only --skip-git-repo-check --ephemeral` | nonzero | 1 | execution_failed | no | enforced | no | UNKNOWN | stdout 796 bytes; stderr 39 bytes; stderr SHA-256 `1aa26269eb1cc57f86b235a03cda53c004edb5b1e9fc99d4da4f00843293d721` |
| Omnirush | `-p` | succeeded | 0 | valid | yes | not enforceable by CLI | no | UNKNOWN | stdout 32 bytes; SHA-256 `21bdbb1d709c71244a48722385bb628e9a0f0248f78a728a4654224834e62b06` |
| Copilot | `-p --output-format json --no-ask-user --mode plan` | succeeded | 0 | valid | yes | enforced | yes | `gpt-5.6-luna` | stdout 90,477 bytes; SHA-256 `413f3d67cc62d6712b2d5da67195f634b564e1939db8356683bc101eb64932ba` |
| Claude Code | `-p --output-format json --no-session-persistence --permission-mode plan` | succeeded | 0 | valid | yes | enforced | yes | UNKNOWN | stdout 1,616 bytes; SHA-256 `205407aa43277508691a8f1290e28a690f01a3164494ea94f9d1151304254b9d` |

## Gate interpretation

- **Gemini: BLOCKED.** Final smoke exit 55 with no stdout. The earlier
  bounded diagnostic retry repeated the same exit and stderr digest, but the
  sanitized evidence does not prove the underlying cause.
- **Codex: BLOCKED.** Final smoke exit 1. The earlier bounded diagnostic retry
  repeated the same exit and stderr digest; the underlying cause remains
  unclassified.
- **Omnirush: PASS_FOR_SMOKE; BLOCKED_FOR_PRODUCTION_RESTRICTION.** The exact
  marker returned, but the installed CLI does not expose an enforceable plan or
  read-only mode in the verified command surface.
- **Copilot: PASS_FOR_SMOKE.** The nested JSON event parser reassembled the
  assistant response, and the exact marker, session, and reported model were
  captured.
- **Claude Code: PASS_FOR_SMOKE.** The exact marker and session returned. Model
  identity was not reported and remains `UNKNOWN`.

The orchestrator is not production-ready: Gemini and Codex remain blocked,
Omnirush cannot satisfy the required host-enforced production restriction, and
controller-side evidence/session persistence, recovery, builder/debugger,
integration, and independent release gates remain incomplete.
