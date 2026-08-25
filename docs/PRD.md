# PRD — Narration Pipeline
### Script-to-audio production tool with per-project cost accounting

**Status:** Draft v1
**Owner:** Sayrikey
**Last updated:** 20 August 2026

---

## 1. Problem

Producing long-form narrated video on ElevenLabs today means manual, repetitive work in a UI that isn't built for it. A 25–30 minute script must be split by hand into sub-5,000-character chunks (a hard v3 API limit), pasted one at a time, generated, downloaded, renamed, reordered and stitched. Every re-roll is an untracked cost, and there is no way to answer "what did episode 14 cost me?" after the fact.

On pay-as-you-go billing, that opacity matters. Cost is now variable and per-take rather than absorbed into a flat monthly quota, so an indecisive session shows up on the invoice. Producers need to see the spend *before* they commit to a generation and *attributed* to a project afterwards.

**One-line pitch:** paste a script, pick a voice, get a stitched narration track — with every character and dollar accounted for.

---

## 2. Goals

| # | Goal | Success measure |
|---|---|---|
| G1 | Turn a full-length script into production-ready audio in one operation | 30-min script → stitched track, no manual chunking |
| G2 | Make cost visible before spending | Pre-flight estimate within ±2% of actual |
| G3 | Attribute every dollar to a project and a take | Per-project, per-chunk, per-take cost query |
| G4 | Make re-rolls cheap and targeted | Regenerate one chunk without touching the other 12 |
| G5 | Keep delivery consistent across chunks | Same voice/model/settings applied to all chunks by default |

### Non-goals (v1)

- Video editing, timeline assembly, or subtitle burn-in
- Multi-tenant SaaS, billing, or team seats — single-operator tool first
- Voice cloning or voice design (use ElevenLabs' own UI, consume the resulting `voice_id`)
- Music, dubbing, or sound effects
- Replacing an NLE — this produces audio assets, not finished video

---

## 3. Users

**Primary — the producer (you).** Writes or receives a script, needs narration, cares about consistency and cost. Technical enough to run a local service but wants the workflow to be push-button on the fifteenth episode.

**Secondary — a contracted editor.** Given access to a project, can re-roll a chunk that reads badly and re-export, without touching API keys or knowing the cost model.

---

## 4. Core concepts

```
Project (a channel, client, or series)
  └── Script (one episode)
        └── Chunk (a ≤5,000-char segment, ordered)
              └── Take (one generation attempt; many per chunk)
                    └── Asset (the resulting audio file)
```

Selecting one take per chunk defines the **cut**. Exporting a cut stitches the selected takes in order.

This shape is deliberate: cost accrues at the **take** level, but value is measured at the **script** level. Everything in section 7 depends on that separation.

---

## 5. Functional requirements

### P0 — Must have

**F1. Script ingestion.** Accept `.md`, `.txt`, or pasted text. Detect explicit chunk markers (e.g. `## [CHUNK n]`) and honour them. Where absent, auto-chunk on paragraph boundaries, never mid-sentence, targeting 2,000–4,500 characters to stay clear of the 5,000-character v3 ceiling.

**F2. Chunk review before generation.** Show every chunk with its character count and estimated cost. Allow manual merge, split, and edit. Nothing is sent until the operator confirms.

**F3. Voice and model selection.** Fetch available voices from the account; select per project with per-chunk override. Model selectable per script (v3, v3 Conversational, v2 Multilingual, Flash/Turbo) with the rate shown alongside — the 2× price difference between tiers should be visible at the point of choice.

**F4. Customisation profile.** Per project, persist and apply: voice settings (stability, similarity, style, speaker boost, and speed where the model supports it — verify current support against the API reference), plus a **prefix tag block** (e.g. `[fast-paced][urgent]`) automatically prepended to every chunk. Overridable per chunk, since some sections want different direction.

> Note: prefix tags are billable characters. They must be included in the estimate, not stripped from it.

**F5. Batch generation.** Generate all chunks in one operation, in parallel within the account's concurrency limit, with retry and backoff. Partial failure must not lose completed chunks — the run resumes from where it stopped.

**F6. Take management.** Every generation is preserved as a numbered take with its full parameter set. Compare takes, select one as the cut, discard none automatically. Re-rolling chunk 7 leaves chunks 1–13 untouched.

**F7. Export.** Stitch selected takes in order to a single file (MP3 + WAV), with configurable inter-chunk silence. Also export individual chunk files for manual assembly in an NLE.

**F8. Cost accounting.** See section 7 — this is the differentiating requirement, not a reporting afterthought.

### P1 — Should have

**F9. Loudness normalisation** across chunks at export (target −16 LUFS), since take-to-take level drift is the most audible seam artefact.

**F10. Budget guardrails.** Per-project monthly cap and per-run cap. Warn at 80%, block at 100% pending override. Directly addresses re-rolls becoming a variable cost.

**F11. Script diffing.** On re-import of an edited script, identify which chunks changed and offer to regenerate only those. Editing one paragraph should not cost a full episode.

**F12. Pronunciation dictionary.** Per-project find-and-replace applied pre-submission (e.g. "10x" → "ten x", "2am" → "two in the morning") so fixes made once persist across episodes.

### P2 — Nice to have

**F13. A/B model comparison** — generate one chunk on two models, cost both, play side by side.
**F14. Runtime estimation** from character count, with drift reported against actual audio duration once generated.
**F15. Webhook/CLI trigger** for pipeline automation.
**F16. Silence trimming** at chunk head/tail.

---

## 6. Cost and credit accounting

The core feature. Requirements are split by billing mode because the account may be on either.

### 6.1 Dual-mode metering

| Mode | Unit | Source of truth |
|---|---|---|
| Pay-as-you-go | USD, at per-1k-character model rate | Local computation + API usage reconciliation |
| Subscription | Credits (1 char = 1 credit on v3/v2; 0.5–1 on Flash/Turbo) | Same, expressed in credits |

The system must record **both** figures for every take, since the account may switch modes and historical comparison should survive the switch.

### 6.2 Requirements

**C1. Pre-flight estimate.** Before any run: total characters (including prefix tags), per-model rate, projected cost, and — on subscription — remaining quota after the run. No generation without this shown.

**C2. Per-take attribution.** Every take records: character count submitted, model, rate applied, computed cost, timestamp, and whether it was selected into the cut. This is what makes "episode 14 cost $3.40, of which $1.10 was re-rolls" answerable.

**C3. Rollups.** Cost aggregated at chunk, script, project, and account level, over arbitrary date ranges.

**C4. Waste metric.** Cost of unselected takes, as an absolute figure and as a percentage of project spend. This is the single number that tells the operator whether their direction process is efficient — high waste means fix the script or the tags, not the voice.

**C5. Reconciliation.** Periodically fetch actual usage from ElevenLabs and compare against the local ledger. Surface drift above a threshold. Local computation is an estimate; the provider's counter is authoritative, and silent divergence would undermine the entire feature.

**C6. Rate configuration.** Model rates live in config, not code. ElevenLabs repriced during 2026 and will again; a price change must not require a deploy, and historical takes must retain the rate in force when they were generated.

**C7. Cost per finished minute.** Derived metric — project spend ÷ exported runtime. The number that actually informs "can I afford twice-weekly?"

---

## 7. Technical design

Stack chosen to match existing capability — FastAPI, Celery, Postgres, React.

```
React (Vite)  ──REST──▶  FastAPI  ──▶  Postgres (projects, scripts, chunks, takes, ledger)
                             │
                             ├──▶ Celery + Redis   (generation queue, retries, backoff)
                             │        └──▶ ElevenLabs TTS API
                             └──▶ S3 / local FS    (audio assets)
                                      └──▶ ffmpeg  (stitch, normalise, transcode)
```

**Queue over synchronous calls.** A 30-minute script is 6–13 API calls, each taking seconds to tens of seconds. Celery gives retries, concurrency limiting, and resumable partial runs for free — F5 is otherwise painful.

**Concurrency ceiling.** Respect the account's concurrent-request limit; exceeding it produces rejections, not queuing. Make it a config value, default conservative (2–3).

**Idempotency.** Each generation request carries a key derived from `(chunk_text, voice_id, model, settings_hash)`. A retry after a network failure must not double-charge. This is the most important correctness property in the system — a duplicate charge is worse than a failed run.

**Ledger is append-only.** Cost records are never mutated. Corrections are compensating entries. Reconciliation (C5) depends on an immutable history.

**Secrets.** API key from environment or a secrets manager, never persisted in the database and never returned to the client.

---

## 8. Key flows

**Happy path.** Import script → auto-chunk → review chunks and estimate → confirm → batch generate → audition takes → select cut → export stitched track → view episode cost.

**Re-roll.** Open chunk → adjust tags or edit text → see marginal cost → generate new take → compare → promote to cut → re-export.

**Budget stop.** Run would exceed cap → blocked with projected overage shown → operator overrides or trims scope.

---

## 9. Non-functional requirements

- **Reliability:** partial run failure never loses completed takes; runs resume.
- **Rate limits:** exponential backoff on 429; surface the provider's message rather than a generic error (the daily-cap message that blocked production once was itself informative).
- **Latency:** UI never blocks on generation; progress streamed per chunk.
- **Data retention:** audio assets configurable — keep all takes, or prune unselected after N days.
- **Auditability:** every ledger entry traceable to a request and response.

---

## 10. Success metrics

| Metric | Target |
|---|---|
| Time from final script to exported audio | < 15 min for a 30-min episode |
| Manual steps per episode | ≤ 5 |
| Estimate accuracy vs reconciled actual | within 2% |
| Waste ratio (unselected take spend) | < 25% by episode 5 |
| Cost per finished minute | tracked, trending down |

---

## 11. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Provider reprices or changes credit model | Ledger figures become wrong | Rates in config; historical rate stored per take (C6) |
| Character limits or model names change | Chunking breaks | Limits configurable per model; validate against API before run |
| Take-to-take tonal drift on expressive models | Audible seams | Identical settings by default; F9 normalisation; flag when a chunk's settings diverge from project profile |
| Duplicate charges on retry | Direct financial loss | Idempotency keys; append-only ledger |
| Commercial licensing on the account tier | Published audio out of compliance | Confirm terms for the active plan before v1 use; surface plan mode in UI |
| Scope creep toward video editing | Never ships | Non-goals section is binding for v1 |

---

## 12. Milestones

**M1 — Pipeline spine (2 weeks).** Ingest, chunk, generate, export. CLI-driven. Proves the loop end to end.

**M2 — Cost ledger (1 week).** Pre-flight estimate, per-take attribution, rollups, reconciliation. C1–C5.

**M3 — Producer UI (2 weeks).** React interface for chunk review, take audition and selection, cost dashboard.

**M4 — Production polish (1 week).** Normalisation, budget caps, script diffing, pronunciation dictionary.

---

## 13. Open questions

1. Does the active plan's commercial licence cover published, monetised output? Blocking for real use.
2. Does the current API expose a `speed` parameter for the chosen model, or is pacing controlled only via tags and text? Determines whether F4 exposes a slider or a tag editor.
3. Keep every take indefinitely, or prune? Storage is cheap, but take sprawl hurts the audition UI.
4. Single-operator local tool, or hosted with accounts from day one? Affects M3 scope substantially.