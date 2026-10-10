# Antigravity CLI live report

Date: 2026-10-07.
Authorization: user-requested implementation and one isolated authenticated
smoke run.

## Authentication probe

The installed `agy` CLI returned a successful JSON response for a harmless
marker prompt. Authentication is available on this host.

## Stream and structured-report smoke

The final smoke used:

```text
agy -p <prompt> --output-format stream-json --mode=plan --sandbox --json-schema <schema>
```

The task required a JSON report with a `summary` field. The process returned
exit zero and the normalized result contained:

```json
{"summary":"AGY_STREAM_SMOKE_OK"}
```

Observed evidence:

| Field | Value |
| --- | --- |
| Execution | `succeeded` |
| Exit | `0` |
| Parse | `valid` |
| Marker | present |
| Session | present (`conversation_id`) |
| Reported model | `UNKNOWN` |
| stdout bytes | `3,674` |
| stderr bytes | `0` |
| stdout SHA-256 | `1d410f88897f6e6a1c39f427d52f76370e3536c66bd022c38d1f0a0107f87428` |
| stderr SHA-256 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

The provider emitted both a Markdown-fenced JSON response and a structured
JSON result. The parser accepted the structured result and preserved the
conversation identity. No project or Astro files were edited.

## Implementation

- `OutputMode.STREAM_JSON` is supported for Antigravity.
- `--json-schema` is passed only for Antigravity structured reports.
- `conversation_id` is normalized as the session identifier.
- `result.status` values such as `ERROR` are normalized as
  `ParseStatus.PROVIDER_ERROR`, never as success.
- The current adapter/result/supervisor/skill/packet source digest is:

```text
29254ec92c51787685ff751b50be0ff8d1c998447ff1a874de279a279525732b
```

Status: `PASS_FOR_STREAM_AND_STRUCTURED_SMOKE`.
