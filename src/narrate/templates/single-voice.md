<!--
==============================================================================
 NARRATION SCRIPT — single voice
==============================================================================

 Text wrapped in HTML comment markers is stripped before anything is sent to
 the provider, so it is never spoken and never billed. You can leave every
 comment in this file exactly where it is and generate from it as-is.

 One catch, and it is the only one: do not type a closing comment marker
 inside a comment. It ends the comment right there — as HTML says it should —
 and everything after it becomes narration.

 THE THREE RULES

 1. Write prose, not a screenplay. Paragraph breaks are where the tool
    prefers to split, so a blank line between paragraphs is the single most
    useful thing you can do.
 2. Markers go in square brackets. They are removed before the request, so a
    marker is never read aloud.
 3. A heading (`# Like this`) is dropped entirely. It labels the document for
    you; narrating it would announce the episode's title before the episode.

 CHECK BEFORE YOU SPEND
     narrate script add my-episode.md --project "My Channel"
     narrate chunk review 1        # per-chunk characters and cost
     narrate estimate 1            # the total, before anything is generated
==============================================================================
-->

# Why Plane Windows Are Round

<!--
 The title above is dropped from the narration. Keep it — it names the file in
 the UI and in `narrate script list`.

 -----------------------------------------------------------------------------
 [@ MM:SS] — a TARGET start time, not a command.
 -----------------------------------------------------------------------------
 Speech duration cannot be dialled to a mark, so the tool reports how far the
 section actually landed from your target and leaves the decision to you. Use
 these where the video has something happening at a known moment.

 `[@ 00:00]` at the top is optional. It reads as "this is the beginning".
-->

[@ 00:00]

Look out of an aeroplane window and you are looking through a shape that took
four fatal crashes to arrive at. The windows are round because square ones
killed people.

<!--
 -----------------------------------------------------------------------------
 [SFX: description] — an effect cue.
 -----------------------------------------------------------------------------
 Two things happen when you write one:

   * A cue is queued for generation. It is NOT generated until you accept it,
     and the same description used twice is generated once and placed twice —
     repeats cost nothing.
   * A chunk boundary is forced here. That is the point: a cue at a chunk edge
     gets an exact timeline position, measured from the takes before it,
     rather than an interpolated guess.

 Duration is optional — `[SFX: wind howling]` lets the provider choose. When
 you give one it must be between 0.5 and 30 seconds.
 Add `loop` for a bed you intend to stretch: `[SFX: soft rain, 30s, loop]`

 Effects are OVERLAYS. They carry a timeline position but are not mixed into
 the narration master, so an editor drops them on their own track.
-->

[SFX: distant jet engine, steady drone, 6s]

In 1954, two de Havilland Comets broke apart in mid-air within four months of
each other. The Comet was the first commercial jet airliner, and it was
beautiful, and its windows were square.

<!--
 Keep paragraphs to a few sentences. The chunker splits on paragraph breaks
 first, and a chunk is one request in one voice — so paragraph shape is what
 you actually have control over.
-->

[@ 00:40]

The problem is a thing called stress concentration. Push on a sheet of metal
and the force spreads out evenly, until it meets a corner. At a sharp corner
the force has nowhere to go but into the corner itself.

[SFX: metal groaning under strain, 4s]

A round window has no corners. The stress runs around the curve and keeps
going, spread across the whole frame instead of piling into four points.

<!--
 -----------------------------------------------------------------------------
 THINGS THAT ARE NOT MARKERS
 -----------------------------------------------------------------------------
 Markdown formatting is stripped and its words are kept, so you can write
 **bold**, *italic*, `code` and [links](https://example.com) freely — the
 syntax goes, the words stay.

 A colon in ordinary prose stays ordinary prose. `Note:`, `Warning:` and
 `12:30` are never mistaken for a speaker, because a speaker prefix only
 counts when the name has been cast. See the multi-voice template.

 -----------------------------------------------------------------------------
 AUDIO TAGS — only on eleven_v3
 -----------------------------------------------------------------------------
 On v3 a bracketed phrase in the text is interpreted as delivery direction,
 not stripped:

     [whispering] The light went out. [pause] Nobody noticed.

 Those characters ARE billed, and `narrate models` tells you which models
 accept them. On any other model, delete them.
-->

Every window you have looked through since is the shape it is because of two
aircraft that came apart over the Mediterranean, and the engineers who worked
out why.

<!--
==============================================================================
 WHEN YOU ARE READY
==============================================================================
     narrate script add my-episode.md --project "My Channel"
     narrate chunk review 1        # check the split before spending
     narrate generate 1            # dry run — shows the cost, sends nothing
     narrate generate 1 --go       # narration + accepted cues, asks first
     narrate export 1              # masters, named pieces, and plan.md

 Or drop this file onto the web UI, which does the same thing.
==============================================================================
-->
