# 🔎 Checking takes, and regenerating the ones that are wrong

A text-to-speech model occasionally renders a take that is **not what it was
sent**. It is not a bug in narrate and it is not a mastering problem: the text
goes to ElevenLabs correct, and the audio comes back missing a word, with an
extra one, or with one swapped for another.

This page is how narrate finds those takes for you, and how you fix them.

---

## 🧭 The short version

```bash
uv sync --extra verify                 # once — the optional speech-to-text
narrate verify --download-model        # once — about 480 MB, free
narrate verify 5                                # check the episode. Free, local, nothing sent
narrate regenerate 5 --flagged --export         # price the fix. Sends nothing
narrate regenerate 5 --flagged --export --go    # fix every flagged chunk, rebuild the episode
```

In the web UI: **Check takes** on the Script page, then **Fix all flagged…** —
or **Regenerate…** on any one chunk. The price is always shown before anything
is sent.

---

## 1️⃣ What goes wrong, from a real episode

A 21-minute episode rendered on `eleven_v3_conversational` had five defects.
None was in the script sent to the API:

| At | What happened |
|---|---|
| 4:16 | Said "**Our** problem isn't the job" — the script says "**The**" |
| 6:29 | An unscripted "**God,**" before "The modern S has a Stripe dashboard" |
| 7:26 | "…the one thing that cannot scale. **Them.**" — *Them* reduced to 200ms of noise |
| 10:32 | "instrument" spoken as "instruments" |
| 15:20 | An unscripted "**Good**" before "Here's the honest bit nobody says" |

The one that was noticed, *Them*, was found by ear. The other four were not.
That is the case for checking every take automatically: a listener catches
what they happen to be listening for.

> 💡 **A smarter "coordinator" model would not have helped.** Nothing in
> narrate's generation path involves an LLM — the chunk text goes straight to
> ElevenLabs, and the defects happen inside their speech model. ElevenLabs'
> own long-form product, Studio, handles it the same way narrate now does: it
> "automatically checks the output for … missing or additional words" and
> regenerates.

---

## 2️⃣ `narrate verify` — finding them

```bash
narrate verify 5
```

```text
┏━━━━━━━┳━━━━━━┳━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ chunk ┃ take ┃ at      ┃ check   ┃ what was found                            ┃
┡━━━━━━━╇━━━━━━╇━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 3     │ 1    │ 4:16.8  │ review  │ said "our" where the script says "the"    │
│ 5     │ 1    │ 6:29.9  │ suspect │ extra word "god" — not in the script      │
│       │      │         │         │ heard again on a second listen            │
│ 5     │ 1    │ 7:26.0  │ suspect │ "them" not spoken (0.94s gap)             │
│       │      │         │         │ a 320ms burst at 83.52s, walled in by     │
│       │      │         │         │ silence — the word was reduced to a noise │
│ 8     │ 1    │ 15:20.8 │ suspect │ extra word "good" — not in the script     │
└───────┴──────┴─────────┴─────────┴───────────────────────────────────────────┘
10 take(s) checked: 2 suspect, 1 to review, 7 with no issues found.
```

Every take is transcribed **on your machine** with faster-whisper and compared,
word by word, with what it was asked to say. Nothing is uploaded and nothing is
billed. A 21-minute episode takes about two minutes on an Apple M5 Pro.

| Verdict | Meaning |
|---|---|
| **suspect** | A word missing, a word that was never in the script, or a word reduced to a noise. Listen, then regenerate |
| **review** | Probably wrong — a swapped word, say. Worth thirty seconds of listening |
| **no issues found** | The checks found nothing. **Not** a guarantee — see below |
| **unverified** | Never checked |

`narrate verify` reports the state of the whole episode every time, but only
**re-checks** takes it has not seen with the current settings — so running it
again after a regeneration costs only the new take's time. `--recheck` listens
to everything again. It exits `3` when a take in the cut is suspect, so it can
gate a script.

### Why you can trust a "suspect"

A naive comparison of that episode against its transcript raised **28**
suspects. **23** were the transcriber's spelling, not the narrator's mistake —
`$10 million` against *ten million dollars*, *favour* against *favor*,
*Cashflow* against *cash flow*. A detector that cries wolf that often teaches
you to ignore it, so most of the work is in *not* reporting things:

1. **Both sides are normalised** with Whisper's own English normaliser, so
   numbers, spellings and contractions stop differing.
2. **A scored alignment**, not a plain diff — a plain diff folded the extra
   "Good" into its neighbour and hid it.
3. **An uncertain extra word gets a second listen.** The few seconds around it
   are transcribed again; a word the narrator really said is there again, and a
   transcriber phantom is not. That step removed every phantom in the episode
   and kept both unscripted words.
4. **The waveform is a second witness.** A short loud burst walled in by
   digital silence is how *Them* was garbled. On its own a burst is only worth
   a listen; it becomes suspect when the transcript agrees.

On that episode the result is exactly the five real defects above and no false
alarms. On defects deliberately spliced into clean takes it caught six of
seven.

### What it cannot see

Stated plainly, so "no issues found" is read correctly:

- **A word clipped short but still recognisable.** The transcriber hears it as
  the word, so every check passes it.
- **Plural and tense changes.** "instrument" spoken as "instruments" looks
  exactly like the transcriber's own errors, so it is kept as information only.
- **Delivery.** Emphasis, pace, a mispronounced name the transcriber still
  recognises. Only listening judges those.

That is why a clean take is shown as *no issues found* and never as a tick.

---

## 3️⃣ `narrate regenerate` — fixing them

```bash
narrate regenerate 5 --chunk 5,8                     # dry run: the price, nothing sent
narrate regenerate 5 --chunk 5,8 --go                # regenerate, after confirming
narrate regenerate 5 --flagged --export --go         # every flagged chunk, then the episode
```

`--flagged` takes every chunk whose take in the cut is *suspect* or *to review*
(`--suspect` takes the suspect ones only). `--export` rebuilds the episode at
the end if the cut changed, so the audio you download is the fixed one; the web
UI's **Rebuild the episode afterwards** does the same.

```text
┏━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━┓
┃ chunk ┃ in the cut ┃ check   ┃ chars ┃ price per try ┃
┡━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━┩
│ 5     │ take 1     │ suspect │ 2,200 │ $0.1100       │
│ 8     │ take 1     │ suspect │ 1,458 │ $0.0729       │
└───────┴────────────┴─────────┴───────┴───────────────┘
╭──────────────── Regenerate (nothing sent) ─────────────────╮
│ Would regenerate 2 chunk(s): up to $0.5487 at list price   │
│ (3 tries each at most).                                    │
│ Every existing take is kept; each new take is checked.     │
╰────────────────────────────────────────────────────────────╯
```

### What happens to the old take

**Nothing is ever deleted.** Every take stays on disk and in the ledger, so a
regeneration that turned out worse costs only its own price.

Whether the new take goes into the cut is decided by the check:

| Option | The cut moves to the new take… |
|---|---|
| *(default)* | only if it checks **strictly better** — a flagged take replaced by a clean one |
| `--use-new` | unless it checks **worse** |
| `--keep-cut` | never — you choose after listening, with `narrate cut set`. The one exception: a cut whose audio file is missing has nothing to play, so the new take is used, and you are told |

The default is deliberate. A regeneration is a fresh sample and can introduce a
new defect as easily as fix the old one. Only a take whose words were heard by
speech-to-text can count as better: without the verify extra, the waveform
check alone cannot hear a missing or an extra word, so the cut stays put and
the choice is yours. And if you regenerated a take you
simply did not *like*, both takes check clean — the check cannot tell which
delivery is better, so the choice stays with you.

### Until it is clean

A regeneration keeps going while the new take is still flagged — *suspect* or
*to review* — up to three tries per chunk, and stops at the first clean one.
**Each try is a separate, billed request**; the price shown is for all of
them, and `--attempts 1` or `--max-spend` bounds it lower.

The tries are not all the same. The first is a plain retry, since most defects
are bad luck. Once a plain try has come back flagged — in this run, or twice
before it — the next is made with the **steadiest delivery** (stability 1.0).
ElevenLabs documents v3's most expressive setting as *"prone to
hallucinations"* and its steadiest as *"highly stable … consistent"*, at the
cost that it *"reduces responsiveness to directional prompts"*: invented words
become less likely, and tags like `[urgent]` land more softly. It applies to
that take only; the chunk's own settings are not changed.

When a chunk is **still** flagged after its tries, the same problem returning
is a sign the text itself provokes it. The report says so, and the fix is then
one of yours: reword the line (below), or cut the moment in the edit — the
finding gives its time.

On the real episode, both stubborn defects sat at a **paragraph break right
after a punchline**, under `[fast-paced][urgent]` — the model filled the pause
with a reaction of its own:

| Chunk | The break | What came back | What fixed it |
|---|---|---|---|
| 5 | *"…more seductive than it's ever been."* ¶ *"The modern S…"* | *"God,"* in two plain takes and one steady | the second steady take |
| 3 | *"…selling you a course."* ¶ *"The problem isn't the job."* | *"Our"* for *"The"*, then *"work"*, *"great"*, *"great"* | joining the two paragraphs — the same words, one fewer break — clean on the first try |

Joining paragraphs changes no word, so it is the first reword to try:
`--text` with the chunk's own words and that one blank line removed.

### Changing the words first

Sometimes the more reliable fix is to reword the line rather than roll the dice
again. *"…the one thing that cannot scale. Them."* can become *"…the one thing
that cannot scale: them."*:

```bash
narrate regenerate 5 --chunk 5 --text "…the one thing that cannot scale: them. …" --go
```

In the UI, tick **Change the words first**.

> ⚠️ Rewording is only allowed on the **eleven_v3 family**, which sends no
> context between chunks. On a stitched model like `multilingual_v2`, each
> request carries its neighbours' text, so changing one chunk changes what the
> next run would send for the chunks either side — and it would generate, and
> bill, them again. It is refused there, with the price, before you confirm.

When the words change, the new take goes into the cut unless it checks worse:
the old take now says something the script no longer does.

The new words are kept only once a take has been made with them. If nothing
comes back — the spending limit, the monthly cap, a failed request, one that
was never answered — the old words are put back, so the chunk never claims to
say something no take says, and the next ordinary run does not send the new
words a second time.

### When it refuses

- **A request whose outcome is unknown** — sent, but never answered. It may
  already have been billed, so regenerating on top of it could pay twice for
  one line. Run `narrate cost reconcile` first, or pass `--accept-unknown` if
  you are sure.
- **The monthly cap.** Never overridden from here.

---

## 4️⃣ Catching it as you go

```bash
narrate generate 5 --go --verify
```

Checks the new takes as soon as they are made and names anything flagged, with
the command to fix it. Off by default, because it adds about ten seconds per
take.

`narrate export` warns when a take in the cut is flagged, and `plan.md` lists
them under **Takes to check** with episode times. For a script or a release
step, `narrate export --require-verified` refuses instead — unless every take it
would export was heard by speech-to-text and none is suspect. A *review* it
heard does not block it: listening decides those.

---

## 5️⃣ Before you spend — the preflight hint

`narrate script add` and `narrate chunk review` point out two patterns:

- **A one-word sentence ending a paragraph after a long sentence** — the exact
  shape of *"…cannot scale. Them."*. It is a heuristic from that one case, and
  deliberately narrow: of 38 one-word sentences in that script it names two.
  If the line matters, join it to the sentence before.
- **[bracketed] text on a model that does not treat it as an audio tag.** Only
  the eleven_v3 family reads `[fast-paced]` as a direction; every other model
  reads it aloud, and bills for it.

---

## 6️⃣ Which model

For narration, prefer **`eleven_v3`** — it is what a new project gets unless
you choose otherwise. An existing project keeps its model. Changing it with
`narrate project set <project> --model eleven_v3` changes every chunk's request,
so a script that already has takes would be generated — and billed — again in
full on its next run: switch before an episode, not after one.

`eleven_v3_conversational` costs half as
much, but ElevenLabs documents it as *"an ultra-low-latency version of Eleven
v3, optimized for live, back-and-forth dialogue"* — for agents and assistants.
Their model guide recommends `eleven_v3` or `eleven_multilingual_v2` for
"audiobooks & video narration".

`multilingual_v2` is described as "most stable on long-form generations", but
it does **not** honour audio tags, so a script written with `[fast-paced]` and
friends will have them read aloud — the preflight hint above says so.

No model is documented as never doing this, so keep checking whichever you
choose.

---

## 🧰 Installing the extra

It is optional so the core install stays light: about 55 MB of wheels on Apple
Silicon, up to about 130 MB on Linux (no torch), plus a speech model downloaded
once, only when you ask.

| | |
|---|---|
| **uv** | `uv sync --extra verify` — or `just sync-verify` |
| **pip** | `pip install --prefer-binary -e '.[verify]'`, from the repository — narrate is not on PyPI |
| **Platforms** | macOS 14+ on Apple Silicon (13+ on Intel), Linux with glibc 2.28+ on x86-64 or ARM64, Windows on x86-64. Not Alpine/musl Linux, not Windows on ARM64 — see [INSTALL.md](INSTALL.md) |
| **Model** | `narrate verify --download-model` — about 480 MB, into narrate's data directory |
| **Offline** | Copy a model directory across and point `NARRATE_STT_MODEL_DIR` at it |
| **Check** | `narrate doctor` shows a *speech-to-text* row |

> ⚠️ Plain `uv sync` (and `just sync`) removes extras it was not asked for. Once
> verification is installed, use `just sync-verify`.

Without the extra, `narrate verify --energy-only` runs the waveform check
alone. It catches a garbled word like *Them*, but not an extra or a swapped
word, so it can raise things to listen to and can never clear a take.

---

## 📚 See also

- **[FEATURES.md](FEATURES.md)** — everything the tool does
- **[VOICES.md](VOICES.md)** — clones, and two performances of one episode
- **[INSTALL.md](INSTALL.md)** — setup on macOS, Linux and Windows
