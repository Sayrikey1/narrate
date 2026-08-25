# Probe results

Run 2026-08-21T11:31:51+00:00 against `eleven_v3` with voice `EXAVITQu4vr4xnSDxMaL`.

Submitted 59 characters, billed 12.

## Findings

- Plain text: 12 submitted, 3 billed.
- Audio tags billed partially: +3 for 11 tag characters.
- **`previous_text` is rejected** by eleven_v3 (HTTP 400: Providing previous_text or next_text is not yet supported with the 'eleven_v3' model.) — this model has no text-conditioning continuity either.
- `speed` is **accepted** (not rejected) by eleven_v3. Whether it has any audible effect is a separate question — the docs say it does not on v3.
- Concurrency ceiling for this model family: 3. Safe to raise `concurrency` to it.

## Cases

| Case | Question | Submitted | Billed | Result |
|---|---|---|---|---|
| `baseline` | What does plain text cost? | 12 | 3 | ok |
| `audio_tag` | Are audio-tag characters billed? | 23 | 6 | ok |
| `previous_text` | Is context text billed? | 12 | — | HTTP 400 — Providing previous_text or next_text is not yet supported with the 'eleven_v3' model. |
| `speed_field` | Does this model accept `speed`? | 12 | 3 | ok |

## Response headers observed

```
access-control-allow-headers: *
access-control-allow-methods: POST, PATCH, OPTIONS, DELETE, GET, PUT
access-control-allow-origin: *
access-control-expose-headers: request-id, history-item-id, character-cost, regeneration-count, generation-info, current-concurrent-requests, maximum-concurrent-requests, collect-preference
access-control-max-age: 600
alt-svc: h3=":443"; ma=2592000
character-cost: 3
collect-preference: True
content-length: 21359
content-type: audio/mpeg
current-concurrent-requests: 1
date: Fri, 21 Aug 2026 11:31:47 GMT
history-item-id: tFo6KODr41Cb5jL3fmnL
maximum-concurrent-requests: 3
request-id: oKG8zOBWPuX4mN1THuLn
server: uvicorn
strict-transport-security: max-age=1800;
tts-latency-ms: 447
vary: Accept-Language
via: 1.1 google
workspace-concurrent-requests: 1
x-region: europe-west4
x-trace-id: f072e44189577818198059c710572f7f
```
