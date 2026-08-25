# 🎙️ narrate

**Turn a long script into a stitched narration track — and know exactly what it
cost.**

A script-to-audio pipeline for long-form narration on ElevenLabs, with a CLI and
a web UI over the same code. Built for the case the vendor's own interface makes
painful: a 25–30 minute script that has to be split into sub-5,000-character
chunks, generated, re-rolled, reordered, stitched and delivered — while somebody
keeps track of the bill.

```text
📝 script.md  →  ✂️ chunks  →  🎙️ takes  →  ✅ the cut  →  🎧 master + plan.md
                                  ↕
                            💰 every charge, on an append-only ledger
```

---

## ✨ What it does

Producing long-form narration on ElevenLabs today means pasting chunks one at a
time, downloading, renaming, reordering and stitching by hand. Every re-roll is
an untracked cost, and there is no way to answer *"what did episode 14 cost
me?"* after the fact.

This does the mechanical part and keeps the receipts.

| | |
| --- | --- |
| ✂️ **Chunks a script properly** | Paragraph → sentence → clause → hard boundaries, sized to the model's real request ceiling |
| 🎭 **More than one voice** | Cast your characters; `Morag:` gives that line her voice. Optionally one true multi-speaker request per exchange |
| 🔊 **Sound effects as a library** | The same cue at four points is one generation and one charge, placed four times |
| 🎧 **Play the whole timeline** | Takes in sequence with the export's own gaps, effects layered at their positions — before you export anything |
| 📄 **Ships an editing plan** | `plan.md`: what plays when, which file, and where a cue still belongs |
| 💰 **Accounts for every character** | Append-only ledger, integer micro-USD, the provider's own billed figure — not `len(text)` |
| 🧪 **Spends nothing by default** | An offline provider produces real, playable audio through the identical code path |

### The shape of it

```text
Project (a channel, client, or series)
  └── Script (one episode)
        └── Chunk (an ordered segment sized to the model's request limit)
              └── Take (one generation attempt; many per chunk)
                    └── Asset (the resulting audio file)
```

Selecting one take per chunk defines the **cut**. Exporting a cut stitches the
selected takes in order.

Cost accrues at the take level; value is measured at the script level. That
separation is what makes *"episode 14 cost $3.40, of which $1.10 was re-rolls"*
a query rather than an archaeology project.

---

## 🚀 Quick start

Full per-platform instructions — macOS, Linux, Windows — are in
**[docs/INSTALL.md](docs/INSTALL.md)**. The short version:

```bash
# macOS
brew install ffmpeg node just
curl -LsSf https://astral.sh/uv/install.sh | sh

git clone <your-fork-url> narrate && cd narrate
uv sync
just ui-install     # skip if you only want the CLI
just doctor         # ffmpeg, database, key, provider reachability
```

Then run the entire pipeline **without spending anything** — the offline
provider writes real, playable audio and reports costs the way the API does, so
the chunker, runner, ledger and export all take the identical code path:

```bash
just demo               # one voice, five chunks, five cues, exported
just demo-dialogue      # two speakers, both modes
```

Ready to spend? Put a key in `.env` (copy `.env.example`) and open the UI:

```bash
just up
#   backend  http://127.0.0.1:8420
#   frontend http://127.0.0.1:5173   <- open this one
```

---

## 🎬 The loop

```bash
narrate models                          # compare rates, limits, capabilities
narrate voices                          # find a voice_id

narrate project new "My Channel" --voice <voice_id>
narrate cast set "My Channel" Morag --voice <voice_id>   # optional
narrate script add episode-14.md --project "My Channel"

narrate chunk review 1                  # counts, per-chunk cost, overrides
narrate estimate 1                      # what it will cost — spends nothing
narrate generate 1                      # dry run by default
narrate generate 1 --go                 # narration + effect cues, asks first

narrate takes 1                         # audition
narrate cut set 1 7 --take 2            # promote a re-roll into the cut
narrate timeline 1                      # what plays when
narrate export 1 --format wav,m4a,mp3   # masters + named pieces + plan.md

narrate cost report --script 1          # spend by operation, waste, cost/minute
narrate cost reconcile                  # ledger vs the provider's own counter
```

Re-rolling one chunk leaves the others untouched:

```bash
narrate generate 1 --only 7 --go --force
```

**40 commands** in all. `narrate --help` lists them; every one that spends says
so first.

---

## ✍️ Writing a script

Mark up the text and the tool does the rest. **Markers are stripped before
anything is sent**, so they are never spoken and never billed:

```markdown
[CAST] Morag = 21m00Tcm4TlvDq8ikWAM · Keeper = pNInz6obpgDQGcFmaJgB

[@ 00:00]

Morag: You knew it would end. Everyone knew, and nobody said it aloud.

Keeper: I hoped not. Hoping is a different thing from knowing.

[SFX: distant foghorn, single low blast, 4s]

[@ 00:40] The light went out on a Tuesday afternoon.
```

| Marker | What it does |
| --- | --- |
| `[@ MM:SS]` | A **target**, not a command. Speech duration cannot be dialled to a mark, so the plan reports the signed drift and leaves the response to you |
| `[SFX: description, 4s]` | Creates an effect slot **and forces a chunk boundary**, which is what gives the cue an exact timeline position rather than an interpolated one |
| `[CAST] Name = voice_id` | Declares who speaks in this script |
| `[VOICE: Name]` | Switches speaker mid-paragraph |
| `Name: her line` | A speaker turn — **only** when that name is cast |

> 🛡️ **A prefix only counts as a speaker when the name is cast.** That single
> rule is what makes this safe over existing writing: `Note:`, `Warning:`,
> `Chapter one:` and `12:30` have no cast entry, so they stay narration.
> Recognising speakers by capitalisation instead would eventually delete a word
> from somebody's prose, and silently.

**Markdown is layout, not speech**, so it is stripped too — emphasis, links,
bullets and quote marks keep their words and lose their syntax; a heading is
dropped outright, because `# The Keeper's Log` would otherwise be narrated in
front of the episode already called that. Ordinary prose survives untouched:
`5 * 3`, `output_format` and `$0.19` are all left alone, and that is a test.

---

## 🖥️ The web UI

Seven pages, all real URLs that survive a reload:

| Path | |
| --- | --- |
| `/` | Projects, and the form for a new one |
| `/cast` | 🎭 Who speaks, and in which voice |
| `/script/:id` | 📊 Track view, chunks and takes, generate, export |
| `/script/:id/effects` | 🔊 Cue slots, the reusable library, suggestions |
| `/script/:id/media` | 📁 Every artifact, with playback, reading and download |
| `/script/:id/plan` | 📄 The editing plan, as a document *and* as analysis |
| `/costs` | 💰 Spend across every project |

The timeline is a **two-lane track view** — narration below, effects in their
own lane *above*, because they are overlays over the speech rather than inserts
between it. Planned-but-ungenerated cues draw as dashed blocks, so a hole in the
sound design is visible rather than buried in a table row.

Press play once and you hear **the whole episode**: takes in sequence with the
spacing the export uses, effects layered at their own positions. The transport
lives in the shell, so looking at the cost page does not stop playback. Space
toggles play/pause; clicking either lane seeks.

🌗 Dark and light, following your OS unless you choose; `?theme=light` pins it
in a link.

---

## 💰 Cost accounting

The differentiating requirement, not a reporting afterthought.

```bash
narrate cost report --script 1
```

| | Why it is like this |
| --- | --- |
| **Append-only ledger** | Enforced by SQLite `RAISE(ABORT)` triggers, not by convention — a rule that lives only in a code review is not a rule |
| **Integer micro-USD** | Never floats. A ledger summing thousands of sub-cent amounts cannot afford binary rounding drift |
| **The provider's own figure** | `character-cost` from the response header, not `len(text)`. A live probe found 12 submitted characters billed as 3 |
| **Rate card versioning** | Rates live in config, not code. Historical takes keep the rate in force when they were generated |
| **Waste ratio** | Spend on takes that never made the cut — the number that says whether the *direction* is working, not the voice |
| **Reconciliation** | Local ledger against the provider's own counter, with a drift threshold |

### 🛑 Spending is opt-in

- `generate` **dry-runs unless you pass `--go`**, and even then shows the
  projected split and asks
- `--max-spend 2.00` hard-stops a run mid-flight
- `--monthly-cap` does the same across a calendar month

```bash
narrate project set "My Channel" --monthly-cap 20
```

Warns at 80%, blocks at 100% showing exactly which numbers caused it, and exits
non-zero.

> ⚖️ **The cap sees both halves of a run.** One `generate` produces narration
> *and* the accepted effect cues, so the projection covers both and is checked
> once before either starts. Checked per phase it would see roughly half the
> spend — on a real script, narration alone came to `$0.1928` against a `$0.24`
> cap and would have passed, while the true `$0.2848` is correctly refused and
> **nothing is sent**.

### The error taxonomy that keeps it honest

Failures are sorted by what they imply about *billing*, not by HTTP status:

| Class | Meaning |
| --- | --- |
| `RetryableError` | Rejected before generating. Retrying cannot double-charge |
| `FatalError` | Will never succeed as written |
| `UnknownOutcomeError` | Sent, outcome never observed. **Never retried** — it may already have been billed, and a double charge is direct financial loss |

Unknown outcomes are recorded and surfaced for a human rather than guessed at.

---

## 🎛️ Choosing a model

```bash
narrate models
```

| Model | Rate /1k | Ceiling | Continuity | Notes |
| --- | --- | --- | --- | --- |
| `eleven_multilingual_v2` | $0.10 | 10,000 | request stitching | 🏆 the long-form default |
| `eleven_v3` | $0.10 | 5,000 | **none** | audio tags, 🎭 multi-speaker dialogue |
| `eleven_v3_conversational` | $0.05 | 5,000 | **none** | audio tags, half the price of v3 |
| `eleven_flash_v2_5` | $0.05 | 40,000 | request stitching | separate concurrency pool |
| `eleven_flash_v2` | $0.05 | 30,000 | request stitching | English only |

The picker shows only the delivery controls a model **actually honours** — on v3
that is stability alone, because speed, similarity and speaker boost do nothing
there, and a slider that silently has no effect is worse than no slider.

---

## 🗄️ Where your data lives

The database is created on first use and lives **outside the project
directory** — `~/.local/share/narrate/narrate.db`, or `%LOCALAPPDATA%\narrate\`
on Windows. A project tree gets synced, backed up and duplicated, and this is a
WAL-mode SQLite file holding a ledger that reconciliation replays.

```bash
narrate db status    # where it is, and whether that is sensible
narrate db list      # every narrate database, and what is in each
narrate db adopt     # move one into the managed location, verified
```

Generated audio goes to `assets/`, browsable and downloadable per project.
Details and migration in **[docs/INSTALL.md](docs/INSTALL.md#-provisioning-the-database)**.

---

## 🧪 Spending nothing

```bash
NARRATE_PROVIDER=mock just up     # the whole UI, offline
just demo                         # the pipeline end to end
just check                        # 414 Python + 67 frontend tests
```

The offline provider produces **real, playable** audio and reports a
`character-cost` the way the API does, so the ledger, chunker, runner and export
all take the identical code path they take in production — for nothing. Tests
never touch the network.

### ❓ Open questions the docs don't answer

```bash
narrate probe                       # shows what it would cost, sends nothing
narrate probe --live                # ~100 characters, under two cents
narrate probe --live --effects      # what `character-cost` means per-second
narrate probe --live --dialogue     # what a multi-speaker request is billed
```

Findings are written down rather than remembered, in
[docs/probe-results.md](docs/probe-results.md) and
[docs/probe-effects.md](docs/probe-effects.md). Two that changed the code:
`eleven_v3` **rejects `previous_text` outright** (which would have failed every
chunk after the first), and the sound-effects `character-cost` header is **5 per
second, rounded up** — not the 40 credits/second the subscription docs quote.

---

## 📚 Documentation

| | |
| --- | --- |
| 🛠️ **[docs/INSTALL.md](docs/INSTALL.md)** | Setup for macOS, Linux and Windows; database provisioning; troubleshooting |
| 📋 **[docs/FEATURES.md](docs/FEATURES.md)** | Everything supported, read from the code — and a plain list of what is not |
| 🔬 **[docs/probe-results.md](docs/probe-results.md)** | What live probing settled about billing |
| 📐 **[docs/PRD.md](docs/PRD.md)** | The original brief, kept for the reasoning behind the design |

---

## 🗂️ Repo layout

```text
src/narrate/          the pipeline — one module per concern
  config/models.toml    the rate card: rates, limits, capabilities
  db/                   models, migrations, and where the database lives
  provider/             the only modules that touch the network
frontend/src/
  pages/                one file per route
  components/           feature components
  ui/                   shared primitives (Card, DataTable, Markdown, …)
docs/                 INSTALL, FEATURES, the PRD, and probe findings
examples/             scripts to try it on
tests/                414 Python tests
assets/               generated audio (gitignored)
```

Nothing generated lives in the repo. The database, and the per-account model
cache `narrate models --sync` writes, both sit in the data directory beside each
other — `~/.local/share/narrate/` or `%LOCALAPPDATA%\narrate\`. A project
directory is not a data directory, and the ledger is the one file here that
cannot afford a stray copy.

---

## 🤝 Development

```bash
just                # lists every recipe, by group
just check          # lint, typecheck, Python tests, UI tests
just check-all      # adds the production frontend build
```

`just check` skips the UI tests with a notice if `just ui-install` has not been
run, so a fresh clone that has only synced Python still gets a green gate rather
than a confusing failure.

Python 3.12, `uv`, ruff, mypy strict, pytest. React 19 + Vite + strict
TypeScript, with `marked` and DOMPurify the only runtime dependencies beyond
React itself.

---

## 📄 Licence

[MIT](LICENSE) — do what you like with it, no warranty.

---

## 🚧 Not supported

Stated plainly, because a gap you know about is cheaper than one you discover:
voice cloning or design (use ElevenLabs' own UI and consume the `voice_id`),
Postgres (the seam exists, no dialect but SQLite is tested), multi-user,
loudness normalisation, effects mixed into the master (they are overlays by
design), and `.docx`/`.pdf` scripts. Dialogue pricing is undocumented by the
vendor and is declared at the ordinary rate, flagged unverified, until probed.
