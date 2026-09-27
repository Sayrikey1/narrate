"""The publish pack: what goes in the box the video ships in.

`plan.md` tells you how to cut the episode. This tells you how to upload it —
chapter markers, the retention target, title candidates, the thumbnail brief,
the description and the tags. Same shape as `plan.py` and for the same reasons:
the renderers are pure functions returning a string, so the CLI writes a file
with them, the API serves them from memory, and neither one knows how the other
works.

The part worth understanding is the chapters. A chapter has a **name** and a
**time**, and they come from opposite ends of the tool: the name is written in
the script, and the time is measured off the finished audio. Nothing here trusts
the script's own `[@ MM:SS]` targets for a timestamp — TTS duration cannot be
dialled to a mark, so the anchor says where a section was *meant* to start and
the timeline says where it does. A chapter list built from intentions would
disagree with the audio it describes, which is the one way a chapter list can be
worse than none at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from narrate.db.models import Project, Script, ThumbnailBrief, TitleCandidate
from narrate.retention import Target, targets
from narrate.script_parse import format_youtube_time
from narrate.timeline import Timeline, TimelineEntry

# YouTube's own rules for a chapter list. All three, or it shows none of them.
MIN_CHAPTERS = 3
MIN_CHAPTER_GAP_S = 10.0
FIRST_CHAPTER_S = 0.0

# Where YouTube truncates a title in most surfaces. Not a limit — a longer title
# still publishes, it just gets cut off exactly where somebody reads it.
TITLE_COMFORTABLE_CHARS = 60


@dataclass(frozen=True)
class Chapter:
    """One line of a YouTube chapter list."""

    start_s: float
    title: str
    chunk_ordinal: int | None = None

    @property
    def stamp(self) -> str:
        return format_youtube_time(self.start_s)

    @property
    def line(self) -> str:
        """The form that gets pasted into a description, verbatim."""
        return f"{self.stamp} {self.title}"


@dataclass(frozen=True)
class ChapterList:
    """Chapters, and whether they can actually be used.

    Kept together because a chapter list that breaks a rule is not an error to
    raise — the writer still wants to see what they have and what is wrong with
    it. `usable` is the single question the renderer asks.
    """

    chapters: list[Chapter]
    problems: list[str]

    @property
    def usable(self) -> bool:
        return not self.problems and len(self.chapters) >= MIN_CHAPTERS

    @property
    def text(self) -> str:
        return "\n".join(c.line for c in self.chapters)


def chapters_of(timeline: Timeline) -> ChapterList:
    """Build the chapter list, and say what would stop YouTube accepting it.

    Every rule here is YouTube's, not ours, and each one fails the whole list
    rather than the offending line — which is why they are reported together
    instead of the first one raising.
    """
    starts: list[TimelineEntry] = timeline.chapter_starts
    problems: list[str] = []

    if not starts:
        return ChapterList(
            chapters=[],
            problems=[
                "No chapters are named. Add a `## Heading`, a `[CHAPTER: ...]` marker, "
                "or a name on an anchor — `[@ 03:00 The Employee Trap]` — then re-chunk."
            ],
        )

    chapters = [
        Chapter(
            start_s=e.start_s,
            title=_tidy(e.chapter_title or ""),
            chunk_ordinal=e.chunk_ordinal,
        )
        for e in starts
    ]

    if not timeline.is_complete:
        problems.append(
            "Some chunks have no audio yet, so these timestamps are not final. "
            "A chapter list that disagrees with the video is worse than none."
        )

    if chapters[0].start_s > FIRST_CHAPTER_S:
        problems.append(
            f"The first chapter is at {chapters[0].stamp}, but YouTube requires one at "
            "0:00 and ignores the whole list without it. Name the opening section too."
        )

    if len(chapters) < MIN_CHAPTERS:
        problems.append(
            f"Only {len(chapters)} chapter(s). YouTube needs at least {MIN_CHAPTERS} "
            "before it shows any of them."
        )

    for earlier, later in pairwise(chapters):
        gap = later.start_s - earlier.start_s
        if gap < MIN_CHAPTER_GAP_S:
            problems.append(
                f"{earlier.title!r} and {later.title!r} are {gap:.1f}s apart; YouTube "
                f"requires {MIN_CHAPTER_GAP_S:.0f}s between chapters."
            )

    return ChapterList(chapters=chapters, problems=problems)


def _tidy(title: str) -> str:
    """A chapter name on one line, with no leading numbering.

    A writer numbering their own headings (`## 2. The Employee Trap`) would
    otherwise produce a list numbered twice over, since YouTube numbers them.
    """
    cleaned = " ".join(title.split())
    while cleaned[:1].isdigit() or cleaned[:1] in ".)-":
        stripped = cleaned.lstrip("0123456789").lstrip(".)- ")
        if not stripped:
            break
        cleaned = stripped
    return cleaned


@dataclass(frozen=True)
class TitleOption:
    """A title candidate, detached from the session for rendering."""

    id: int
    text: str
    formula: str
    rationale: str
    accepted: bool
    proposed: bool

    @property
    def over_length(self) -> bool:
        """Whether a search result would cut it off."""
        return len(self.text) > TITLE_COMFORTABLE_CHARS


@dataclass(frozen=True)
class BriefOption:
    """A thumbnail brief, detached from the session for rendering."""

    id: int
    archetype: str
    overlay_text: str
    subject: str
    contrast: str
    rationale: str
    principles: str
    accepted: bool

    @property
    def word_count(self) -> int:
        return len(self.overlay_text.split())


@dataclass(frozen=True)
class PublishContext:
    """Everything the pack reports that is not on the timeline.

    Frozen and session-free, exactly as `PlanContext` is: the renderers below are
    pure functions of it, so the CLI can write them to a file and the API can
    serve them from memory without either knowing about the other.
    """

    title: str = ""
    description: str = ""
    tags: tuple[str, ...] = ()
    titles: tuple[TitleOption, ...] = ()
    briefs: tuple[BriefOption, ...] = ()
    # Channel-level text, merged in for the pasted description rather than
    # stored per episode.
    boilerplate: str = ""
    project_tags: tuple[str, ...] = ()

    @property
    def chosen_title(self) -> TitleOption | None:
        return next((t for t in self.titles if t.accepted), None)

    @property
    def chosen_brief(self) -> BriefOption | None:
        return next((b for b in self.briefs if b.accepted), None)

    @property
    def all_tags(self) -> tuple[str, ...]:
        """Episode tags first, then the channel's, without repeats."""
        seen: dict[str, None] = {}
        for tag in (*self.tags, *self.project_tags):
            if tag:
                seen.setdefault(tag, None)
        return tuple(seen)


def gather_publish(session: Session, script_id: int) -> PublishContext:
    """Read everything the pack needs, and detach it from the session."""
    script = session.get(Script, script_id)
    if script is None:
        raise ValueError(f"No script {script_id}.")
    project = session.get(Project, script.project_id)

    titles = tuple(
        TitleOption(
            id=row.id,
            text=row.text,
            formula=row.formula,
            rationale=row.rationale,
            accepted=row.accepted,
            proposed=row.source == "proposed",
        )
        for row in session.scalars(
            select(TitleCandidate)
            .where(TitleCandidate.script_id == script_id)
            .order_by(TitleCandidate.ordinal, TitleCandidate.id)
        ).all()
    )
    briefs = tuple(
        BriefOption(
            id=row.id,
            archetype=row.archetype,
            overlay_text=row.overlay_text,
            subject=row.subject,
            contrast=row.contrast,
            rationale=row.rationale,
            principles=row.principles,
            accepted=row.accepted,
        )
        for row in session.scalars(
            select(ThumbnailBrief)
            .where(ThumbnailBrief.script_id == script_id)
            .order_by(ThumbnailBrief.ordinal, ThumbnailBrief.id)
        ).all()
    )
    return PublishContext(
        title=script.title,
        description=script.description,
        tags=_split_tags(script.tags),
        titles=titles,
        briefs=briefs,
        boilerplate=project.description_boilerplate if project else "",
        project_tags=_split_tags(project.default_tags) if project else (),
    )


def _split_tags(raw: str) -> tuple[str, ...]:
    return tuple(t.strip() for t in raw.split(",") if t.strip())


def render_publish_pack(timeline: Timeline, context: PublishContext | None = None) -> str:
    """Render `publish.md` — everything needed to upload the finished episode.

    Pure, like `render_plan`: a string in, a string out, no paths and no session.
    Sections that have nothing in them say what would fill them rather than being
    omitted, because a missing section reads like a bug and an empty one reads
    like an instruction.
    """
    ctx = context or PublishContext()
    chapters = chapters_of(timeline)
    target = targets(timeline.runtime_s)

    lines: list[str] = [
        f"# {timeline.script_title} — publish pack",
        "",
        _summary(timeline, ctx, target),
        "",
        "`plan.md` is for cutting the episode. This is for uploading it: every "
        "field below is meant to be copied out as it stands.",
        "",
    ]

    lines += _title_section(ctx)
    lines += _description_section(ctx, chapters)
    lines += _tags_section(ctx)
    lines += _chapter_section(chapters)
    lines += _thumbnail_section(ctx)
    lines += _retention_section(timeline, target)

    lines.append("")
    return "\n".join(lines)


def _summary(timeline: Timeline, ctx: PublishContext, target: Target) -> str:
    parts = [f"Runtime **{format_youtube_time(timeline.runtime_s)}**"]
    chapters = chapters_of(timeline)
    if chapters.chapters:
        state = "" if chapters.usable else ", unusable"
        parts.append(f"{len(chapters.chapters)} chapters{state}")
    if target.good_pct:
        parts.append(f"target {target.good_pct}% AVD")
    if ctx.titles:
        parts.append(f"{len(ctx.titles)} title candidate(s)")
    if not timeline.is_complete:
        parts.append("**incomplete**")
    return " · ".join(parts)


def _title_section(ctx: PublishContext) -> list[str]:
    lines = ["## Title", ""]
    chosen = ctx.chosen_title
    headline = chosen.text if chosen else ctx.title
    lines.append(f"**{headline}**" if headline else "*No title set.*")
    if headline and len(headline) > TITLE_COMFORTABLE_CHARS:
        lines += [
            "",
            f"> {len(headline)} characters. YouTube cuts a title off around "
            f"{TITLE_COMFORTABLE_CHARS} in most places it is read, so the end of this "
            "one will often be invisible.",
        ]

    others = [t for t in ctx.titles if not t.accepted]
    if others:
        lines += [
            "",
            "### Other candidates",
            "",
            "| # | Formula | Title | Why |",
            "|---:|---|---|---|",
        ]
        for option in others:
            flag = " ⚠️" if option.over_length else ""
            lines.append(
                f"| {option.id} | {option.formula or '—'} | {option.text}{flag} "
                f"| {option.rationale or '—'} |"
            )
        lines += ["", "Choose one with `narrate publish accept <#>`."]
    elif not ctx.titles:
        lines += [
            "",
            '*No candidates yet.* Add one with `narrate publish title <id> "..."`, or '
            "generate a set with `narrate publish draft <id> --go`.",
        ]
    return [*lines, ""]


def _description_section(ctx: PublishContext, chapters: ChapterList) -> list[str]:
    lines = ["## Description", ""]
    if not ctx.description and not ctx.boilerplate:
        lines += [
            "*Nothing written.* Set one with `narrate publish set <id> --description`, "
            "or generate one with `narrate publish draft <id> --go`.",
            "",
        ]
        return lines

    # Assembled in the order it gets pasted: the episode's own words, then the
    # chapters (which belong in the description for YouTube to find them), then
    # whatever the channel always says.
    body: list[str] = []
    if ctx.description:
        body.append(ctx.description.strip())
    if chapters.usable:
        body.append("Chapters:\n" + chapters.text)
    if ctx.boilerplate:
        body.append(ctx.boilerplate.strip())

    lines += ["```text", "\n\n".join(body), "```", ""]
    if chapters.chapters and not chapters.usable:
        lines += [
            "> The chapters are left out above because they would not be accepted. "
            "See **Chapters** below.",
            "",
        ]
    return lines


def _tags_section(ctx: PublishContext) -> list[str]:
    lines = ["## Tags", ""]
    tags = ctx.all_tags
    if not tags:
        lines += [
            "*None set.* `narrate publish set <id> --tags a,b,c`, or "
            "`narrate project set <name> --default-tags` for the ones every episode shares.",
            "",
        ]
        return lines
    lines += ["```text", ", ".join(tags), "```", ""]
    if ctx.project_tags:
        lines += [f"*{len(ctx.project_tags)} of these come from the channel's defaults.*", ""]
    return lines


def _chapter_section(chapters: ChapterList) -> list[str]:
    lines = ["## Chapters", ""]
    if chapters.chapters:
        lines += ["```text", chapters.text, "```", ""]
        lines += [
            "*Times are measured from the exported audio, not from the script's "
            "`[@ MM:SS]` anchors — those are targets, and TTS cannot be dialled to one.*",
            "",
        ]
    for problem in chapters.problems:
        lines += [f"> ⚠️ {problem}", ""]
    if not chapters.problems and chapters.chapters:
        lines += ["Paste the block above into the description as it stands.", ""]
    return lines


def _thumbnail_section(ctx: PublishContext) -> list[str]:
    lines = ["## Thumbnail", ""]
    brief = ctx.chosen_brief
    if brief is None:
        lines += [
            "*No brief yet.* Generate one with `narrate publish brief <id> --go`.",
            "",
            "No image is produced — the brief is the deliverable, and it is written to "
            "be handed to a designer or pasted into any image tool.",
            "",
        ]
        return lines

    lines += [
        "| | |",
        "|---|---|",
        f"| **Composition** | {brief.archetype or '—'} |",
        f"| **Text on image** | **{brief.overlay_text or '—'}** ({brief.word_count} words) |",
        f"| **Subject** | {brief.subject or '—'} |",
        f"| **Contrast** | {brief.contrast or '—'} |",
        f"| **Leans on** | {brief.principles or '—'} |",
        "",
    ]
    if brief.rationale:
        lines += [f"{brief.rationale}", ""]

    others = [b for b in ctx.briefs if not b.accepted]
    if others:
        lines += [
            f"*{len(others)} other brief(s) on record. Switch with `narrate publish choose <#>`.*",
            "",
        ]
    return lines


def _retention_section(timeline: Timeline, target: Target) -> list[str]:
    lines = ["## Retention target", ""]
    if not target.good_pct:
        lines += ["*Nothing generated yet, so there is no runtime to judge.*", ""]
        return lines

    lines += [
        "| | |",
        "|---|---:|",
        f"| Runtime | {format_youtube_time(target.runtime_s)} |",
        f"| Good average view | {target.good_pct}% |",
        f"| Great average view | {target.great_pct}% |",
        f"| Hold for *good* | {format_youtube_time(target.good_hold_s)} |",
        f"| Hold for *great* | {format_youtube_time(target.great_hold_s)} |",
        "",
        "*The target falls as an episode gets longer — each doubling of runtime costs "
        "five points of average percentage viewed.*",
        "",
    ]
    if target.extrapolated:
        lines += [
            "> ⚠️ Outside the published 8 to 120 minute range, so these are extrapolated "
            "rather than quoted.",
            "",
        ]
    return lines


def render_publish_json(timeline: Timeline, context: PublishContext | None = None) -> str:
    """Machine-readable twin of the pack, for the UI and anything downstream.

    The UI reads *this*, never the markdown. Re-parsing a rendered document to
    recover the facts that produced it is how a display and its source drift
    apart, and the rule is already established for `plan.md`.
    """
    ctx = context or PublishContext()
    chapters = chapters_of(timeline)
    target = targets(timeline.runtime_s)
    chosen_title = ctx.chosen_title
    chosen_brief = ctx.chosen_brief

    payload: dict[str, Any] = {
        "script_id": timeline.script_id,
        "script_title": timeline.script_title,
        "project": timeline.project_name,
        "runtime_s": round(timeline.runtime_s, 3),
        "complete": timeline.is_complete,
        "title": {
            "chosen": chosen_title.text if chosen_title else ctx.title,
            "over_length": bool(chosen_title and chosen_title.over_length),
            "candidates": [
                {
                    "id": t.id,
                    "text": t.text,
                    "formula": t.formula,
                    "rationale": t.rationale,
                    "accepted": t.accepted,
                    "proposed": t.proposed,
                    "over_length": t.over_length,
                }
                for t in ctx.titles
            ],
        },
        "description": ctx.description,
        "boilerplate": ctx.boilerplate,
        "tags": list(ctx.all_tags),
        "chapters": {
            "usable": chapters.usable,
            "problems": chapters.problems,
            "text": chapters.text,
            "entries": [
                {
                    "stamp": c.stamp,
                    "start_s": round(c.start_s, 3),
                    "title": c.title,
                    "chunk_ordinal": c.chunk_ordinal,
                }
                for c in chapters.chapters
            ],
        },
        "thumbnail": (
            {
                "id": chosen_brief.id,
                "archetype": chosen_brief.archetype,
                "overlay_text": chosen_brief.overlay_text,
                "word_count": chosen_brief.word_count,
                "subject": chosen_brief.subject,
                "contrast": chosen_brief.contrast,
                "rationale": chosen_brief.rationale,
                "principles": chosen_brief.principles,
            }
            if chosen_brief
            else None
        ),
        "briefs": [
            {
                "id": b.id,
                "archetype": b.archetype,
                "overlay_text": b.overlay_text,
                "accepted": b.accepted,
            }
            for b in ctx.briefs
        ],
        "retention": {
            "good_pct": target.good_pct,
            "great_pct": target.great_pct,
            "good_hold_s": round(target.good_hold_s, 3),
            "great_hold_s": round(target.great_hold_s, 3),
            "extrapolated": target.extrapolated,
        },
    }
    return json.dumps(payload, indent=2)


class DuplicateTitle(ValueError):
    """That title is already on record for this script."""


def add_title(
    session: Session,
    script_id: int,
    text: str,
    *,
    formula: str = "",
    rationale: str = "",
    source: str = "manual",
    accepted: bool = True,
    model_id: str = "",
) -> TitleCandidate:
    """Record a title candidate.

    `accepted` defaults to True so a title written by hand is chosen by
    construction. The LLM path passes False explicitly — the same convention
    `EffectSlot` uses, and for the same reason: a proposal should not quietly
    become the thing that ships.
    """
    cleaned = " ".join(text.split())
    if not cleaned:
        raise ValueError("A title is required.")

    clash = session.scalars(
        select(TitleCandidate).where(
            TitleCandidate.script_id == script_id, TitleCandidate.text == cleaned
        )
    ).one_or_none()
    if clash is not None:
        raise DuplicateTitle(f"{cleaned!r} is already candidate {clash.id} for this script.")

    highest = session.scalar(
        select(func.max(TitleCandidate.ordinal)).where(TitleCandidate.script_id == script_id)
    )
    row = TitleCandidate(
        script_id=script_id,
        ordinal=(highest or 0) + 1,
        text=cleaned,
        formula=formula,
        rationale=rationale,
        source=source,
        accepted=False,
        model_id=model_id,
    )
    session.add(row)
    session.flush()
    if accepted:
        choose_title(session, row)
    return row


def choose_title(session: Session, candidate: TitleCandidate) -> None:
    """Make this candidate the episode's title, and unchoose the rest.

    Writes through to `Script.title`, which is where the rest of the tool already
    reads the title from — the candidate rows stay as a record of what was
    considered. Exactly the relationship `Cut` has to `Take`.
    """
    for sibling in session.scalars(
        select(TitleCandidate).where(TitleCandidate.script_id == candidate.script_id)
    ).all():
        sibling.accepted = sibling.id == candidate.id
    script = session.get(Script, candidate.script_id)
    if script is not None:
        script.title = candidate.text
    session.flush()


def choose_brief(session: Session, brief: ThumbnailBrief) -> None:
    """Make this the brief the episode ships, and unchoose the rest."""
    for sibling in session.scalars(
        select(ThumbnailBrief).where(ThumbnailBrief.script_id == brief.script_id)
    ).all():
        sibling.accepted = sibling.id == brief.id
    session.flush()
