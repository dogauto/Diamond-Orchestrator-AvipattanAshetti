# Live provider smoke report

Date: 2026-10-07.
Authorization: user-approved one isolated, non-editing smoke test per provider.
This report is historical evidence for the pre-refinement adapter candidate;
it is not current-candidate acceptance after subsequent argv, parser, and
supervisor changes.
Workspace: temporary directory under the approved `TMPDIR`; no project source
or Astro files were edited and no commit was created.

Prompt contract:

```text
Reply with exactly the text FLAWLESS_DIAMOND_PIECE_SMOKE_OK and nothing else.
Do not use tools, edit files, or access the network.
```

The controller used provider-specific noninteractive argv, a 45-second
deadline, a 256 KiB combined output limit, process-group cleanup, and result
normalization. Raw output is not persisted. The report keeps byte counts and
SHA-256 digests so a future diagnostic run can be compared without copying
provider output or credentials into project files.

| Provider | Execution | Exit | Parse | Marker | Session | Model | Evidence |
| --- | --- | ---: | --- | --- | --- | --- | --- |
| Gemini | nonzero | 55 | execution_failed | no | no | UNKNOWN | stdout 0 bytes; stderr SHA-256 `c3c51cd27f9f663ea49eb8c854b34f15db98035f8d10753957ec50fba026ab62` |
| Codex | nonzero | 1 | execution_failed | no | no | UNKNOWN | stdout 0 bytes; stderr SHA-256 `121db64788d9c8d29af50964b03ed3db1be4105f3d8ad29a6c5f343c59368f64` |
| Omnirush | succeeded | 0 | valid | yes | no | UNKNOWN | stdout 32 bytes; SHA-256 `21bdbb1d709c71244a48722385bb628e9a0f0248f78a728a4654224834e62b06` |
| Copilot | succeeded | 0 | valid | no | yes | UNKNOWN | stdout 14,378 bytes; SHA-256 `d391a0560bf8d493f3312f85021d3cc71308b8af2960a0035fa63c9830ecfc4f` |
| Claude Code | succeeded | 0 | valid | yes | yes | UNKNOWN | stdout 1,597 bytes; SHA-256 `37853e8715751fc6fc2967864aa3e22a936a5c7b6b58e9e095b9ac485963dc3c` |

## Gate interpretation

- **Omnirush: PASS for this smoke contract.** Text output returned the exact
  marker. Model identity was not reported and remains `UNKNOWN`.
- **Claude Code: PASS for this smoke contract.** Structured output returned the
  exact marker and a session identifier. Model identity was not reported and
  remains `UNKNOWN`.
- **Copilot: BLOCKED.** The process and parser completed and a session was
  present, but the normalized result did not surface the exact final marker.
  The adapter must be refined against Copilot's actual JSON event shape before
  acceptance.
- **Gemini: BLOCKED.** The nonzero exit is recorded, but the sanitized report
  does not establish whether the cause is authentication, quota, network, or
  invocation compatibility. A separately authorized diagnostic run is needed.
- **Codex: BLOCKED.** The nonzero exit is recorded, but the sanitized report
  does not establish whether the cause is authentication, quota, sandbox, or
  invocation compatibility. A separately authorized diagnostic run is needed.

This report does not establish production readiness. It establishes that the
supervisor can launch and classify real provider processes, while three provider
gates remain blocked and model identity reporting is incomplete for successful
providers.
