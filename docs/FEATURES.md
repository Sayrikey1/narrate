# What narrate does

Everything currently supported, generated from the code rather than from
memory — 40 CLI commands, 33 HTTP endpoints, 7 pages, and 414 Python plus 67
frontend tests.

The organising idea: **every character is accounted for.** A 25–30 minute
script becomes a stitched narration track, and afterwards you can answer "what
did episode 14 cost me?" exactly, from an append-only ledger rather than a
running total.

---

## 1. Scripts in, chunks out

| Feature | Detail |
| --- | --- |
| Ingest a script | `.txt`, `.md`, `.markdown`, `.text`. Paste, browse, or drag onto the page |
| Byte validation | Extension, a 1 MB ceiling, and that the bytes really are UTF-8 — an extension proves nothing |
| Automatic chunking | Split to fit the model's request ceiling, on paragraph → sentence → clause → hard boundaries in that order |
| Chunk review | Per-chunk character count and cost **before** generating |
| Manual control | `chunk merge`, `chunk split`, `chunk rechunk`, and per-chunk overrides of voice, model, delivery and tags |
| One ingestion path | The CLI, the API and the tests all call `ingest_script`. Two paths that diverged would mean markers stripped on one and spoken — and billed — on the other |

### Markers stripped before anything is billed

Chunk text goes straight into the request, so anything left inline is read
aloud and charged for. On `eleven_v3` a bracketed phrase is *additionally*
interpreted as an audio tag, so a stray marker can change the delivery of the
narration around it.

| Marker | Meaning |
| --- | --- |
| `[@ 02:15]` | A target start time. Reported as drift, never enforced — speech duration cannot be dialled to a mark |
| `[SFX: wind howling, 4s]` | An effect slot, and a forced chunk boundary so the cue gets an exact timeline position |
| `[SFX: soft rain, 30s, loop]` | A looping ambience bed |
| `[CAST] Morag = <voice_id>` | Declare who speaks in this script |
| `[VOICE: Morag]` | Switch speaker mid-paragraph |
| `Morag: her line` | A speaker turn — **only** when Morag is cast |

**Markdown is layout, not speech**, so it is stripped too. Emphasis, links,
inline code, bullets and quote marks lose their syntax and keep their words;
images and rules go entirely; a heading is dropped outright, because
`# The Keeper's Log` would otherwise be narrated in front of the episode that is
already called that. Ordinary prose survives untouched: `5 * 3`,
`output_format` and `$0.19` are all left alone, and that is a test.

---

## 2. More than one person speaking

Cast a project's characters once and they carry across its episodes.

**A speaker prefix only counts when the name is cast.** That single rule is what
makes the feature safe to run over existing writing: `Note:`, `Warning:`,
`Chapter one:` and `12:30` have no cast entry, so they stay narration.
Recognising speakers by capitalisation instead would eventually delete a word
from somebody's prose, and silently. A name that looks like a speaker but is not
cast is reported, because a misspelled cast entry doing nothing at all is the
confusing case.

| Mode | How | Trade |
| --- | --- | --- |
| **Per-turn voices** | Each turn becomes its own chunk with its own voice | Works on **every model**, ordinary rate, each line separately re-rollable and separately costed |
| **Dialogue** | Consecutive turns become one `POST /v1/text-to-dialogue` request | The model hears the whole exchange. **`eleven_v3` only**, capped at **2,000 characters across all turns** — tighter than v3's own 5,000 — and a re-roll re-charges the group |

Dialogue falls back to per-turn on a model that cannot do it, and says so.
A single-speaker passage takes the ordinary speech path, because a one-turn
dialogue request would cost more for nothing.

**Dialogue pricing is not documented.** No ElevenLabs page states it and the
only figures available are third-party, so `dialogue_cost_multiplier` is `1.0`
and flagged `dialogue_cost_verified = false`. Estimates say the ordinary rate
until `narrate probe --live --dialogue` measures the real `character-cost`
header. A figure wrong by 1.8× is worse than one labelled unsure.

---

## 3. Voices and models

| Feature | Detail |
| --- | --- |
| Voice picker | Every voice on the account, filterable by name or accent |
| **Audition before selecting** | Two separate acts. The row is a `role="radio"`; the play button is its *sibling* |
| Proxied samples | `/api/voices/{id}/preview`, cached. The provider's link is nullable, expiring and cross-origin, and all three failures look like a dead button |
| Zero-cost auditions | A preview plays a sample the provider already hosts |
| Offline stand-ins | Audition as a short tone, because a preview that plays silence is indistinguishable from one that is broken |
| Delivery controls | Only the settings the chosen model **honours**. On v3 that is stability alone — speed, similarity and speaker boost do nothing there |
| Rejected settings | Dropped on save and reported, never quietly kept |
| Per-project profile | Voice, model, delivery, prefix tag block, monthly cap |
| Per-chunk override | Any of the above, for one chunk |

### Model registry

Rates live in `config/models.toml`, not in code — ElevenLabs repriced during
2026 and will again, and historical takes keep the rate that was in force.

| Model | Rate | Ceiling | Continuity | Also |
| --- | --- | --- | --- | --- |
| `eleven_multilingual_v2` | $0.10/1k | 10,000 | request stitching | the long-form default |
| `eleven_v3` | $0.10/1k | 5,000 | **none** | audio tags, **dialogue** |
| `eleven_v3_conversational` | $0.05/1k | 5,000 | **none** | audio tags |
| `eleven_flash_v2_5` | $0.05/1k | 40,000 | request stitching | separate concurrency pool |
| `eleven_flash_v2` | $0.05/1k | 30,000 | request stitching | English only |

Two deprecated Turbo models are listed so the CLI can name the replacement
rather than fail with an opaque error. `narrate models --sync` reconciles the
declared capabilities against `GET /v1/models` and **reports drift without
overwriting a rate**.

---

## 4. Generating

**One `Generate` produces a finished episode** — narration *and* the accepted
effect cues — and the monthly cap is checked against both together. Checked per
phase it would see roughly half the spend: on a real script, narration alone
came to $0.1928 against a $0.24 cap and would have passed, while the true
$0.2848 is correctly refused and nothing is sent.

| Feature | Detail |
| --- | --- |
| Dry run by default | `generate` spends nothing without `--go`, and even then shows the projected split and asks |
| Concurrency | Bounded, defaulting to 2. Exceeding the account's limit produces rejections, not queuing |
| Request stitching | Where the model supports it, so prosody continues across chunk boundaries |
| Resume | Client-side idempotency over a SHA-256 of everything that determines the audio. Re-running generates nothing |
| Re-roll one chunk | `--only 7 --force`, leaving the others untouched |
| Retry policy | Sorted by billing implication, not by HTTP status |
| Progress | Streamed per chunk over SSE. The UI never blocks |

### The error taxonomy that keeps the ledger honest

| Class | Meaning |
| --- | --- |
| `RetryableError` | Rejected before generating. Retrying cannot double-charge |
| `FatalError` | Will never succeed as written |
| `UnknownOutcomeError` | Sent, outcome never observed. **Never retried** — it may already have been billed, and a double charge is direct financial loss |

Unknown outcomes are recorded and surfaced for a human rather than guessed at.

---

## 5. Sound effects

Effects are **overlays**: they carry a timeline position but are not mixed into
the narration master, so an editor drops them on their own track.

| Feature | Detail |
| --- | --- |
| From markers | `[SFX: ...]` creates a slot and forces a chunk boundary |
| By hand | `effects add`, or the form on the Effects page |
| LLM suggestions | `effects suggest` proposes placements via Groq. **Nothing is generated unaccepted** |
| A library, not a list | The same cue at four points is **one generation and one charge**, placed four times |
| Reuse across runs | By idempotency key, and only when the audio is still on disk |
| Reuse within a run | Two slots asking for the same cue in one run produce one generation |
| Billed per second | Not per character. Cost is computed from measured duration at the published rate; the `character-cost` header is recorded separately for reconciliation |

`narrate probe --live --effects` settled what that header actually contains —
5 per second, rounded up, not the 40 credits/second the subscription docs quote.
Written down in [probe-effects.md](probe-effects.md).

---

## 6. The timeline, and hearing it

| Feature | Detail |
| --- | --- |
| Two-lane track view | Narration below, effects in their own lane **above** — they are overlays, not inserts |
| Planned cues visible | Dashed blocks, so a hole in the sound design is impossible to miss |
| **Play the whole thing** | Takes in sequence with the export's own gaps, effects **layered** at their positions |
| Persistent transport | Lives in the shell, so playback survives navigation |
| Per-lane mute | Check the narration on its own |
| Click to seek | On either lane, or on a row of the plan |
| Keyboard | Space toggles play/pause |
| Audition one take | Per-chunk players, arbitrated so only one source is ever audible |

Position comes from one clock, not from any element's `currentTime`, because
several clips play at once and none of them is the clock. **Honest limitation:**
`<audio>` start scheduling is accurate to a few tens of milliseconds, so a cue
can sit slightly off its mark. Web Audio would be sample-accurate but only after
fetching and decoding every take up front — tens of megabytes before a
30-minute episode could play at all, which is the wrong trade for a preview.

---

## 7. Takes, cuts and export

| Feature | Detail |
| --- | --- |
| Every take kept | With its cost, duration and whether it is in the cut |
| Choose the cut | `cut set 1 7 --take 2` promotes a re-roll. A deliberate choice is never displaced |
| Dangling cut repair | A cut pointing at deleted audio is detected and named |
| Stitch | The **concat filter**, not the demuxer — inputs are heterogeneous and the filter also sidesteps the encoder-delay gap at every mp3 seam |
| Formats | `wav`, `mp3`, `m4a` (AAC in MP4, what an NLE imports), `flac`, `opus` |
| Timeline-named pieces | `Project_HH-MM-SS-mmm_HH-MM-SS-mmm.mp3`, applied at export so a re-roll never renames something already delivered |
| Zip per export | One download for the whole delivery |

### The editing plan

Every export ships `plan.md` — the document that guides the edit.

| Feature | Detail |
| --- | --- |
| Running order | Start, end, length, track, filename and content, per piece |
| Rendered as a document | Sortable table, numeric-aware — `4.00s` before `35.52s`, timecodes as times |
| Click a row to seek | The transport jumps to that moment |
| Outline | Jump between sections |
| Analysis view | Runtime by lane, drift outliers, cues still missing, longest and shortest chunk, cost per finished minute |
| Data twin | The same facts as JSON, so the page never re-derives them from prose |
| Any document | The viewer opens any `.md` or `.txt` artifact in the media library, before you download it |

Markdown is parsed by `marked` and **always** passed through DOMPurify. A script
can arrive from anywhere, so `<img src=x onerror=…>` in a `.md` is a real input.

---

## 8. Cost accounting

The differentiating requirement, not a reporting afterthought.

| Feature | Detail |
| --- | --- |
| Append-only ledger | Enforced by SQLite `RAISE(ABORT)` triggers, not by convention — a rule that lives only in a code review is not a rule |
| Integer micro-USD | Never floats. A ledger summing thousands of sub-cent amounts cannot afford binary drift |
| Per operation | Every generation, effect, suggestion and probe, with its rate, request id and unit kind |
| Provider's own figure | `character-cost` from the response header, not `len(text)` — 12 submitted characters billed as 3 |
| Self-calibrating estimates | A `billing_ratio` learned from observed headers |
| Rate card versioning | Historical takes retain the rate in force when generated |
| Waste ratio | Spend on takes that never made the cut — whether the *direction* is working, not the voice |
| Cost per finished minute | Divided by the runtime the document itself reports |
| Monthly cap | Warns at 80%, blocks at 100%, counts everything on the ledger that month |
| Reconciliation | Local ledger against the provider's own counter, with a drift threshold |
| Unknown outcomes | Listed for a human to resolve |

---

## 9. Where things live

| Feature | Detail |
| --- | --- |
| Managed database | `~/.local/share/narrate/narrate.db`, honouring `XDG_DATA_HOME` |
| Legacy fallback | An existing database in the project root **keeps being used**, and is reported. A tool that quietly stops seeing your ledger is worse than one using an awkward path loudly |
| `db status` | Which database is in use, and whether it is somewhere sensible |
| `db list` | Every narrate database on the machine, what is in each, and whether any of it was **real** money |
| `db adopt` | Copies into the managed location, checkpointing the write-ahead log first and verifying row counts. **Deletes nothing** |
| Media | `assets/`, browsable and downloadable per project |
| Migrations | Alembic, `batch_alter_table` throughout because SQLite cannot `ALTER` in place |

Why this exists: a project directory is not a data directory. Three stray
`narrate*.db` files accumulated in the project root during development and
nothing in the codebase created them — which is the problem. A database nobody's
code owns is one anybody's tooling can duplicate, and the ledger is the one file
here that cannot afford a shadow copy. Inspecting them is genuinely side-effect
free: `mode=ro` is not enough, because a read-only open of a WAL database still
builds the WAL index and creates files.

---

## 10. Surfaces

**CLI** — 40 commands across `project`, `script`, `chunk`, `cast`, `cut`,
`effects`, `cost`, `db`, plus `doctor`, `models`, `voices`, `estimate`,
`generate`, `takes`, `export`, `formats`, `media`, `timeline`, `plan`, `serve`
and `probe`.

**HTTP** — 33 endpoints. Deliberately thin: every one calls the same functions
the CLI does. Two entry points that disagreed about what a re-roll costs would
be worse than having one.

**Web** — 7 pages, all real URLs that survive a reload:

| Path | Page |
| --- | --- |
| `/` | projects, and the form for a new one |
| `/cast` | who speaks, and in which voice |
| `/script/:id` | track view, chunks and takes, generate, export |
| `/script/:id/effects` | cue slots, the library, suggestions |
| `/script/:id/media` | every artifact, with playback, reading and download |
| `/script/:id/plan` | the editing plan, as a document and as analysis |
| `/costs` | spend across every project |

Dark and light, following the OS unless overridden; `?theme=light` pins it in a
link. Every colour is declared once as `light-dark(…)`. A visible focus ring on
every interactive element, `prefers-reduced-motion` respected, and no horizontal
overflow down to 700px.

---

## 11. Spending nothing

| Feature | Detail |
| --- | --- |
| Mock provider | Produces **real, playable** audio and reports a `character-cost` the way the API does, so the ledger, chunker, runner and export all take the identical code path |
| `just demo` | The whole pipeline end to end against a scratch database |
| `just demo-dialogue` | Two speakers, both modes, including cue reuse |
| `NARRATE_PROVIDER=mock` | Anywhere, including the web UI |
| Tests | 414 Python + 67 frontend. None touches the network |
| Zero-cost commands | `models`, `voices`, `estimate`, `chunk review`, `timeline`, `plan`, `formats`, `media`, `cost *`, `db *`, and every voice audition |

`narrate probe --live` is the only command that spends without being asked
twice, and it costs about two cents. What it settled is written down in
[probe-results.md](probe-results.md) and [probe-effects.md](probe-effects.md) —
including that `eleven_v3` rejects `previous_text` outright, which would have
failed every chunk after the first.

---

## Not supported

Stated plainly, because a gap you know about is cheaper than one you discover:

- **Voice cloning or voice design.** Use ElevenLabs' own UI and consume the resulting `voice_id`
- **Postgres.** The seam exists — `NARRATE_DATABASE_URL` — but no dialect other than SQLite is supported or tested
- **Multi-user.** Single-operator by design; run state is in-memory and process-local
- **Loudness normalisation.** WAV masters are the correct input for it, done elsewhere
- **Effects mixed into the master.** They are overlays with positions, by design
- **`.docx` / `.pdf` scripts.** PDF in particular loses the paragraph breaks the chunker splits on
- **Verified dialogue pricing.** Declared at the ordinary rate and flagged unverified until probed
