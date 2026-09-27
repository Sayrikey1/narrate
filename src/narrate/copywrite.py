"""Titles, descriptions, tags and thumbnail briefs, via Groq.

The three taxonomies below — nine title formulas, twelve thumbnail compositions,
seven psychology principles — are not invented here. They are transcribed from a
YouTube-automation course document, and written out as literal tuples because the
compositions exist in it only as images and cannot be read out of it
programmatically. `docs/PUBLISHING.md` is the reference for them; here they are
the vocabulary a schema enumerates.

Two things follow from enumerating them rather than describing them in prose:

* **The model cannot answer off-taxonomy.** `formula` and `archetype` are
  `enum` fields under strict decoding, so "some other kind of title" is not a
  possible reply. A free-text field would have produced a different name for the
  same nine formulas every run, and the whole point of recording which formula a
  title uses is being able to count them across a channel.
* **The five-word rule is structural.** `overlay_text` is an *array* with
  `maxItems: 5`, not a string with an instruction to keep it short. Constrained
  decoding enforces it; nothing here has to check afterwards and nothing has to
  decide what to do about a six-word answer.

**Nothing generated here is chosen.** Titles and briefs land `accepted=False`,
the same convention `EffectSlot` uses, for the same reason: what ships should be
a decision somebody made.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from narrate.llm import GroqError, TokenUsage, chat_json, client_for, resolve_model
from narrate.settings import Settings

# The nine title formulas, each with the shape it takes.
FORMULAS: tuple[tuple[str, str], ...] = (
    ("Superlative", "The best, worst, biggest or first of something."),
    ("Curiosity Gap", "Names a thing but withholds the answer: what nobody tells you about X."),
    ("Fear or Urgency", "A cost of not knowing, or a window that is closing."),
    ("Direct Address", "Speaks to the viewer about themselves: you are doing X wrong."),
    ("Contrast", "Two things that should not sit together, or a paradox."),
    ("Emotion", "Leads with a feeling — brutal, beautiful, humiliating, insane."),
    ("Concise", "Four or five plain words that state the subject and nothing else."),
    ("Bracketed", "A plain title with a qualifier appended: (Explained), — The Truth."),
    ("Clear Promise", "States exactly what the viewer will be able to do afterwards."),
)

# The twelve thumbnail compositions.
ARCHETYPES: tuple[str, ...] = (
    "Face First",
    "Two Faces",
    "Face and Object",
    "Object First",
    "Action",
    "Perspective",
    "Organized Clutter",
    "Colors",
    "Two-panel",
    "Three-panel",
    "Text to Amplify",
    "Clipart",
)

# The seven principles a thumbnail is judged against.
PRINCIPLES: tuple[str, ...] = (
    "curiosity",
    "emotion",
    "clarity",
    "high contrast",
    "minimal text",
    "authority",
    "mini-story",
)

# The published guidance: three to five words, and never more.
MAX_OVERLAY_WORDS = 5

# Where YouTube truncates a title where it is read.
TITLE_TARGET_CHARS = 60

_FORMULA_NAMES = tuple(name for name, _ in FORMULAS)


TITLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["titles"],
    "properties": {
        "titles": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "formula", "reason"],
                "properties": {
                    "text": {
                        "type": "string",
                        "description": (
                            f"The title, ideally under {TITLE_TARGET_CHARS} characters so "
                            "it is not cut off. No quotation marks around it."
                        ),
                    },
                    "formula": {
                        "type": "string",
                        "enum": list(_FORMULA_NAMES),
                        "description": "Which formula this title is an instance of.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "One short sentence on why this earns the click.",
                    },
                },
            },
        }
    },
}

PACKAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["description", "tags"],
    "properties": {
        "description": {
            "type": "string",
            "description": (
                "Two or three short paragraphs. The first two lines matter most — "
                "they are all that shows before 'more'. No chapter list, no links, "
                "no hashtags: those are added separately."
            ),
        },
        "tags": {
            "type": "array",
            "minItems": 5,
            "maxItems": 20,
            "items": {
                "type": "string",
                "description": "A search term someone would actually type. Lowercase.",
            },
        },
    },
}

THUMBNAIL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["briefs"],
    "properties": {
        "briefs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "archetype",
                    "overlay_words",
                    "subject",
                    "contrast",
                    "principles",
                    "reason",
                ],
                "properties": {
                    "archetype": {
                        "type": "string",
                        "enum": list(ARCHETYPES),
                        "description": "The composition to use.",
                    },
                    "overlay_words": {
                        "type": "array",
                        # The five-word rule, enforced by decoding rather than
                        # by checking afterwards.
                        "minItems": 1,
                        "maxItems": MAX_OVERLAY_WORDS,
                        "items": {"type": "string"},
                        "description": (
                            "The words on the image, one per array entry. Three to five "
                            "at most — it has to be readable at the size of a fingernail."
                        ),
                    },
                    "subject": {
                        "type": "string",
                        "description": (
                            "What is in frame, concretely enough to shoot or generate: "
                            "who, doing what, lit how."
                        ),
                    },
                    "contrast": {
                        "type": "string",
                        "description": "The colour or tonal contrast that makes it pop.",
                    },
                    "principles": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 4,
                        "items": {"type": "string", "enum": list(PRINCIPLES)},
                    },
                    "reason": {
                        "type": "string",
                        "description": "One sentence on why it earns the click.",
                    },
                },
            },
        }
    },
}


def _formula_list() -> str:
    return "\n".join(f"- **{name}** — {shape}" for name, shape in FORMULAS)


TITLE_PROMPT = f"""You are titling a YouTube video. You are given the opening of
its script and a summary of what it covers.

Propose {{count}} titles, spread across these formulas rather than clustered on one:

{_formula_list()}

Rules:
- Under {TITLE_TARGET_CHARS} characters where you can. Longer titles get cut off
  exactly where somebody is reading them.
- Describe what the video actually contains. A title the video does not pay off
  costs more in watch time than it gains in clicks.
- No clickbait that the script does not support, and no invented numbers.
- Do not use quotation marks around the title.
"""

PACKAGE_PROMPT = """You are writing the description and tags for a YouTube video,
from its script.

The description's first two lines are all that shows before "more", so put the
substance there. Two or three short paragraphs total. Do not write a chapter
list, links, hashtags or a subscribe line — those are added separately and will
be duplicated if you include them.

Tags are search terms somebody would type, lowercase, specific to this episode
and its subject. Not a list of synonyms for the same phrase.
"""

THUMBNAIL_PROMPT = f"""You are briefing a thumbnail artist for a YouTube video.
You produce a brief, not an image.

Propose {{count}} distinct concepts, each using a different composition.

The seven things a thumbnail is judged on: curiosity, emotion, clarity, high
contrast, minimal text, authority, and telling a small story in one frame.

Rules:
- At most {MAX_OVERLAY_WORDS} words on the image. Fewer is better. The text is
  read at the size of a fingernail.
- The text on the image must not repeat the title. Together they should say more
  than either does alone.
- `subject` has to be concrete enough to act on: name who or what is in frame,
  what they are doing, and how it is lit.
- Do not describe text that explains the image. The image carries it.
"""


@dataclass(frozen=True)
class TitleIdea:
    text: str
    formula: str
    reason: str


@dataclass(frozen=True)
class BriefIdea:
    archetype: str
    overlay_text: str
    subject: str
    contrast: str
    principles: str
    reason: str


@dataclass
class CopyRun:
    """What one round of copywriting produced, and what it cost.

    Mutable and accumulating, like `SuggestionRun`: several requests make up a
    run, a failing one is recorded rather than raised, and the charge is the sum
    of whatever was actually spent.
    """

    titles: list[TitleIdea] = field(default_factory=list)
    briefs: list[BriefIdea] = field(default_factory=list)
    description: str = ""
    tags: tuple[str, ...] = ()
    usage: TokenUsage = field(default_factory=TokenUsage)
    errors: list[str] = field(default_factory=list)

    @property
    def cost_micros(self) -> int:
        return self.usage.cost_micros

    @property
    def model(self) -> str:
        return self.usage.model


async def propose_copy(
    brief: str,
    settings: Settings,
    *,
    model: str | None = None,
    titles: int = 6,
    want_package: bool = True,
    client: httpx.AsyncClient | None = None,
) -> CopyRun:
    """Propose titles, and optionally a description and tags.

    `brief` is what the episode is about — in practice the opening of the script
    plus a little of the rest, assembled by the caller, because the whole episode
    would exceed the free tier's per-minute token budget on its own.

    Two requests at most, sequentially. Titles and the description want different
    instructions and different temperatures, and splitting them means a failure
    in one still delivers the other.
    """
    chosen = resolve_model(model, settings)
    run = CopyRun(usage=TokenUsage(model=chosen))

    owns_client = client is None
    http = client or client_for(settings)
    try:
        try:
            content, usage = await chat_json(
                http,
                chosen,
                system=TITLE_PROMPT.format(count=titles),
                user=brief,
                schema=TITLE_SCHEMA,
                name="title_candidates",
                # Higher than everything else here on purpose: nine formulas at
                # 0.4 produce six rephrasings of one idea.
                temperature=0.9,
            )
            run.usage += usage
            for item in content.get("titles", [])[:titles]:
                text = " ".join(str(item["text"]).split()).strip("\"'")
                if text:
                    run.titles.append(
                        TitleIdea(
                            text=text,
                            formula=str(item["formula"]),
                            reason=str(item["reason"]).strip(),
                        )
                    )
        except (httpx.HTTPError, GroqError) as exc:
            run.errors.append(f"titles: {exc}")

        if want_package:
            try:
                content, usage = await chat_json(
                    http,
                    chosen,
                    system=PACKAGE_PROMPT,
                    user=brief,
                    schema=PACKAGE_SCHEMA,
                    name="episode_package",
                )
                run.usage += usage
                run.description = str(content.get("description", "")).strip()
                run.tags = tuple(
                    " ".join(str(t).split()).lower()
                    for t in content.get("tags", [])
                    if str(t).strip()
                )
            except (httpx.HTTPError, GroqError) as exc:
                run.errors.append(f"description: {exc}")
    finally:
        if owns_client:
            await http.aclose()

    return run


async def propose_thumbnails(
    brief: str,
    settings: Settings,
    *,
    model: str | None = None,
    count: int = 3,
    client: httpx.AsyncClient | None = None,
) -> CopyRun:
    """Propose thumbnail briefs. No image is generated."""
    chosen = resolve_model(model, settings)
    run = CopyRun(usage=TokenUsage(model=chosen))

    owns_client = client is None
    http = client or client_for(settings)
    try:
        content, usage = await chat_json(
            http,
            chosen,
            system=THUMBNAIL_PROMPT.format(count=count),
            user=brief,
            schema=THUMBNAIL_SCHEMA,
            name="thumbnail_briefs",
            temperature=0.8,
        )
        run.usage += usage
        for item in content.get("briefs", [])[:count]:
            words = [str(w).strip() for w in item["overlay_words"] if str(w).strip()]
            run.briefs.append(
                BriefIdea(
                    archetype=str(item["archetype"]),
                    overlay_text=" ".join(words[:MAX_OVERLAY_WORDS]),
                    subject=str(item["subject"]).strip(),
                    contrast=str(item["contrast"]).strip(),
                    principles=", ".join(str(p) for p in item["principles"]),
                    reason=str(item["reason"]).strip(),
                )
            )
    except (httpx.HTTPError, GroqError) as exc:
        run.errors.append(str(exc))
    finally:
        if owns_client:
            await http.aclose()

    return run
