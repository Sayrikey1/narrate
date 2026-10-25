# Integrating the studio redesign

The design team's mock-ups (`narrate-studio.zip`, kept out of git) are eight
static pages — Projects, Workbench, Effects, Media, Plan, Publish, Cast, Costs —
rendered from a template with sample data. They are the specification, not
code to ship: each page is rebuilt in this React app, wired to real data, with
the design's markup and inline styles as the exact reference for structure,
sizes and colour.

## Decisions

| | Decision |
| --- | --- |
| Fonts | Bricolage Grotesque, Geist and Geist Mono from Google Fonts, as the design does; system fonts offline |
| Phase 5 | Out until chosen item by item |
| Design files | Not committed — the zip is in `.gitignore` |
| Order | Phase 1, then the Workbench, then a review before the rest of Phase 2 |

## How each page is checked

Side-by-side screenshots of the design and the app at 1440px in dark and
light, and at 860px. Every existing test stays green; new behaviour gets tests.
Every commit leaves the app working. Data a page cannot show yet is hidden,
never invented.

## Phases

### 1. Look and frame — frontend only

- [x] Tokens: green-black surfaces, lime accent, violet effects, amber warning,
      coral error; a full light theme
- [x] Fonts
- [x] Building blocks: buttons, cards, tags, stat tiles, meters, chips,
      banners, inline confirmations, the pipeline stepper, icon tiles
- [x] Left menu: project switcher, STUDIO and EPISODE sections, an episode
      switcher that remembers the last episode, monthly-cap meter
- [x] Top bar: breadcrumbs with the episode switcher
- [x] Bottom player: previous/next chunk, effect markers on the scrub bar

### 2. Pages on today's data — mostly frontend

- [x] Workbench — pipeline stepper, price-first Generate panel, Export card,
      chunk cards, inline panels, cost card
- [x] Projects — project cards, pick up where you left off, episode table,
      project settings with an editable monthly cap
- [x] Media — this episode / whole project, search, sort, type filters,
      collapsible groups
- [x] Effects — where they sit, effect cards with their passage, inline edit
      and remove *(editing waits for "Editing an effect" in Phase 3)*
- [x] Publish — packaging first, feed preview, chapters that play
- [x] Plan — a play button on every running-order row
- [x] Cast — speaker cards, voice grid, confirm before removing
- [x] Costs — tiles, by project, by episode
- [x] ⌘K quick switcher — episodes, pages and projects by name

### 3. Free backend additions

- [x] Waveform data for takes, effects and files
- [x] Episode status for the pipeline bars, and whether an export is out of date
- [x] Spend this month and last, and per day
- [x] ElevenLabs credits left
- [x] The global "Generating…" status
- [x] An activity history per episode — read from existing records, so no migration
- [x] Plain-language provider errors; failed takes shown with their reason
- [x] Editing an effect; the effect library
- [x] How much each speaker speaks
- [x] Editing description, tags and your own titles
- [x] "Matches ElevenLabs" — reconcile from the UI

### 4. Paid actions, price shown first

- [x] Suggest effects
- [x] Draft more titles
- [x] Draft thumbnail briefs

### 5. New ideas — only if chosen

- "Published" status on episodes
- An emoji per episode and project
- Placing an effect partway into a chunk
- "Clean up takes not in any cut" (deletes files)
- Generating a version in another voice from the Media page
- Full-text search
