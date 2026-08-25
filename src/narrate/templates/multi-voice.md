<!--
==============================================================================
 NARRATION SCRIPT — several voices
==============================================================================

 Text wrapped in HTML comment markers is stripped before anything is sent.
 Never spoken, never billed. Generate from this file as-is if you like.

 One catch: do not type a closing comment marker inside a comment. It ends
 the comment there, and everything after it becomes narration.

 THE ONE RULE THAT MATTERS

     A `Name:` prefix becomes a speaker only when that name is CAST.

 Nothing else. There is no guessing from capitalisation, which is why this is
 safe to use on prose you have already written: `Note:`, `Warning:`,
 `Chapter one:` and `12:30` have no cast entry, so they stay narration. If you
 misspell a cast name, the tool tells you it left it as narration rather than
 silently doing something you did not ask for.

 TWO WAYS TO CAST

 1. Per project, so a character carries across every episode:
        narrate cast set "My Channel" Morag --voice <voice_id>
        narrate cast list "My Channel"
    Or use the Cast page in the web UI, where you can audition each voice
    before choosing it.

 2. Inline, in the script, with a [CAST] block. Useful for a one-off
    character, and it overrides the project for this script only.
==============================================================================
-->

# The Lighthouse Argument

<!--
 -----------------------------------------------------------------------------
 [CAST] — declare who speaks in this script.
 -----------------------------------------------------------------------------
 Replace the ids below with real ones from `narrate voices`. Two shapes work:

     [CAST] Morag = 21m00Tcm4TlvDq8ikWAM · Inspector = pNInz6obpgDQGcFmaJgB

 or one per line under the header, which is easier to read with a large cast:

     [CAST]
     Morag = 21m00Tcm4TlvDq8ikWAM
     Inspector = pNInz6obpgDQGcFmaJgB

 If the project is already cast, DELETE this block — the project's cast is
 used automatically and you do not need to repeat it here.
-->

[CAST]
Morag = REPLACE_WITH_A_VOICE_ID
Inspector = REPLACE_WITH_ANOTHER_VOICE_ID

<!--
 -----------------------------------------------------------------------------
 WRITING THE DIALOGUE
 -----------------------------------------------------------------------------
 Put the name, a colon, then the line. The prefix is stripped, so "Morag:" is
 never read aloud — leaving it in would have the voice say "Morag colon" and
 charge you for it.

 A blank line between turns. Each turn becomes its own chunk, so this is also
 where the tool splits.
-->

[@ 00:00]

Morag: You knew it would end. Everyone knew, and nobody said it aloud.

Inspector: I hoped not. Hoping is a different thing from knowing, and I have
had thirty years of practice at both.

<!--
 A turn can run for several paragraphs. It stays with the same speaker until
 the next `Name:` or `[VOICE: Name]`, so you do not repeat the prefix.
-->

Morag: The automated lamp arrived in a crate in March. Two men from the board
came with it, and neither of them looked at the tower.

They looked at the paperwork, and at the sea, and at their watches.

<!--
 -----------------------------------------------------------------------------
 [SFX: description] — cues work exactly as in a single-voice script.
 -----------------------------------------------------------------------------
 A cue forces a chunk boundary, which gives it an exact timeline position.
 Repeats of the same description are generated once and placed as often as you
 like. Effects are overlays: they carry a position but are not mixed into the
 master, so an editor drops them on their own track.
-->

[SFX: heavy iron door closing, echo in a stone tower, 3s]

Inspector: I signed the order. I want that on the record, because the record
is the only thing that outlasts any of us.

<!--
 -----------------------------------------------------------------------------
 [VOICE: Name] — switch speaker without starting a new paragraph.
 -----------------------------------------------------------------------------
 Use it where a `Name:` prefix would break the flow of the writing, or where
 one speaker's line continues after something else.
-->

Morag: And the light? [VOICE: Inspector] The light does not care who reads the
record. It turns, or it does not.

<!--
==============================================================================
 TWO WAYS TO PERFORM THIS — and the trade between them
==============================================================================

 A) ONE CHUNK PER TURN                                    (the default)

        narrate script add script.md -p "My Channel"

    Each turn is its own request in its own voice.
      + Works on EVERY model.
      + Ordinary per-character rate.
      + Any single line can be re-rolled on its own, and costed on its own.
      - Each line is performed without the model hearing the other side.

 B) ONE CONVERSATION PER EXCHANGE                          (--dialogue)

        narrate script add script.md -p "My Channel" --dialogue

    Consecutive turns are packed into one text-to-dialogue request, so the
    model hears the whole exchange and the timing between speakers is its own.
      + Genuinely conversational delivery.
      - eleven_v3 ONLY. Any other model falls back to (A) and says so.
      - Capped at 2,000 characters across all turns in one request — tighter
        than v3's own 5,000-character limit.
      - A re-roll re-charges the whole exchange, not one line.

 Note on price: ElevenLabs does not publish what the dialogue endpoint costs.
 The tool estimates it at the ordinary rate and flags that as unverified until
 you run `narrate probe --live --dialogue`, which measures it for a fraction of
 a cent. An estimate that is wrong by nearly double is worse than one labelled
 unsure.

==============================================================================
 CHECK BEFORE YOU SPEND
==============================================================================
     narrate cast list "My Channel"   # who is cast, and in which voice
     narrate chunk review 1           # one row per turn, with its voice
     narrate estimate 1               # the total, before anything is sent
     narrate generate 1               # dry run — sends nothing
     narrate generate 1 --go          # asks before spending
==============================================================================
-->
