# 📦 Publishing: titles, chapters, thumbnails and retention

Reference for the packaging side of the tool. `plan.md` tells you how to **cut**
the episode; `publish.md` tells you how to **upload** it.

The taxonomies here — nine title formulas, twelve thumbnail compositions, seven
psychology principles, the retention table — are transcribed from a
YouTube-automation course document, which is **not redistributed in this repo**.
They are somebody else's framework, not this tool's invention, and they are
written into the code as literal tuples because the compositions exist in that
document only as images.

---

## 🧭 The short version

```bash
narrate publish write 1              # publish.md — no key, no cost
narrate retention 1                  # what this length should hold
narrate publish draft 1 --go         # titles + description + tags
narrate publish brief 1 --go         # thumbnail briefs
narrate publish accept 3             # choose a title
narrate publish choose 1             # choose a brief
```

Everything that spends is behind `--go`, and **dry run is the default**. Chapters
and the retention target cost nothing and need no key at all.

---

## 1️⃣ Chapters

A chapter has a **name** and a **time**, and they come from opposite ends of the
tool. The name is written in the script. The time is measured off the finished
audio.

### Three ways to name one

| Syntax | When to use it |
|---|---|
| `## The Employee Trap` | A heading you were going to write anyway |
| `[CHAPTER: The Employee Trap]` | A section break with no heading, or a chapter name that differs from the heading |
| `[@ 03:00 The Employee Trap]` | You already mark where sections start with anchors — just name them |

All three produce the same thing. `##` and `###` become chapters; `#` is the
episode's own title and is left alone, because a chapter named after the episode
is noise. `####` and deeper are treated as sub-notes — a forty-entry chapter list
is worse than none.

> 💡 A heading whose text contains `]` is never promoted, and says so. Truncating
> it to fit the marker syntax would silently rename the chapter.

### Times come from the audio, never from the script

This is the whole design. An `[@ MM:SS]` anchor is a **target**, and TTS duration
cannot be dialled to a mark. On a real 21-minute episode the drift was this
large:

| Anchor said | Audio landed at | Drift |
|---|---|---|
| 00:00 | 0:00 | — |
| 01:10 | 1:46 | +36s |
| 13:30 | 10:58 | −152s |
| 17:00 | 15:53 | **−224s** |

A chapter list built from the anchors would have been nearly four minutes wrong
by the end. So chapters are read off the timeline, which sums measured `ffprobe`
durations.

### YouTube's three rules

All three, or it shows **none** of your chapters:

1. The first chapter is at exactly `0:00`.
2. There are at least **three** chapters.
3. They are at least **10 seconds** apart.

`narrate publish` checks all three and reports every break, not just the first —
fixing one rule at a time is a bad loop. It also refuses while any chunk is
ungenerated, because a chapter list that disagrees with the video is worse than
no chapter list.

> ⚠️ Chapter markers are **forced chunk boundaries**, so adding one changes how
> the script is split. Re-chunking is refused once anything has been generated
> (`ScriptHasTakes`), which protects the ledger — so add chapters *before* you
> generate. An existing finished episode keeps its chunks, its takes and its
> cost records.

---

## 2️⃣ Titles

### The nine formulas

| Formula | The shape it takes |
|---|---|
| **Superlative** | The best, worst, biggest or first of something |
| **Curiosity Gap** | Names a thing but withholds the answer |
| **Fear or Urgency** | A cost of not knowing, or a window closing |
| **Direct Address** | Speaks to the viewer about themselves |
| **Contrast** | Two things that should not sit together |
| **Emotion** | Leads with a feeling — brutal, beautiful, humiliating |
| **Concise** | Four or five plain words, subject and nothing else |
| **Bracketed** | A plain title plus a qualifier: `(Explained)`, `— The Truth` |
| **Clear Promise** | Exactly what the viewer will be able to do afterwards |

`narrate publish draft` is asked to **spread across** these rather than cluster
on one, and the formula is an `enum` in the schema — so an off-taxonomy answer is
structurally impossible, and the same formula gets the same name every run. That
matters because it lets you see that a channel has published nine superlatives in
a row.

### Length

60 characters is where YouTube truncates a title in most of the places it is
actually read. Longer titles still publish — they just get cut off mid-word in
search. `publish titles` flags them; it does not refuse them.

### Nothing is chosen for you

Every proposed title lands **unchosen**. Accepting one writes through to
`Script.title` and unchooses the rest, so an episode always has exactly one
title, and the candidates stay on record as what was considered and why.

> 🧠 Titles are asked for at a **higher temperature** than anything else here.
> Nine formulas at the default produce six rephrasings of one idea.

---

## 3️⃣ Thumbnails

**No image is generated.** The brief is the deliverable: hand it to a designer,
or paste it into whatever image tool you use. There is no second billable
provider and no second API key.

### The twelve compositions

| | | |
|---|---|---|
| Face First | Two Faces | Face and Object |
| Object First | Action | Perspective |
| Organized Clutter | Colors | Two-panel |
| Three-panel | Text to Amplify | Clipart |

### The seven principles

Curiosity · emotion · clarity · high contrast · **minimal text** · authority ·
mini-story.

### Minimal text is enforced, not requested

At most **five words**, and three is better — it is read at the size of a
fingernail. This is not an instruction in the prompt that the model may ignore:
`overlay_words` is an *array* with `maxItems: 5`, so a six-word answer is not a
reply constrained decoding can produce.

A brief that came back looked like this:

```text
Composition       Face First
Text on image     Your Job Is Debt        (4 words)
Subject           Middle-aged man in a gray suit, eyes wide, gripping a
                  briefcase, lit by harsh orange key light from behind
Contrast          Neon orange backdrop against deep shadows
Leans on          curiosity, emotion, authority, mini-story
```

The overlay text is asked **not** to repeat the title — together they should say
more than either does alone.

---

## 4️⃣ Retention targets

The published guidance is five rows, keyed by video length:

| Length | Good | Great |
|---|---|---|
| 8 min | 45% | 50% |
| 15 min | 40% | 45% |
| 30 min | 35% | 40% |
| 60 min | 30% | 35% |
| 120 min | 25% | 30% |

Stored as a table this would need interpolating between rows and could say
nothing about a 21-minute episode — which is the length people actually make.

**It does not need interpolating, because the table is a formula.** Each
*doubling* of runtime costs five points:

```
good% = 60 − 5 · log₂(minutes)
great% = good% + 5
```

That reproduces all five published rows exactly, and it is continuous. Outside
8–120 minutes the document says nothing, so those answers are marked
`extrapolated` and the output says so rather than presenting a guess with the
same confidence as a published figure.

### A percentage is not actionable; a time is

```text
Runtime        20m 46s
Target AVD     38% good / 43% great
Hold for       7m 53s to reach "good"
               8m 55s to reach "great"
At 38% the average viewer leaves during chunk 5 (6m 03s) — So the smart ones…
```

That last line is the part nothing else can tell you, and it is only answerable
because the timeline is built from measured durations. It points at a specific
paragraph.

> 🚫 **This is not niche research.** narrate can only see your own projects. It
> has no data about other channels, their performance, or what a subniche is
> currently worth, and it does not pretend otherwise.

---

## 5️⃣ Drafting a script

Outline first, then expand section by section.

```bash
narrate write outline -p "My Channel" --title "Why Plane Windows Are Round" \
    --brief-file brief.txt --minutes 12 --beats 6 --go
narrate write beats 2                       # review the budget
narrate write beat 2 3 --minutes 3          # change one
narrate write expand 2 --go                 # prose, one request per section
```

### Why not one request for the whole script

A 25-minute script is about 20,000 characters. One request for it is slow,
exceeds the free tier's per-minute budget, and — the real objection — cannot be
steered: getting section three wrong means paying for the other nine again.

### The beat sheet **is** the script

`write sync` renders the beats into `Script.source_text` and re-chunks, so the
existing pipeline needs no knowledge of beats. Two things fall out of that for
free:

- each heading becomes a `## Heading`, therefore a **chapter** — outlining and
  chaptering turn out to be one feature;
- each `intent` becomes an HTML comment, which is stripped before anything is
  billed — so your notes about a section travel with the script and are **never
  narrated**.

### Timings are measured, not assumed

`words_per_second` reads the project's own takes. On real projects here it
measured 150–184 words per minute, and the outline's budgets get more accurate as
the channel produces episodes. With no history it falls back to a documented
2.5 words/second and says so.

> ⚠️ Expanding is refused once anything has been generated, because re-chunking
> would orphan the takes. **Finish drafting before you generate.**

> 🐌 The free tier allows 8,000 tokens per minute, and a six-beat expansion will
> often hit it. A rate-limited beat is recorded and skipped, and re-running
> `write expand` does only the **empty** beats — so it fills the gaps and charges
> only for what it writes. `--rewrite` re-does beats that already have prose.

---

## 6️⃣ Channel-level defaults

Some packaging belongs to the channel, not the episode:

```bash
narrate project set "My Channel" \
    --default-tags "money,investing" \
    --description-boilerplate "Subscribe for more."
```

Episode tags come first, then the channel's, without repeats. The boilerplate is
appended to the assembled description. Regenerating either per episode would pay
an LLM to retype what never changes.

---

## 💸 What it costs

All of it goes through Groq, billed per token, and all of it is in the ledger as
`kind="copy"`.

| Command | Real measured cost |
|---|---|
| `publish draft` (6 titles + description + tags) | **$0.0012** |
| `publish brief` (3 concepts) | **$0.0011** |
| `write outline` (6 beats) | **$0.0003** |
| `write expand` (12 minutes of prose) | **~$0.003** |
| `publish write`, `retention`, everything else | **$0.00** |

Because it is `provider=groq` / `unit_kind=tokens`, it counts toward the monthly
cap, stays **out** of the re-roll waste ratio (which measures speech only), and
is excluded from reconciliation against ElevenLabs' character count — all without
a migration, because the ledger has recorded units and their kind since the
second revision.

```bash
narrate cost log --kind copy
narrate cost report --script 1        # copywriting appears as its own line
```

---

## 🚧 Not supported

- **Image generation.** The thumbnail brief is the deliverable. If it is ever
  added it attaches as a fourth provider Protocol in `provider/base.py` with its
  own rate card and key — and the accepted brief is already the request payload,
  which is why brief-only was the right place to stop.
- **Niche research.** No data about other channels exists here.
- **Uploading.** narrate produces the pack; you paste it.

---

## 📚 See also

- **[FEATURES.md](FEATURES.md)** — everything the tool does
- **[VOICES.md](VOICES.md)** — clones, slots, and two versions of one episode
- **[INSTALL.md](INSTALL.md)** — setup and database provisioning
