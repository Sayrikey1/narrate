# narrate — product description

A complete description of what the product does, page by page and control by
control, written for the design team taking on the redesign. It describes the
app as it is today (October 2026). Where something is awkward or broken, that
is called out in §12, separately from the description, so the two are not
mixed up.

---

## Contents

1. [What narrate is](#1-what-narrate-is)
2. [Who uses it](#2-who-uses-it)
3. [Principles the redesign must keep](#3-principles-the-redesign-must-keep)
4. [Vocabulary](#4-vocabulary)
5. [The end-to-end workflow](#5-the-end-to-end-workflow)
6. [Navigation and structure](#6-navigation-and-structure)
7. [The app shell](#7-the-app-shell)
8. [Pages](#8-pages)
9. [Shared components](#9-shared-components)
10. [The current visual system](#10-the-current-visual-system)
11. [States, feedback and copy](#11-states-feedback-and-copy)
12. [Known friction: where the redesign can help most](#12-known-friction-where-the-redesign-can-help-most)
13. [Features that exist only on the command line](#13-features-that-exist-only-on-the-command-line)
14. [Appendix: realistic content and where things live in code](#14-appendix-realistic-content-and-where-things-live-in-code)

---

## 1. What narrate is

narrate turns a long written script (typically a 20–30 minute YouTube episode)
into a finished narration track, using ElevenLabs text-to-speech voices. It
splits the script into pieces the speech service accepts, generates each one,
lets you listen, check and re-do the pieces that came out wrong, and stitches
the result into one audio file. It also places sound effects, writes an editing
plan for the video editor, and prepares the YouTube upload details (chapters,
title, thumbnail brief).

Throughout, every cent is tracked. The product's founding promise is that you
can answer "what did episode 14 cost me?" exactly, and that **you see the price
before you spend it**.

It is a **local, single-user tool**: a web app running on the producer's own
computer, alongside a command-line version (`narrate …`) that can do
everything the web app does and more. There are no accounts, logins, teams or
sharing.

**One-line pitch:** paste a script, pick a voice, get a stitched narration
track — with every character and dollar accounted for.

---

## 2. Who uses it

**Primary: the producer.** Runs a narrated YouTube channel (the current user
makes a finance-explainer series). Writes the
script, picks the voice, generates, fixes, exports, and hands the audio to an
editor or edits it themselves. Technically comfortable, but wants the fifteenth
episode to feel push-button. Pays per use, so an indecisive session shows up
on the bill.

**Secondary: a contracted editor.** Given the project, they re-do a line that
reads badly and re-export, without needing to understand the cost model or
handle API keys.

### What they come to the app to do

| Job | How often | Where today |
| --- | --- | --- |
| Start a new episode from a finished script | Every episode | Projects → Script |
| See what it will cost before generating | Every run | Script (Dry run) |
| Generate the narration | Every episode | Script |
| Listen to the whole episode as it will sound | Every session | Transport bar, Script |
| Find and fix lines that came out wrong | Every episode | Script (Check takes, Regenerate) |
| Export audio files for the edit | Every episode | Script, Media |
| Hand the editor a guide to the cut | Every episode | Plan, Media |
| Prepare YouTube chapters, title, thumbnail | Every episode | Publish |
| Check spending against a monthly limit | Now and then | Costs, Script |
| Set up a new series (voice, model, budget) | Rarely | Projects |
| Cast several speakers for dialogue | Rarely | Cast |

---

## 3. Principles the redesign must keep

These are behaviours the product is built around. A redesign can change how
they look, but it should not lose them.

1. **Price before spend.** Anything that costs money shows the price first.
   The button that spends names the amount ("Regenerate for up to $0.27").
   Changing any option throws the old price away, so the figure on the button
   is always for exactly what will be sent. The one exception today is the
   main Generate button (see §12).
2. **Nothing paid for is destroyed.** Every take is kept, even after a better
   one replaces it. Deleting an episode or project hides it; what it cost stays
   counted, and it can be restored. Files are deleted only when explicitly
   asked for.
3. **Every figure is attributed.** Spend is shown in dollars, and in ElevenLabs
   credits where known, and always attached to the project, episode, chunk or
   take that bought it.
4. **Say why, and what to do next.** Empty states say what would fill them.
   Warnings explain the consequence. Errors carry the server's own explanation.
5. **Listening is how work is checked.** Players are everywhere. Only one thing
   is ever audible at a time: starting one player stops any other, including
   the episode playback.
6. **Honest labels.** The automatic check says "no issues found", never shows
   a tick, because a check can miss things. Timings that depend on missing
   audio are labelled "provisional".
7. **Keyboard and screen-reader support.** Every control is reachable by
   keyboard and has a proper label (details in §10.6).

---

## 4. Vocabulary

The interface uses these terms. Several appear in labels, so they need to stay
consistent in the redesign.

| Term | Meaning |
| --- | --- |
| **Project** | A series or channel. Holds the default voice, model, delivery settings and an optional monthly spending cap. Every script belongs to one. |
| **Script / Episode** | One episode's text, and everything generated from it. The UI uses both words (nav says "Script"; delete and replace say "episode"). |
| **Chunk** | A piece of the script small enough to send in one request (up to 5,000 characters on the default model). An episode usually has 5–10. Split automatically at paragraph, then sentence, then clause boundaries. |
| **Take** | One generated audio attempt for a chunk. A chunk can have many takes. Each records its cost, duration and voice. |
| **Cut** | Which take is chosen for each chunk. The cut is what gets exported. A new take goes into the cut automatically unless a choice was made by hand. |
| **Run** | One generation job. Runs happen in the background and stream progress line by line. |
| **Dry run** | Works out what a run would cost without sending anything. |
| **Effect slot / cue** | A place in the episode where a sound effect belongs, with a description ("heavy wooden door creaking open") and a length. |
| **Planned** | An effect slot that has no audio yet. Shown in amber, dashed. A normal state, not an error. |
| **Accepted** | Effect slots suggested by the AI need accepting before anything is generated. |
| **Effect library** | Generated effects are reused: the same cue placed four times is generated and paid for once. |
| **Version / variant** | The same episode performed in a different voice. Made by generating again with another voice; each version exports separately. |
| **Export** | Stitching the cut into finished files. |
| **Master** | The full stitched episode file, in each chosen format (wav, mp3, m4a, flac, opus). |
| **_fx master** | The same master with the sound effects mixed in (`Episode_fx.mp3`). The plain master is kept for editors who want effects on their own track. |
| **Pieces** | Every take and effect also exported as its own file, named by its position on the timeline (`Project_00-01-14-200_00-02-31-900.mp3`), so an editor can drop them straight onto a timeline. |
| **Plan (`plan.md`)** | The editing plan: a document listing what plays when, written with every export. |
| **Publish pack (`publish.md`)** | The YouTube upload details: chapters, title, description, tags, thumbnail brief, retention target. |
| **Check / verify** | Transcribes each take on this computer and compares it word by word with the script. Free, and nothing is uploaded. |
| **Finding** | One problem the check found ("'magazine' not spoken", "extra word 'roo'"), with its time in the take. |
| **Verdict** | A take's check result: *flagged* (suspect), *review*, *no issues found*, *check failed*, or not checked. |
| **Anchor / drift** | `[@ 02:15]` in a script asks for a line to start at 2:15. Speech length can't be controlled exactly, so the difference is reported as drift. |
| **Waste** | Money spent on takes that never made it into the cut. A high waste ratio means the direction isn't working. |
| **Monthly cap** | A spending limit per project. Warns at 80%, blocks generation at 100%. |
| **Credits** | ElevenLabs' own billing unit, shown next to dollars. |
| **Model** | Which ElevenLabs speech engine is used. Models differ in price, maximum chunk size, and whether they carry the voice smoothly across chunk boundaries ("continuity"). |
| **Audio tags** | Bracketed directions in the script (`[whispers]`) that some models act on. |
| **Cast** | The named speakers in a project and the voice each one uses. |
| **Dialogue** | Several speakers rendered in one request, so they react to each other. Only on one model, and only from the command line today. |

### Script markers

Writers type these into the script. They are stripped out before anything is
sent, so they are never spoken or paid for. They show up in the UI as helper
text, placeholders, empty states and templates.

| Marker | What it does |
| --- | --- |
| `[SFX: wind howling, 4s]` | Places a sound effect here |
| `[SFX: soft rain, 30s, loop]` | Places a looping background sound under the passage |
| `[@ 02:15]` | Asks for this point to land at 2:15 (reported as drift, never forced) |
| `## Heading`, `[CHAPTER: Name]`, `[@ 03:00 Name]` | Starts a named YouTube chapter |
| `Morag: her line` | A line spoken by Morag, but only if Morag is cast |
| `[CAST] Morag = <voice id>` | Casts a speaker for this script only |
| `[VOICE: Morag]` | Switches speaker in the middle of a paragraph |
| `<!-- a note -->` | A note to self; never narrated |

---

## 5. The end-to-end workflow

```text
 Projects            Script page                                    Script tabs           Costs
 ────────            ───────────                                    ───────────           ─────
 1 Create project →  3 Dry run (price) → 4 Generate → 5 Listen  →   9  Media (download)   Spend,
   (voice, model,                         │                          10 Plan (edit guide)  waste,
    budget)                               ↓                          11 Publish (YouTube)  restore
 2 Add script        6 Check takes → 7 Fix flagged → 8 Export
   (upload/paste)        (free)        (priced first)  (formats)
```

1. **Create a project** with a name, model, voice, delivery settings and an
   optional monthly cap. *(Projects page)*
2. **Add a script** by dropping a file, browsing, or pasting text. The app
   splits it into chunks and opens the new script. *(Projects page)*
3. **Dry run** to see the cost of narration and effects, without sending
   anything. *(Script page)*
4. **Generate.** Narration and accepted effects are produced in one run.
   Progress streams into a log line by line. *(Script page)*
5. **Listen** to the whole episode on the transport bar, with effects layered
   at their positions, or play individual takes. *(Anywhere / Script page)*
6. **Check takes.** Transcribes every take and flags missing, extra or
   garbled words. Free. *(Script page)*
7. **Fix what's flagged.** Regenerate one chunk or all flagged chunks. Price
   first, then confirm. By default it keeps trying up to three times, moves
   the better take into the cut and rebuilds the export. *(Script page)*
8. **Export** the cut in the chosen formats. *(Script page)*
9. **Download** the masters, pieces, or a zip of the whole export, and read
   the documents. *(Media page)*
10. **Read the editing plan** and check timings, drift and missing cues.
    *(Plan page)*
11. **Prepare the upload**: chapters, title, thumbnail brief, retention
    target. *(Publish page)*
12. **Track spend** across everything, and restore anything deleted.
    *(Costs page)*

A typical episode: one Dry run, one Generate (a minute or two for 8 chunks),
one Check takes (a couple of minutes), one or two rounds of fixing, one
Export, then Media and Publish.

---

## 6. Navigation and structure

### Pages

| Address | Page | Scope |
| --- | --- | --- |
| `/` | Projects | All projects; scripts of the selected project |
| `/cast` | Cast | Selected project |
| `/script/:id` | Script (the workbench) | One script |
| `/script/:id/effects` | Effects | One script |
| `/script/:id/media` | Media | The script's **whole project** (despite the address) |
| `/script/:id/plan` | Editing plan | One script |
| `/script/:id/publish` | Publish pack | One script |
| `/costs` | Costs | Everything |
| anything else | "No such page" + "Back to projects →" | — |

### How the navigation behaves

- The left rail always shows **Projects**, **Cast** and **Costs**.
- **Script, Effects, Media, Plan, Publish** appear in the rail only while a
  script is open (the address contains a script id), between Cast and Costs.
  On `/`, `/cast` and `/costs` they are hidden.
- The Effects link shows a count when the script has effect slots:
  "Effects (3)".
- There is a **selected project**, chosen on the Projects page. Opening a
  script selects its project automatically. The selected project scopes the
  scripts list, the Cast page and the Media page, and is shown at the bottom of
  the rail.
- There is no breadcrumb and no script switcher. To change script you go back
  to Projects and pick another.
- Links are real links: middle-click and "open in new tab" work, and a hard
  refresh keeps you on the same page.

---

## 7. The app shell

```text
┌────────────┬────────────────────────────────────────────────────────────────┐
│ narrate  ◐ │ ▶  ━━━━━━━━━━━●──────────────  3:12 / 21:40  [~Narration][✦Effects] 011 │  ← transport bar
│            ├────────────────────────────────────────────────────────────────┤
│ Projects   │  Page title                                    [Actions…]       │
│ Cast       │  faint subtitle                                                 │
│ ~ Script   │                                                                 │
│ ✦ Effects 3│  ┌─ Main column ───────────────────────┐ ┌─ Aside (330px) ──┐   │
│ Media      │  │ CARD TITLE              [actions]   │ │ CARD TITLE       │   │
│ Plan       │  │ …                                   │ │ …                │   │
│ Publish    │  └─────────────────────────────────────┘ └──────────────────┘   │
│ Costs      │                                                                 │
│            │                                                                 │
│ PROJECT    │                                                                 │
│ HINDSIGHT  │                                                                 │
│ v3         │                                                                 │
└────────────┴────────────────────────────────────────────────────────────────┘
```

### 7.1 Left rail (208px)

- **Brand**: the word "narrate".
- **Theme toggle**: an icon button next to the brand. Each click cycles
  *follow the system → light → dark → follow the system*. The icon shows the
  current state (half-filled circle, sun, moon). The choice is remembered on
  this computer. Adding `?theme=light` or `?theme=dark` to the address forces
  a theme without changing the saved choice (used for screenshots).
- **Navigation links**: text links, the current page highlighted. Only Script
  (waveform icon) and Effects (sparkle icon) have icons; the rest are text
  only.
- **Footer**: a small uppercase "PROJECT" label, the selected project's name,
  and its model in monospace with the "eleven_" prefix removed (e.g. "v3").
  Shown only when a project is selected.

### 7.2 Transport bar (episode playback)

A full-width bar across the top of the content area that plays the **whole
episode** as it will sound.

- **Appears only** when the open script has generated audio. Hidden
  otherwise.
- **Play/Pause** button (32px, round). The space bar also toggles it, except
  while typing in a field.
- **Scrub bar**: click anywhere to jump there. Shows progress fill and a
  playhead dot.
- **Time**: "3:12 / 21:40".
- **Lane toggles**: two chips, "Narration" (waveform icon) and "Effects"
  (sparkle icon). Clicking one mutes or unmutes that layer, so the narration
  can be checked on its own. "On" chips are highlighted.
- **Script title** at the far right, faint, truncated at 220px.
- Narration takes play one after another with the same gaps the export
  inserts; effects play **on top**, starting at their own positions.
- **Playback continues** when you move between pages.
- Pressing play on any individual player pauses the transport, and starting
  the transport stops any individual player.
- Clicking a timeline block, a lane, or a row in the Plan document moves the
  transport's playhead there.

### 7.3 Page frame

- **Page header**: page title (26px), a faint subtitle with context, and page
  actions aligned right.
- **Cards** (panels): a surface with a small-caps heading and optional
  actions on the right of the heading.
- **Two-column layout**: a flexible main column and a 330px aside. Below
  about 900px wide, the aside stacks under the main column.
- If a script's data fails to load, a red notice appears at the top of every
  script page: "Could not load this script" with the error and a "Try again"
  button.

### 7.4 Narrow windows (900px and below)

- The rail becomes a horizontal bar across the top that scrolls sideways; the
  project footer is hidden.
- The transport keeps play, scrub and time; the lane toggles and title are
  hidden.
- Page padding shrinks; tables scroll horizontally inside their cards.
- There is no dedicated mobile design. It works, but it was never designed
  for phones.

---

## 8. Pages

### 8.1 Projects (`/`): the landing page

**Purpose:** pick or create a project, add a script, open a script.

**Header:** "Projects". Subtitle is "{project} — 8 scripts" when a project is
selected, otherwise "a project holds a voice, a model and a spending cap". No
header actions.

#### Card: Projects (full width)

A compact table, one row per project:

| Column | Content |
| --- | --- |
| Name | The project name **as a button**. The selected project's button is filled blue and marked as pressed. Clicking selects that project (does not navigate). |
| Model | Model id, monospace, "eleven_" removed (e.g. `v3`, `multilingual_v2`). |
| Voice | The first 12 characters of the voice's id, monospace (not the voice's name). If no voice is set, an amber "unset" badge. |
| Spend | Total spend in dollars, 4 decimals. |
| Waste | Waste percentage. |
| Cap | Percentage of the monthly cap used, as a badge, amber at 80% or more. "—" if no cap. |

**Empty state:** "No projects yet — A project holds a voice, a model and a
spending cap, and every script belongs to one." with a "+ New project" button.

**Action row under the table** (when there are projects). Only one of the
three panels can be open at a time; each button changes its label while its
panel is open:

- **+ New project** (blue) → becomes **Cancel**.
- **Voice & delivery — {project}** → becomes **Done**.
- **Delete project — {project}…** (ghost, red on hover) → becomes **Cancel**.

Visiting `/#new-project` opens the new-project form directly.

##### New project form (inline panel)

| Field | Detail |
| --- | --- |
| Name | Text. Placeholder "Ep14". Required. |
| Model | Dropdown. Each option shows name, price and chunk limit: "Eleven v3 — $0.10/1k, 5,000 chars (default)". The default model is preselected. |
| Model notices | Shown under the dropdown depending on the model: an amber warning if the model can't carry the voice across chunks ("seams between chunks are unavoidable"); a green note if the model can do dialogue ("…add the script from the command line with `narrate script add … --dialogue`"); and any note the model carries. |
| Voice | The voice picker (below). |
| Delivery | Sliders for the settings this model supports (below). |
| Monthly cap (USD) | Number, step 0.01. Placeholder "no cap". Hint: "Warns at 80%, blocks at 100%. Counts every charge this month, not just this run." |
| **Create project** | Blue. Disabled until name and model are set. Shows "Saving…" while working. |

##### Voice & delivery form (inline panel)

The same form for the selected project, without Name and Monthly cap (the cap
can't be changed here after creation). Button: **Save**. If the model is
changed, an amber warning explains that chunks already generated on the old
model would be generated and billed again on their next run, and suggests
adding a new script on the new model instead. After saving, a green notice:
"Saved." or "Saved. Eleven v3 does not honour speed, similarity — dropped."

##### Voice picker

- A filter box: "Filter by name or accent…". Matches the voice name and its
  labels.
- A scrollable list of every voice on the ElevenLabs account. Each row has:
  - The voice **name** (bold), and up to three labels (accent, gender, age,
    use case) in small faint text on the right.
  - An "offline stand-in" tag for practice voices that don't produce real
    audio.
  - A **"Hear it"** button to the right of the row. States: *Hear it* →
    *Loading…* → *Playing* (blue) → back to *Hear it*; or *No sample*
    (greyed) if the sample can't be played. Samples load only when clicked.
    Voices with no sample show "no sample" text instead.
- Clicking a row **selects** it (tinted blue, with a marker). Hearing and
  selecting are deliberately separate actions: you can hear a voice without
  choosing it.
- Keyboard: the list is one tab stop; arrow keys move and select, Home/End
  jump to the ends.
- States: "Loading voices…"; "No voices available. Add `ELEVEN_API` to
  `.env`." when there's no key; "Nothing matches that." when the filter
  matches nothing; a red error if the list fails.
- If an offline stand-in is selected: amber note "This is an offline
  stand-in, not a real voice — it will not produce audio from the provider."

##### Delivery settings

Labelled "Delivery — {model name}". Only the settings the chosen model
actually uses are shown:

| Control | Range | Default |
| --- | --- | --- |
| Stability slider | 0–1, steps of 0.05 | 0.5 |
| Similarity slider | 0–1 | 0.75 |
| Style slider | 0–1 | 0 |
| Speed slider | 0.7–1.2 | 1 |
| Speaker boost checkbox | on/off | on |

Each slider shows its current value in monospace on the right. When some
settings are hidden, a note explains: "Eleven v3 honours only stability.
Pacing and delivery are directed with audio tags instead." On the default
model (v3), only Stability appears.

##### Delete project panel

The shared delete panel (see §8.2, Delete…), worded for a project: "Deletes
the project **HINDSIGHT**, with 12 episode(s) — 96 take(s)."

#### Card: Scripts (main column)

Scripts in the selected project:

| Column | Content |
| --- | --- |
| Title | Link to the script page (bold, turns blue on hover). |
| Chunks | Number of chunks. |
| Characters | Character count with thousands separators. |
| (actions) | An **Open** button (does the same as the title link). |

**Empty states:** "No scripts in this project — Add one on the right — drop a
.txt or .md file, or paste the text." or, with no projects at all, "Nothing
here yet — Create a project first, then add a script to it."

There is no status column: you can't tell from this list which episodes are
generated, incomplete, checked or exported.

#### Card: Add a script (aside)

- **Project**: a dropdown showing the selected project, **always disabled**
  (tooltip: "Scripts are added to the selected project"). You change it by
  selecting a project in the table above.
- **Title**: optional text field ("optional — the filename is used
  otherwise"). Placeholder "Episode 14".
- **Drop zone**: an upload icon, "Drop a script here", and ".txt · .md ·
  .markdown — or click to browse". Highlights blue while a file is dragged
  over it. Shows "Reading…" while uploading. Clicking opens the file browser.
- **"or paste the text instead"** (small ghost button) swaps the drop zone
  for a large text box. Its placeholder shows the marker syntax ("Paste the
  script. [SFX: wind howling, 4s] marks an effect. [@ 02:15] marks a target
  time."). Buttons: **Add script** (blue, disabled while empty) and "upload a
  file instead".
- **Result**: on success, a green line "Added — 8 chunks · 2 effect slots"
  (plus any warnings), then the app **opens the new script**. On failure, a
  red line with the server's reason (wrong file type, too large, not valid
  text).
- **Templates**: "Not sure of the format? Start from a template:" followed by
  download links for the starter scripts (single-voice and multi-voice), and
  the note "Annotated throughout, and every note is stripped before anything
  is sent — so it generates as-is."
- **Marker help** at the bottom: "`[SFX: description, 4s]` places an effect;
  `[@ 02:15]` marks a target time. Both are stripped before anything is sent,
  so they are never spoken or billed."
- With no projects: "No projects yet — Create one above, then add a script to
  it."

#### Card: Spend (aside)

One line per project with its total spend, and a link "Full breakdown →" to
the Costs page.

---

### 8.2 Script (`/script/:id`): the workbench

**Purpose:** everything that happens to an episode: generate, listen, check,
fix, choose takes, export. The most important and most complex page.

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ 011                         [Dry run] [✦ Generate] [⤓ Export] Replace… Delete… │
│ HINDSIGHT · 8 chunks · 15,545 chars                                           │
├──────────────────────────────────────────────┬───────────────────────────────┤
│ ┌ Track view ──────────────────────────────┐ │ ┌ THIS RUN ─────────────────┐ │
│ │ 011                          21:40 · $1.2│ │ │ ☑ Include effect cues     │ │
│ │ 0:00   2:00   4:00   6:00 …              │ │ │ ⓘ One Generate covers…    │ │
│ │ Effects ▕ ▓   ▓▓      ┆┆┆      ▓        ▏│ │ │ EXPORT FORMATS            │ │
│ │ Narration▕1 ███ 2 ████ 3 ███ 4 ████ …  ▏│ │ │ [WAV][MP3][M4A][FLAC][Opus]│ │
│ │ ■ narration ■ effect ┆ planned          │ │ │ ┌ log ───────────────────┐ │ │
│ └──────────────────────────────────────────┘ │ │ │ chunk 2: ok (515 chars)│ │ │
│ ┌ CHUNKS AND TAKES   [Check takes][show table]│ │ └────────────────────────┘ │ │
│ │ ⚠ 5 take(s) in the cut are flagged [Fix all…]│ │ ✓ Exported wav, mp3 …      │ │
│ │ # Chars Opening            Takes            │ └───────────────────────────┘ │
│ │ 1 2,345 "In 2003, a…"      [✓take 1][no issues] ▶━━ 2:31 $0.23           │ ┌ COST ────────────────────┐ │
│ │ 3 2,967 "Carreyrou…"       [✓take 2][flagged·2] ▶━━ 3:04 $0.29           │ │ narration 15k chars $1.55│ │
│ │                            ▶ 2:35 "carreyrou" unclear…                    │ │ Spent  $1.55 · 15,545 cr │ │
│ │                            [Regenerate…]                                  │ │ Waste ratio  12%         │ │
│ └──────────────────────────────────────────────┘ │ Monthly cap ▓▓▓░░ 62%    │ │
└──────────────────────────────────────────────┴───────────────────────────────┘
```

#### Header

- **Title**: the script's title. **Subtitle**: "{project} · 8 chunks · 15,545
  chars".
- **Actions**, left to right:

| Button | What it does | Disabled when |
| --- | --- | --- |
| **Dry run** | Works out the cost of generating everything outstanding and prints it in the log. Sends nothing. | Something is running |
| **✦ Generate** (blue) | Starts generating all outstanding narration (and effects, if ticked). **Spends immediately**; there is no confirmation step. Its label changes to "Working…" while **any** action on the page is running, not just generation. | Something is running |
| **⤓ Export** | Stitches the cut into the chosen formats. | Something is running, **or** any chunk has no audio. The reason is only in a hover tooltip: "Every chunk needs a take before there is anything to stitch". |
| **Replace script…** (ghost) | Opens the Replace panel. | Something is running |
| **Delete…** (ghost, red on hover) | Opens the Delete panel. | Something is running |

All header actions share one "busy" state: while a generate, check,
regenerate or export is running, all of them are disabled.

#### Generate: what the user sees

1. Pressing **Dry run** or **Generate** prints the projection at the top of
   the log:

   ```text
   narration 7 chunk(s), 15,545 chars — $1.5545
   effects   3 cue(s), 12s — $0.0300
   total $1.5845
   ```

2. On a real run, a line per chunk streams in as it finishes ("chunk 2: ok
   (515 chars billed)"), then a summary: "7 chunk(s) · 0 cue(s) generated, 7
   reused · $0.3416".
3. Everything on the page refreshes when the run ends.
4. If the project's monthly cap would be exceeded: red notice "That run did
   not finish — Blocked by the project's monthly cap — the whole run,
   narration and cues. Raise the cap, or turn effects off for this run."
5. Any other failure shows as a red notice "That run did not finish" with
   the server's reason.

#### Replace script… panel (inline, under the header)

For uploading an edited version of the script over this episode. Chunks whose
words didn't change keep their takes, so only the changes are paid for.

- A file input ("New script file") or, if no file is chosen, a text box
  ("…or paste the new script here").
- **Price it** sends nothing and shows what would happen: "Keeps **6**
  chunk(s) with their 6 take(s); adds **2**; drops 1, keeping their 1 paid
  take(s) in an archived copy. Generating what changed afterwards: 2 chunk(s)
  — about **$0.48**. Replacing sends nothing." Plus faint lines about effect
  slots removed, an outline detached, and any warnings.
- If the text is identical: "That script is the same as the current one."
- After pricing, the button becomes **Replace the script** (blue). Changing
  the file or text throws the price away.
- If a generation seems to still be running, the refusal is shown with a
  checkbox: "It crashed and will never finish — replace anyway".
- **Cancel** closes the panel and returns focus to the button.
- After replacing, the log says "Script replaced: 6 chunk(s) kept with their
  takes, 2 new, 1 dropped. Generate what changed — about $0.48."

#### Delete… panel (shared with project delete)

- Opening it changes nothing. It first shows "Checking what this would
  delete…", then: "Deletes the episode **011** — 15 take(s)." and "The
  **$1.20** it cost stays on the record, attributed to it, in every total. It
  can be restored from the Costs page."
- If a run appears to be in progress: amber "A generation looks like it is
  still running. Wait for it to finish before deleting." and a checkbox "It
  crashed and will never finish — delete anyway". Delete stays disabled until
  it's ticked.
- Checkbox: "Also delete its 15 file(s) from disk (42.3 MB) — they cannot be
  restored". Off by default.
- **Delete episode** (red) and **Cancel**. Focus lands on **Cancel**, so
  pressing Enter twice can't delete by accident.
- After deleting, the app goes back to Projects.

#### Card 1: Track view (main column, top)

A visual timeline of the episode in two lanes.

- **Header line**: script title, total runtime and total cost
  ("21:40 · $1.2034").
- **Ruler**: about six time labels at round intervals (0:00, 2:00, 4:00…).
- **Effects lane** (labelled "Effects — overlay track"), shorter, **above**
  the narration on purpose: effects play over the speech, they don't add
  time. Shows "no effects" if empty.
- **Narration lane**: one block per chunk, positioned and sized by its real
  start time and length.
- **Block styles**: narration in neutral grey-blue; generated effects in
  teal; planned (not generated) effects as an amber dashed outline. Blocks
  wide enough (over 7% of the width) show the chunk number and the opening
  words; narrower blocks are plain markers with a hover tooltip ("0:01:14.200
  – 0:02:31.900 In 2003, a…"). Very short effects are kept at least 0.6% wide
  so they stay clickable.
- **Playhead**: a vertical line through both lanes while the transport
  plays.
- **Clicking** a lane jumps the transport there. Clicking a block selects it
  (blue ring) and jumps to its start.
- **Legend**: colour swatches for narration, effect and "planned, not
  generated"; amber "timings provisional" if any chunk is missing audio; and
  the hint "click a lane to seek · space to play".
- **Detail panel** for the selected block: a type tag, start–end times,
  length, drift against its target if any ("+3.2s vs target"), its full text
  or description, and a wide player, or "Not generated yet — nothing to
  play."
- **Empty state** (nothing generated yet): "Nothing on the timeline yet —
  Press Generate — it produces the narration and its effect cues in one run,
  and the monthly cap is checked against both together."

#### Card 2: Chunks and takes (main column)

The core loop: listen to each take, choose the one to use, and fix the bad
ones.

**Card actions:**

- **Check takes** (ghost): runs the free transcription check on takes not yet
  checked. Disabled until at least one take exists. The log shows "Checking
  takes against the script (speech-to-text) — nothing is sent anywhere.", then "7
  take(s) checked: 5 flagged, 0 to review, 2 with no issues found."
- **show the table / hide the table**: reveals the detailed timeline table
  at the bottom of the card.

**Flagged banner** (amber, when any take in the cut is flagged or needs
review): "5 take(s) in the cut are flagged — Words missing, added or changed
in chunk 3, 4, 6, 7, 8. Press ▶ beside each finding to hear the spot, or fix
them all at once." with a **Fix all flagged…** button that opens a Regenerate
panel covering all flagged chunks (button becomes **Cancel**).

**Chunk table:**

| Column | Content |
| --- | --- |
| # | Chunk number. |
| Chars | Character count. |
| Opening | The first 90 characters of the chunk's text. If the chunk had to be cut in the middle of a sentence: amber "split mid-sentence — consider adjusting". |
| Takes | "not generated" if it has none; otherwise every take, stacked (below). |

**Each take** is one row:

- A tag **"take 2"**: green with a tick if it's in the cut, plain otherwise.
- A **verdict badge**:

  | Badge | Look | Meaning |
  | --- | --- | --- |
  | flagged · 2 | red | Words missing, added or garbled; 2 serious findings |
  | review | amber | Probably fine, worth a listen |
  | no issues found | faint, dashed border | The check found nothing; deliberately not a tick |
  | check failed | red | The check itself couldn't run |
  | fine by ear | faint, dashed border | Flagged, but you listened and marked it fine; the findings stay listed, dimmed |
  | *(none)* | — | Not checked yet |

- Duration and cost in faint monospace: "151.24s · $0.2345".
- A **mini player** (play/pause, scrub bar, time).
- **use this** button: puts this take into the cut. Shown only on takes not
  in the cut that succeeded.
- **It's fine** (ghost) on a flagged take: after listening, marks it fine.
  It leaves the flagged banner and Fix all, and plan.md stops listing it.
  Becomes **undo** once marked.
- **Findings** under the take, at most four, then "and 6 more on this take.":
  each is a **▶ 2:44** button that plays the take from 1.5 seconds before the
  problem, then the description ("'magazine' not spoken") and a faint note.

**Regenerate…** (ghost, under a chunk's takes) opens a Regenerate panel in a
new row beneath that chunk.

##### Regenerate panel (one chunk, or all flagged)

Generates new takes for a chunk without losing the old ones. Two steps: price,
then spend.

| Option | Choices | Default |
| --- | --- | --- |
| Change the words first (checkbox, single chunk only) | Reveals a text box holding the chunk's text to edit. Hint: rewording is only allowed on models without continuity, because elsewhere neighbouring chunks would be re-billed. | Off |
| If the new take is still flagged | "keep trying, up to 3 tries (recommended)" · "try again, up to 2 tries" · "stop — one try" | 3 tries |
| Use the new take | "when it checks better (recommended)" · "unless it checks worse" · "never — I'll choose after listening" | When better |
| Rebuild the episode afterwards (checkbox) | Re-exports with the new take, in the formats chosen on this page | On |

- With more than one try: hint explaining that after a failed plain try, the
  next uses the steadiest delivery, and it stops at the first clean take.
- **Price it** asks the server for the cost and any blockers. Sends nothing.
- **Blockers** appear as amber notes, e.g. a chunk whose last request never
  got an answer and may already have been billed. Blocked chunks are skipped
  ("Chunk 4 will be skipped."), and a checkbox appears: "I have checked —
  regenerate anyway, accepting it may bill twice".
- **Quote**: "Up to **$0.27** at list price (3 tries at most). The new take is
  checked with speech-to-text." (plus "Mock provider — nothing is
  billed." in practice mode).
- The button then becomes **Regenerate for up to $0.27** (blue). The amount
  shown is sent as a hard spending limit.
- **Cancel** closes the panel and returns focus to the button that opened it.
- Afterwards the log gets a line per chunk, e.g. "chunk 4: suspect, clear —
  now in the cut — 1 made with the steadiest delivery", "spent $0.0598", "The
  episode was rebuilt with the new takes."

##### Detail table (when "show the table" is on)

The running order as a table: #, Start, End (hh:mm:ss.mmm), Length, Track (tag:
narration / effect / planned), Content (effect descriptions in italics), and a
player per row. Below it, an "Anchor drift" list ("Chunk 3 targeted
00:02:15.000, landed 00:02:18.400 (+3.4s)") and, if anything is missing,
"Timings after the first ungenerated chunk are provisional."

#### Card 3: This run (aside)

- **Include effect cues** checkbox (on by default). Applies to Dry run and
  Generate.
- Info note: "One Generate covers narration and the accepted cues, and the
  monthly cap is checked against both together. Manage cues →" (links to
  Effects).
- Amber warning if the model has no continuity across chunks.
- **Export formats**: toggle chips, one per format (WAV, MP3, M4A (AAC),
  FLAC, Opus), each with a tooltip explaining it. WAV and MP3 are on by default.
  These apply to the header's Export button and to rebuilds after
  regenerating.
- **Log**: a monospace box that appears once something has run, streaming
  progress lines. Screen readers announce new lines. The log lives only on
  this page: navigating away mid-run loses it.
- **Export result**: green "Exported wav, mp3, each also with its 3 effect(s)
  mixed in (_fx) — see the files →" (links to Media).

#### Card 4: Cost (aside)

| Line | Content |
| --- | --- |
| Breakdown table | One row per kind of spend (narration, effects, suggestions, probes): units ("15,545 chars", "12.0 s"), dollars, credits ("15,545 cr" or "—"). |
| Spent | Dollars, and credits in faint text. |
| Outstanding / Projected | Only while chunks or effects remain: "~$0.27" outstanding, the projected total, and "1 chunk still to generate." |
| In the cut | Spend on the takes currently chosen. |
| Re-rolls | Spend on takes not in the cut. |
| Waste ratio | Percentage, amber above 25%. |
| Cost / minute | Spend per minute of finished audio. |
| Monthly cap | "$20.00 · 62%" with a thin bar: blue, amber from 80%, red at 100%. Only when the project has a cap. |
| Footnote | "Waste is spend on takes that never made the cut — the number that says whether the direction is working, not the voice." |

---

### 8.3 Effects (`/script/:id/effects`)

**Purpose:** see, add, accept and generate the sound effects for one episode.

**Header:** "Effects". Subtitle: "{title} · no cues yet" / "· 3 cues still to
generate" / "· all cues generated". No header actions.

**One card containing:**

**Slots table:**

| Column | Content |
| --- | --- |
| Chunk | The chunk number the effect starts at (no text from the chunk is shown). |
| Effect | The description, then faint monospace details: length ("4s" or "auto"), "· loop", and where it came from ("marker", "suggested" or "manual"). Any note below. |
| Status | "not accepted" tag; or "planned" (amber tag); or, once generated, a teal tag with its short name and sparkle icon plus a mini player. |
| (actions) | **accept** (only for unaccepted suggestions) and a **trash** button that deletes the slot **immediately, without confirmation**. |

**Empty state:** "No effect slots — Add `[SFX: description, 4s]` markers to
the script, or one below."

**Buttons under the table:** **Dry run** and **✦ Generate 3** (blue; the
number is how many distinct sounds will be generated). Both disabled when
nothing is planned. Results appear as a line: "Would generate 3 for $0.0300,
reusing 1." / "Generated 3, reused 1 — $0.0300."

**Notes, when relevant:** "5 slots, 3 distinct cues — repeats are generated
once and reused." and "2 suggestions awaiting review. Nothing is generated
until accepted."

**Add an effect** form: Description (placeholder "heavy wooden door creaking
open"), At chunk (number, from 1), Seconds (0.5–30, placeholder "auto"), and
**+ Add**.

**Explainer** at the bottom: "A cue is generated once and placed as often as
you like — repeats cost nothing. The export mixes the effects into an `_fx`
version of each master, and keeps the plain master and each effect's own file
for an editor who wants them on a separate track."

Not possible here: editing a slot, moving it, previewing it against the
narration it sits on, or asking the AI for suggestions (command line only).

---

### 8.4 Media (`/script/:id/media`)

**Purpose:** every file the **project** has produced, to play, read or
download, plus the voice versions of the open script.

**Header:** "Media". Subtitle: "{project} — everything generated for this
project".

#### Main card: the library

- A summary line: "528 files · 3.1 GB on disk".
- Files in groups, each with a heading, its total size and, for exports, a
  **⤓ zip** link that downloads the whole export:
  - **"{script title} — export"**, one group per exported episode (written to
    `assets/exports/`, or to the folder set as `NARRATE_EXPORT_DIR`): masters
    (`Episode.mp3`, `Episode_fx.mp3`, `.wav`…), the timeline-named pieces,
    `plan.md`, `publish.md`, `publish.json`.
  - **"Effects"**: the project's effect library; each labelled "door-creak ·
    3 placements".
  - **"Takes"**: **every** successful take in the project, across all
    episodes, labelled "chunk 3 · take 2 · in the cut".
- Each file row: filename (monospace), label and duration, size, then a mini
  player for audio, a **Read** button for documents (.md, .txt, .json), or
  "—", and a download icon.
- **Read** replaces the list with the document viewer (§9) and a "← Back to
  files" button.
- States: skeleton rows while loading; an error notice if it fails; "Nothing
  generated yet — Generate a script, then export it — the exported master,
  its timeline-named pieces, the effect library and every take appear here."
  With no project: "No project selected — Pick one on the projects page…".

There is no search, filter, sort, or collapsing. On a real project the Takes
group alone is hundreds of rows.

#### Aside: Versions

Every voice this script has been generated in:

| Column | Content |
| --- | --- |
| Voice | Voice name (bold) and the start of its id. |
| Coverage | "8/8". |
| (status) | Green "complete" badge, or amber "2 missing". |
| (action) | **⤓ Export** for complete versions ("Exporting…" while running); "—" otherwise. |

Notes underneath depending on the situation: with one version, how to make a
second ("change the project's voice and generate again with **force**"); with
several, that each exports to its own folder; if any version is incomplete,
that it can't be exported until the missing lines are generated; and after
exporting, "Exported Rachel → script-17-rachel". Empty: "Nothing generated
yet — Generate the script and the voice it was made in appears here."

---

### 8.5 Editing plan (`/script/:id/plan`)

**Purpose:** the document an editor uses to assemble the video: what plays
when, and what's still missing.

**Header:** "Editing plan". Subtitle: "{title} — what plays when, and where a
cue still belongs". Actions: download links **⤓ script .md** and **⤓ script
.txt** (the original script).

**Empty state:** "No plan yet — Generate the script and the plan fills in —
every take, every cue, and where each one sits on the timeline."

**Tabs:** **Document** | **Analysis** (keyboard: arrow keys move between
tabs).

#### Document tab

The plan rendered as a readable document (§9, document viewer):

- A header strip with the filename "plan.md", a **Copy** button (shows
  "Copied" with a tick for 1.6 seconds) and a **⤓ plan.md** download.
- The document itself, with headings, paragraphs and tables. The running
  order table is **sortable** by clicking a column header (numbers and times
  sort correctly), and **clicking a row moves the transport to that moment**.
- On the right, a sticky column with:
  - **Contents**: links to each heading, indented by level; clicking scrolls
    smoothly.
  - **At a glance** card: Runtime, Narration "8 chunk(s)", Effects "3 placed",
    Not generated "1 cue(s)" (amber, only if any), Spent (bold). If anything
    is missing: amber "Some pieces are still missing, so the timings after
    the first gap are provisional."

#### Analysis tab

Main column:

- **Where the runtime went**: three horizontal bars with times: Narration,
  Gaps between chunks, and Effects (overlaid, not added). Note: "Effects are
  overlays, so their duration does not extend the runtime — it is how much of
  the episode has something under it."
- **Longest and shortest**: longest chunk and shortest chunk (opening words
  and seconds), and cost per finished minute.

Aside:

- **Anchors that drifted**: a note explaining that `[@ MM:SS]` is a target
  that can't be forced, then a table Wanted / Landed / Drift, worst first,
  drift over 10 seconds in amber.
- **Still to generate**: a list of missing items with their time as an amber
  badge.
- If neither applies: "Nothing outstanding — Every cue is generated and every
  anchor landed close to its mark."

---

### 8.6 Publish pack (`/script/:id/publish`)

**Purpose:** everything needed to upload the episode to YouTube.

**Header:** "Publish pack". Subtitle: "{chosen title} — chapters, title, tags
and the thumbnail brief". Action: **⤓ chapters .txt** (the chapter list,
ready to paste into a YouTube description).

**States:** red "Could not load the publish pack" on error; "Nothing to
publish yet — Ingest a script and the pack fills in. Chapters and the
retention target need no API key at all." while empty.

**Tabs:** **Document** | **Packaging**.

#### Document tab (publish pack)

`publish.md` in the document viewer (same as Plan: copy, download, contents,
sortable tables, click a row to jump). The side summary shows: Runtime;
Chapters count (green if YouTube would accept them, amber if not); Target
average view "37%" (amber if extrapolated); Hold for "8:01"; Tags count.

#### Packaging tab

Main column:

- **Chapters**: any rule YouTube would reject appears as an amber notice
  titled "YouTube would not accept this" (the rules: first chapter at 0:00, at
  least three chapters, each at least 10 seconds apart). Then a table with
  columns **at** / **chapter** / **chunk**. The hint under it says "Times are
  measured from the exported audio, not from the script's anchors. Click a row
  to hear it.", but **these rows aren't clickable** (only the Document tab's
  are). Empty: "No chapters named — Add a `## Heading`, a `[CHAPTER: ...]`
  marker, or a name on an anchor — `[@ 03:00 The Employee Trap]` — then
  re-chunk."
- **Titles**: the chosen title in bold, with an amber "will be cut off" badge
  if over 60 characters. Without one: "No title chosen — Generate candidates
  with `narrate publish draft --go`." (a command-line instruction). Then a
  table of candidate titles: **formula** (which of nine title formulas it
  uses), **chars** (amber badge if too long), **title**, **why** (the AI's
  reasoning), and a **choose** button, or a green "chosen" badge.

Aside:

- **Thumbnail**: the chosen thumbnail brief as a list: Composition (e.g.
  "Face First"), Text on image (bold, with a word-count badge, green at five
  words or fewer), Subject, Contrast, Leans on. No image is generated; the
  brief is the deliverable. Empty: "No brief yet — `narrate publish brief
  --go`." If there are several briefs, a list with **choose** buttons.
- **Retention**: "A 21:40 episode should hold **37%** to be doing well, and
  **42%** to be doing very well — that is **8:01** and **9:06** of watch
  time." With an amber "Extrapolated" notice outside the 8–120 minute range.

The description and tags are in the Document tab but can't be edited in the
app.

---

### 8.7 Cast (`/cast`)

**Purpose:** name the speakers in a project and give each a voice, for scripts
with more than one person speaking.

**Header:** "Cast". Subtitle: "{project} — 2 speakers" or "who speaks, and in
which voice".

**Without a selected project:** "Pick a project first — The cast belongs to a
project, so a character carries across every episode in it."

#### Main card: Speakers

| Column | Content |
| --- | --- |
| Writes as | The name as typed in scripts, in code style: `Morag:` |
| Voice | Voice name and its full id (faint monospace). |
| (hear) | "Hear it" sample button. |
| (remove) | Trash button: removes the speaker **immediately, without confirmation**. |

Empty: "Nobody is cast yet — Add a name below, then write `Morag: her line` in
a script."

**Add form** under the table: "Name in the script" (placeholder "Morag"),
"Voice" (a plain dropdown of every voice: no search and no "hear it" before
choosing, unlike the voice picker on Projects), and **+ Cast**.

#### Aside

- **Writing dialogue**: how to write lines (`Morag: You knew it would end.`),
  that a name only counts when cast (so `Note:` and `12:30` stay narration),
  per-script casting with `[CAST] Morag = <voice_id>`, and `[VOICE: Morag]`
  to switch mid-paragraph.
- **Applying a change** (when anyone is cast): amber note that recasting
  doesn't change audio already generated, and that a script must be
  re-imported to pick up the change; "8 scripts in this project."

---

### 8.8 Costs (`/costs`)

**Purpose:** all spending, everywhere, with what bought it; and restoring
deleted work.

**Header:** "Costs". Subtitle: "every dollar and credit, attributed to what
bought it". On the right, the grand total as a large figure: "$12.3456".

#### Main column

- **By project**: Project, Spend, Waste %, Cap badge. Note: "Waste is spend on
  takes that never made the cut — the number that says whether the direction
  is working, not the voice."
- **Deleted** (only if anything is deleted): rows "Project · Old series
  $1.2000 [Restore]" and "Episode · 001 $0.4200 [Restore]". Note: "Deleting
  hides a project or episode and stops it spending. What it cost stays
  counted — in the total above, and against its project's monthly cap." A
  failed restore shows a red notice above the card.
- **By script and operation**: for each script with spend, a heading with its
  title (link), total, and cost per minute, then a table: Operation
  (narration / effects / suggestions / probes / corrections), Units, Spend,
  Credits. Skeleton rows while loading; error with "Try again"; "Nothing
  generated yet." if empty.

#### Aside: Account

Total spend (bold), Of that, wasted (amber if any), Projects count, Scripts
count, and a note that every figure comes from an append-only record, so a
re-roll can never disappear from it.

Not shown: spend over time, this month vs last, per-day activity, or the
ElevenLabs credit balance remaining.

---

## 9. Shared components

Everything the pages are built from today. The redesign will likely redraw all
of these; this is the inventory to cover.

| Component | Where | Variants / states |
| --- | --- | --- |
| **Button** | Everywhere | Default (grey surface), **primary** (blue), **ghost** (transparent, dim text), **small**, **danger** (red border/text on hover). Disabled = 40% opacity. Pressed state for toggles. |
| **Download link** | Media, Plan, Publish, templates | Bordered link with download icon; blue border on hover. |
| **Card / panel** | Everywhere | Surface with border, small-caps heading, optional heading actions. |
| **Sub-panel** | Inline forms (project form, track detail) | Inset surface inside a card. |
| **Inline action panel** | Regenerate, Replace, Delete | Options, quote text, notes, actions row (primary + Cancel). Opens in place, never as a modal. |
| **Page header** | Every page | Title, subtitle, actions; Costs adds a large figure. |
| **Notice** | Everywhere | info (neutral), **warn** (amber), **error** (red, announced to screen readers), **ok** (green). Optional bold title. |
| **Tag / badge** | Tables, takes, slots | neutral, **cut** (green + tick), **effect** (teal), **planned** (amber), **bad** (red), **warn** (amber), **ok** (green), **quiet** (faint, dashed). Pill-shaped outline. |
| **Stat row** | Cost panels, summaries | Label left, figure right; **total** variant bold with a rule above. |
| **Bar** | Monthly cap, Analysis lanes | Thin bar; blue, amber, red. |
| **Data table** | Most pages | Numeric columns right-aligned with tabular figures; compact variant; built-in empty state; horizontal scroll when narrow; row hover tint. |
| **Sortable document table** | Plan, Publish documents | Click header to sort (number- and time-aware); click row to seek. |
| **Tabs** | Plan, Publish | Underline/pill tabs, optional count hint, arrow-key navigation. |
| **Form field** | Forms | Label above; hint below; error below in red. Text, number, select, textarea. |
| **Checkbox row** | Options | Checkbox with sentence label. |
| **Chips** | Export formats, transport lanes | Toggle pills; "on" highlighted. |
| **Slider row** | Delivery | Label, range input, value in monospace. |
| **Drop zone** | Add a script | Dashed area; drag-over highlight; "Reading…" busy text. |
| **Voice list** | Voice picker | Selectable rows with a separate "Hear it" button. |
| **Mini player** | Takes, effects, media, timeline rows | Round play/pause, thin scrub bar (playhead dot appears on hover/play), time "0:42 / 2:31", optional download icon; **wide** variant in the track detail. |
| **"Hear it" button** | Voice picker, Cast | Small pill: Hear it / Loading… / Playing / No sample. |
| **Transport bar** | Shell | §7.2 |
| **Track view** | Script | Ruler, two lanes, blocks (narration / effect / planned / selected), playhead, legend, detail panel. |
| **Findings list** | Takes | "▶ 2:44" jump buttons with descriptions, colour by severity. |
| **Log** | Script | Monospace, scrolling, announced to screen readers. |
| **Empty state** | Everywhere | Bold title, sentence saying what would fill it, optional action. |
| **Skeleton** | Loading lists and tables | Grey bars of decreasing width. |
| **Document viewer** | Plan, Publish, Media "Read" | Header strip (filename, Copy, download), rendered markdown in a reading column, sticky side column with Contents and an optional summary card. |
| **Icons** | Various | Waveform (narration/script), sparkle (effects/generate), download, upload, plus, trash, check, play, pause, sun, moon, auto-theme. Hand-drawn inline SVGs. |

There are **no modals, dialogs, toasts, dropdown menus, tooltips beyond the
browser's own, or popovers** anywhere today.

---

## 10. The current visual system

For reference and contrast. The redesign is free to replace it, but these
are the semantics the current colours carry.

### 10.1 Character

A dense, utilitarian "studio" tool: dark or light following the computer's
setting, small type (13px body), lots of tables, monospace for every figure
and id, colour used sparingly and only for meaning.

### 10.2 Colour

Each colour is defined once with a light value and a dark value.

| Token | Light | Dark | Used for |
| --- | --- | --- | --- |
| bg | #f6f7f9 | #0b0d12 | Page background |
| surface-1 / 2 / 3 | #ffffff / #f1f3f6 / #e7eaef | #12151c / #191d26 / #222732 | Cards, insets, hovers (three levels) |
| line / line-soft | #d8dde5 / #e8ebf0 | #2b3140 / #20242f | Borders, table rules |
| text / dim / faint | #14171d / #5c6675 / #8a93a1 | #e8eaf0 / #98a1b3 / #697285 | Three levels of text emphasis |
| **accent** | #2563eb | #6ea8fe | Primary buttons, selection, focus ring, links, playhead |
| **narration** | #6b7688 | #7d8ba3 | Narration blocks and bars (deliberately neutral) |
| **effect** | #0d9488 | #3ddbc0 | Anything sound-effect (teal) |
| **planned** | #b45309 | #f0b849 | Not-yet-generated work, warnings, cap ≥80% (amber) |
| **danger** | #dc2626 | #f0736a | Errors, flagged takes, destructive actions, cap 100% |
| **ok** | #15803d | #5cc98a | In the cut, success, complete |

Hover, pressed and selected tints are mixed from the text and accent colours
(6%, 11% and 13%).

### 10.3 Type

- System UI font (San Francisco on Mac) for text; system monospace for
  numbers, ids, filenames, times and the log.
- Sizes: 11, 12, **13 (body)**, 15, 19, 26 (page title).
- All numbers use tabular figures so columns line up.
- Card headings are small caps.

### 10.4 Space, shape, depth, motion

- 4px spacing rhythm: 4, 8, 12, 16, 20, 24, 32, 40.
- Corner radii: 6 (controls), 10 (cards), 14 (large), pill (tags, chips).
- Three shadow levels (resting card, raised, floating), mostly subtle.
- Motion: 90ms ("fast") and 180ms ("calm"), one easing curve. All motion is
  switched off when the computer asks for reduced motion.

### 10.5 Layout

- Rail 208px; page content max 1240px wide; aside 330px (min 300px); main
  column grows from 560px.
- One breakpoint, at 900px (§7.4).

### 10.6 Accessibility behaviours to preserve

- A visible 2px focus ring on every control when using the keyboard (not on
  mouse clicks).
- Labels properly linked to every field.
- Tab strips and the voice list are single tab stops with arrow-key movement.
- Opening an inline panel moves focus into it; closing it returns focus to
  the button that opened it.
- Destructive panels put focus on **Cancel**, not on the destructive action.
- Buttons that toggle report their state (pressed / expanded) to screen
  readers.
- The run log and loading states are announced; errors are announced
  immediately.
- Tables have hidden captions describing them.
- Space bar controls playback, except while typing.
- Reduced-motion preference respected.

---

## 11. States, feedback and copy

### 11.1 How the app tells you things

- **Inline only.** Results, warnings and errors appear as notices or lines
  next to the thing that caused them. There are no toasts or pop-ups.
- **Long jobs stream** into the log on the Script page.
- **Loading** is grey skeleton rows for lists and tables; a few places use
  text ("Loading voices…", "Checking what this would delete…").
- **Empty states** always say what would fill them, and often how.
- **Errors** show the server's own message, which is usually specific and
  actionable ("Chunks [8] have no selected take. Generate them, or pick a
  take, before exporting…"). Some come straight from ElevenLabs and are
  confusing (see §12).
- **Busy buttons** change their label: "Saving…", "Pricing…", "Replacing…",
  "Deleting…", "Exporting…", "choosing…", "Working…".

### 11.2 Statuses the design needs to represent

| Thing | States |
| --- | --- |
| Chunk | not generated · generated · split mid-sentence (warning) |
| Take | succeeded · failed · in the cut / not in the cut |
| Take check | not checked · no issues found · review · flagged (with count) · check failed · fine by ear |
| Finding | fail (serious) · review · info (hidden) |
| Effect slot | suggested, not accepted · planned · generated |
| Timeline entry | narration · effect · planned |
| Script (episode) | no audio · partly generated · complete (exportable) · exported · deleted |
| Version (voice) | complete · N missing |
| Project | voice unset · under cap · cap ≥80% · cap reached · deleted |
| Chapters | usable · rejected by YouTube rules (with reasons) |
| Title | none · candidates · chosen · too long |
| Retention target | published range · extrapolated |
| Any run | idle · running · finished · failed · blocked by cap |

### 11.3 Copy conventions

- Plain, specific sentences that say **why** and **what next**: "A voice
  missing some chunks cannot be exported — the master would have a silent
  gap. Generate the missing lines in that voice first."
- British spelling ("honour", "colour", "normalise").
- Sentence case for buttons and headings. Ellipsis (…) on buttons that open a
  panel rather than act immediately ("Delete…", "Regenerate…", "Fix all
  flagged…").
- Money: dollars with 4 decimals by default ("$0.2345"), 2 for caps and some
  totals. Credits as "15,545 cr".
- Times: "2:44" for short spans, "00:02:44.200" in the detailed timeline,
  "21:40" for runtimes.
- Ids, filenames, markers and commands in monospace or code style.

---

## 12. Known friction: where the redesign can help most

Observed in the code and in real use. Ordered roughly by how much each one
hurts.

1. **Export looks broken when it's only blocked.** The Export button is
   disabled until every chunk has audio, and the only explanation is a hover
   tooltip (browsers often don't show tooltips on disabled buttons). This was
   reported as "the export button is not working" after a run had silently
   failed on two chunks. The page should say plainly what's missing and how to
   fix it.
2. **Failed takes look like real takes.** A take that failed (for example,
   because credits ran out) is listed as "take 1", with "$0.0000" and a
   player that plays nothing. The reason it failed isn't shown, and there's no
   failed state.
3. **Provider errors are misleading.** Running out of ElevenLabs credits comes
   back labelled "Authentication failed: This request exceeds your quota…".
   The app passes it through as-is. It should be translated into "You've run
   out of ElevenLabs credits", with what to do.
4. **Generate spends without showing the price first.** Everywhere else
   (Regenerate, Replace) you price, then confirm an amount. The main Generate
   button starts spending immediately; the cost appears in the log as the run
   begins. Dry run is a separate button next to it. Consider making Generate
   follow the same price → confirm pattern.
5. **The run log disappears.** Progress lives only on the Script page. Leaving
   the page mid-run loses it, and coming back shows nothing about the run
   still going. There's no app-wide sense of "something is running".
6. **One busy state for everything.** While exporting or checking, the
   Generate button reads "Working…" and every action is disabled, so it's not
   clear what's happening.
7. **Export settings are separated from Export.** The format chips and the
   export result sit in the aside, while the Export button is in the page
   header.
8. **No status overview.** The scripts list on Projects shows chunks and
   characters only. You can't see which episodes are complete, flagged,
   exported or published without opening each.
9. **Voices are shown as ids.** The Projects table shows the first 12
   characters of a voice id; Cast shows the full id beside the name.
10. **Project selection is unusual.** Project names are buttons inside a
    table, and the "Project" dropdown in "Add a script" is always disabled,
    which looks broken.
11. **The monthly cap can only be set when creating a project.** The Voice &
    delivery editor has no cap field.
12. **Media doesn't scale.** One flat list per group, no search, filter,
    sorting or collapsing. The Takes group lists every take of every episode
    in the project (hundreds of rows). The page is "project-wide" but lives
    under a script's address.
13. **Destructive actions without confirmation.** Deleting an effect slot and
    removing a cast member happen on one click.
14. **The Cast voice choice is weaker than the main voice picker.** A plain
    dropdown: no search, no hearing a voice before choosing it.
15. **Publish leans on the command line.** Titles, thumbnail briefs,
    description and tags can only be generated or edited from the terminal;
    the page shows the commands as text. The chapters table says "Click a row
    to hear it", but its rows don't respond.
16. **Navigation shifts.** Five of eight rail items appear only while a
    script is open. There's no way to switch scripts without going back to
    Projects, and no breadcrumb showing project › episode.
17. **Inconsistent naming.** "Script" and "episode" for the same thing;
    "cues", "effects", "slots" and "SFX" for related things; table headers in
    Title case on most pages but lower case on Publish.
18. **Effects lack context.** Slots show only a chunk number, not the words
    the effect sits on, and can't be edited or moved once added.
19. **Narrow screens were never designed for.** Usable, but the transport
    loses its lane toggles and every table scrolls sideways.

---

## 13. Features that exist only on the command line

The command line can do everything the web app does, plus the following. These
are candidates for the redesign to bring into the interface.

| Area | Command-line feature | What it does |
| --- | --- | --- |
| Writing | `write outline` / `beats` / `beat` / `sync` / `expand` | Drafts an episode with AI: proposes a beat sheet (sections, purpose, share of runtime), lets you edit each beat, then writes the prose section by section. Paid steps are priced first. |
| Dialogue | `script add … --dialogue` | Imports a multi-speaker script rendered as real dialogue (speakers react to each other). |
| Chunks | `chunk review` / `merge` / `split` / `rechunk` / `set` | Shows each chunk's cost before generating; merges or splits chunks; re-splits the script; overrides voice, model, delivery or tags for one chunk. |
| Effects | `effects suggest` / `library` | Asks the AI where effects might belong (suggestions appear in the UI as "not accepted"); lists the project's effect library and where each is used. |
| Publish | `publish draft` / `brief` / `set` / `title` | Generates title candidates, description and tags, or thumbnail briefs; sets description, tags or target length by hand; adds a title by hand. |
| Project | `project set --default-tags`, `--description-boilerplate`, cap | Channel-wide tags and description text; changes the monthly cap after creation. |
| Voices | `voice clone` / `capability` / `register` / `forget` / `remove` | Clones a voice from audio samples; checks whether the plan allows cloning and how many slots are left; gives voices short local names; frees a slot. |
| Costs | `cost log` / `reconcile` | Every individual charge, newest first; compares the app's records with ElevenLabs' own counter and lists charges whose outcome is unknown. |
| Export | `export --voice`, `--no-mix`, gap and piece-format options | Exports a specific voice; skips the effects mix; changes the gap between chunks or the format of the individual pieces. |
| Health | `doctor` | Checks ffmpeg, the database, the API key and the connection, and shows the **ElevenLabs credits used and remaining**. Nothing in the web app shows the remaining balance. |
| Data | `db status` / `list` / `adopt` | Where the records live, and moving them. |
| Practice | `NARRATE_PROVIDER=mock` | Runs everything with a stand-in voice that costs nothing (also works in the web app). |

---

## 14. Appendix: realistic content and where things live in code

### 14.1 Realistic content for mock-ups

Taken from the current user's real data, so mock-ups can use believable
numbers and lengths:

- 5 projects; about 17 episodes; episode titles are often short ("011",
  "Episode 1") but can be long
  ("01_The_43_Billion_Decision_That_Brought_Down_Two_Planes").
- 5–10 chunks per episode; 1,400–3,000 characters per chunk; about 15,000
  characters per episode; episodes around 20 minutes.
- A chunk costs roughly $0.03–$0.30; an episode $0.30–$2.
- Takes per chunk: usually 1, sometimes 2–4 after fixes and failures.
- A check on 7 takes typically flags 3–5 of them, many for proper names the
  transcriber mishears ("Carreyrou" heard as "kerry roo").
- The media folder holds 528 files (3.1 GB).
- ElevenLabs balance: tens of thousands of credits a month; one episode uses
  roughly 2,000–4,000.

### 14.2 Where each page lives (for engineers)

| Page | File |
| --- | --- |
| Shell, rail, theme toggle | `frontend/src/App.tsx` |
| Routes | `frontend/src/router.tsx` |
| Projects | `frontend/src/pages/Projects.tsx`, `components/ProjectBar.tsx`, `ProjectForm.tsx`, `ModelField.tsx`, `VoicePicker.tsx`, `Audition.tsx`, `ScriptUpload.tsx` |
| Script | `frontend/src/pages/Script.tsx`, `components/TrackView.tsx`, `ChunkList.tsx`, `RegeneratePanel.tsx`, `ReplacePanel.tsx`, `DeletePanel.tsx`, `Timeline.tsx`, `CostPanel.tsx` |
| Effects | `frontend/src/pages/Effects.tsx`, `components/EffectPanel.tsx` |
| Media | `frontend/src/pages/Media.tsx`, `components/MediaLibrary.tsx`, `Variants.tsx` |
| Plan | `frontend/src/pages/Plan.tsx` |
| Publish | `frontend/src/pages/Publish.tsx` |
| Cast | `frontend/src/pages/Cast.tsx` |
| Costs | `frontend/src/pages/Costs.tsx` |
| Transport and players | `components/Transport.tsx`, `Player.tsx`, `src/transport.ts`, `src/audioBus.ts` |
| Shared UI | `frontend/src/ui/` (layout, feedback, form, Tabs, Table, DocumentView, Markdown) |
| Design tokens and all styles | `frontend/src/styles.css` |
| Full feature list (technical) | `docs/FEATURES.md` |
| Original product requirements | `docs/PRD.md` |
