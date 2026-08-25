# Sound-effect probe

Run 2026-08-23T15:59:58+00:00. Generated 3 seconds of audio.

## Findings

- 0.5s billed `character-cost: 3` (6 per second).
- 1s billed `character-cost: 5` (5 per second).
- 1.5s billed `character-cost: 8` (5 per second).
- **`character-cost` is 5 per second, rounded up to the next whole unit.** That is not the 40 credits/second the subscription docs quote, which is consistent with API generations being discounted — the same pattern speech shows, where a 12-character request bills 3.
- The ledger is unaffected: effect cost is computed from duration at the published $0.12/minute API rate, and this header is recorded beside it for reconciliation rather than used to charge.

## Cases

| Requested | `character-cost` | Per second | Result |
|---:|---:|---:|---|
| 0.5s | 3 | 6 | ok |
| 1s | 5 | 5 | ok |
| 1.5s | 8 | 5 | ok |

## Response headers observed

```
access-control-allow-headers: *
access-control-allow-methods: POST, PATCH, OPTIONS, DELETE, GET, PUT
access-control-allow-origin: *
access-control-expose-headers: character-cost
access-control-max-age: 600
alt-svc: h3=":443"; ma=2592000,h3-29=":443"; ma=2592000
character-cost: 3
content-length: 8821
content-type: audio/mpeg
date: Sun, 23 Aug 2026 15:58:53 GMT
server: uvicorn
strict-transport-security: max-age=1800;
vary: Accept-Language
via: 1.1 google
x-region: us-central1
x-trace-id: 06ce258ae7a85f8934be09c299b8a6d8
```
