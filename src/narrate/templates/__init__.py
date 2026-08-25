"""Starter scripts, annotated, that generate correctly as-is.

A template's whole job is to be a working example *and* a guide, and those pull
in opposite directions: guidance is text, and text in a script gets narrated and
billed. HTML comments resolve it — stripped by
[`script_parse`](../script_parse.py) before anything is sent, invisible when the
file is previewed as Markdown, and highlighted by every editor. So the same file
can be dense with explanation and still produce clean narration untouched.

Which is a claim worth testing rather than asserting, so
`tests/test_templates.py` parses every template shipped here and fails if a
single word of guidance reaches the narration. It caught the obvious trap on the
first run: the opening comment described the comment syntax, and the closing
delimiter inside it ended the comment early — exactly as HTML says it should —
so half the guidance became prose.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Template:
    """One starter script."""

    slug: str
    title: str
    summary: str
    filename: str

    @property
    def path(self) -> Path:
        return TEMPLATE_DIR / self.filename

    def read(self) -> str:
        return self.path.read_text(encoding="utf-8")


TEMPLATES: tuple[Template, ...] = (
    Template(
        slug="single-voice",
        title="Single voice",
        summary=(
            "One narrator. Covers paragraph shape, timeline anchors, effect cues "
            "and what is not a marker."
        ),
        filename="single-voice.md",
    ),
    Template(
        slug="multi-voice",
        title="Several voices",
        summary=(
            "A cast, speaker prefixes and mid-paragraph switches — plus the trade "
            "between one chunk per turn and one conversation per exchange."
        ),
        filename="multi-voice.md",
    ),
)


def get(slug: str) -> Template:
    """Look a template up by slug. Raises `KeyError` with the valid names."""
    for template in TEMPLATES:
        if template.slug == slug:
            return template
    raise KeyError(f"No template {slug!r}. Available: {', '.join(t.slug for t in TEMPLATES)}")


def all_templates() -> tuple[Template, ...]:
    return TEMPLATES
