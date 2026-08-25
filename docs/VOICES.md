# 🎭 Voices, clones and slots

How to get your own voice into a project, reuse it across several, and hold two
performances of the same episode side by side.

---

## 🧭 The short version

```bash
narrate voice capability                        # may this account clone?
narrate voice list                              # everything the account has
narrate voice register <voice_id> --name mine   # give it a name you can type
narrate project set "My Channel" --voice mine   # use the name, not the id
```

A **cloned voice is an ordinary `voice_id`.** Nothing in this tool treats it
specially — it appears in the picker, it can be auditioned, it can be cast on a
project, a script, one chunk, or one character. The only special part is
*creating* one, and that depends on your plan.

---

## 1️⃣ Check what your plan allows — first

```bash
narrate voice capability
```

```text
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┓
┃ check                      ┃ value         ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━┩
│ tier                       │ payg          │
│ instant voice cloning      │ not permitted │
│ professional voice cloning │ not permitted │
│ custom voice slots         │ 0 of 3 used   │
└────────────────────────────┴───────────────┘
```

Costs nothing — it reads `GET /v1/user/subscription`. Do this before anything
else, because cloning is a **plan permission**, not something a tool can work
around.

> ⚠️ **The output above is real, from a `payg` account.** Having free voice slots
> is not the same as being allowed to fill them. If `instant voice cloning` says
> `not permitted`, `narrate voice clone` refuses before uploading and tells you
> why, rather than sending a request that fails with something unhelpful.
>
> Whether that flag also stops you cloning in the ElevenLabs **web app** is not
> documented, and I would not assume either way — try it there and see. If the
> web app lets you, [registering the result](#3️⃣-register-a-voice-you-cloned-elsewhere)
> is exactly the path to use.

---

## 2️⃣ Cloning from here

Only when `narrate voice capability` says instant cloning is permitted.

```bash
narrate voice clone "My Voice" samples/*.mp3 \
    --description "narration, calm" \
    --accent british \
    --denoise \
    --assign "My Channel"
```

| | |
|---|---|
| 💸 **Cost** | **No characters.** A clone consumes a voice *slot*, not character quota |
| 🎙️ **Samples** | Instant cloning uses **under two minutes** in total. More is a longer upload for the same result, and the command says so |
| 📋 **Formats** | mp3, m4a, wav, flac, ogg, opus, webm |
| 🧹 **`--denoise`** | Has the provider strip background noise from your samples |
| 🎯 **`--assign`** | Casts the new voice on a project immediately |
| ✅ **Checked first** | The plan permission *and* a free slot, before anything is uploaded |

It reports the total duration it measured and warns if that is far outside what
instant cloning uses — under 10 seconds is very little to work from, over ~150
seconds is wasted upload.

> 🔐 Some voices come back with `requires_verification`. The command says so;
> finish that at elevenlabs.io before generating with it.

---

## 3️⃣ Register a voice you cloned elsewhere

**Registering is not creating.** This is the path when the web app can clone but
the API cannot — and it works for any voice, cloned or not.

```bash
# Clone in ElevenLabs' interface, copy the voice id, then:
narrate voice register 21m00Tcm4TlvDq8ikWAM --name mine --note "recorded Aug 2026"
```

```text
Registered mine → My Voice (cloned)
Use it anywhere a voice is asked for:  --voice mine
```

What it does, and does not:

| Does | Does not |
|---|---|
| Gives the voice a name you can type | Store any audio |
| Confirms the voice exists on the account | Grant any access you did not have |
| Records what kind of voice it is | Create, modify or delete anything on the provider |
| Makes `--voice mine` work everywhere | Consume a voice slot |

```bash
narrate voice registered              # what you have named
narrate voice list                    # account voices, with their local names
narrate voice forget mine             # drop the name; the voice stays
narrate voice register <id> --offline # name it without checking (no key needed)
```

### Why names are worth the trouble

A voice id is twenty random characters, and every voice you clone has twenty
different random characters. A name is what makes a clone usable from a
terminal:

```bash
narrate project set "My Channel" --voice mine     # instead of --voice 21m00Tcm4Tlv…
narrate cast set "My Channel" Morag --voice mine
narrate chunk set 1 7 --voice mine
narrate export 1 --voice mine                     # -> Ep__mine.wav
```

The name reaches the exported filename too, so a master is identifiable without
looking anything up.

> 🛡️ **A name is never silently repointed.** Registering a different voice under
> a name already in use is refused, because a name quietly moving would change
> what the next generation produces — and the charge would land before anybody
> noticed. `--replace` does it deliberately.

> 🔁 **Anything that is not a registered name passes straight through.** Every
> `--voice <id>` still works exactly as before, and no id can be shadowed by a
> name.

---

## 4️⃣ The slot limits, plainly

Voice slots are the real constraint, and they are per **account**, not per
project.

| Field | Meaning |
|---|---|
| `voice_limit` | How many custom voices the account may hold at once |
| `voice_slots_used` | How many it holds now |
| `professional_voice_limit` | Professional clones specifically — a separate, usually smaller allowance |
| `max_voice_add_edits` | A lifetime cap on add/edit operations on some plans |

What consumes a slot, and what does not:

| | Slot? |
|---|---|
| Instant clone | ✅ one each |
| Professional clone | ✅ one each, from the professional allowance |
| Premade voice from the library | ❌ free to use, unlimited |
| **Registering a voice here** | ❌ local only |
| Generating audio | ❌ costs characters, not slots |

When you are full:

```bash
narrate voice list --mine          # what is occupying them
narrate voice remove <voice_id>    # frees one, on the provider
```

> 🗑️ `voice remove` is **irreversible on the provider's side**, and deliberately
> narrow in effect: takes already generated with that voice keep working. The
> audio is on disk here and the ledger rows stay, so an old master remains
> explicable. What stops is generating anything *new* in that voice.
>
> If you registered a name for it, `narrate voice forget` drops the name too —
> though leaving it is harmless and keeps old exports readable.

> ⚠️ `max_voice_add_edits` is a **lifetime** counter on some plans. Deleting a
> voice frees its slot but does not refund the add. Cloning repeatedly to
> experiment can exhaust it, so clone deliberately.

---

## 5️⃣ Two performances of one episode

The reason to bother with a clone: hearing your script in your own voice
alongside the stock one, and keeping both.

```bash
narrate generate 1 --go                       # version one
narrate project set "My Channel" --voice mine
narrate generate 1 --go --force               # version two, same script

narrate takes 1                               # both takes per chunk, with voices
narrate export 1 --voice brian                # -> Ep__brian.wav
narrate export 1 --voice mine                 # -> Ep__mine.wav
```

Each variant writes to **its own folder**, so neither overwrites the other. The
Media page lists them with their coverage and an export button each.

**Nothing extra is stored to make this work.** A take has always recorded the
voice that produced it, so a variant is only a different way of *choosing*
between takes you already have.

Two things worth knowing:

- 🔊 **Effect cues are reused, not re-charged.** The second run regenerates the
  narration and reuses every cue, so it costs less than the first.
- 🕳️ **A partial variant will not export.** A voice covering fewer chunks than
  the script has would ship a master with a silent gap, so it is refused — and
  the message names the missing lines and the command that fills them:

  ```text
  Voice mine has no take for chunks [2, 3].
  Generate them in that voice first:  narrate generate 1 --only 2,3 --go
  ```

---

## 6️⃣ Choosing between them

Once you have two masters, `narrate cost report --script 1` shows what each cost
and the waste ratio across both. Promote the take you prefer per chunk with
`narrate cut set`, and the default `narrate export 1` follows the cut — so you
can mix: your voice for the narration, the stock voice for a section you would
rather not read yourself.

---

## 🚧 Not supported

- **Voice design** — generating a voice from a text description. Clone or use a
  premade voice instead.
- **Professional voice cloning** — only instant cloning is wired up. PVC needs a
  Creator plan and a different endpoint.
- **Sharing or publishing voices** to the ElevenLabs library.

---

## 📚 See also

- **[FEATURES.md](FEATURES.md)** — everything the tool does
- **[INSTALL.md](INSTALL.md)** — setup and database provisioning
- **[../README.md](../README.md)** — the tour
