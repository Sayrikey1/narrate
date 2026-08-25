"""Local handles for voices, so one can be reused without its id.

A voice lives on the provider. This module stores no audio and grants no access
— it adds a **name**: `--voice my-voice` rather than
`--voice 21m00Tcm4TlvDq8ikWAM`.

That is a small thing for a premade voice you pick once in a UI, and the
difference between usable and not for a cloned one. A clone is a voice you made,
will use across several projects, and will refer to from a terminal — and its id
is twenty random characters that look exactly like the twenty random characters
of every other voice you cloned.

**Registration is deliberately not creation.** A voice cloned in ElevenLabs'
own interface, a professional voice, a premade one you keep coming back to — all
can be registered here. The tool never needs to have created a voice to give it
a name, which matters because an account may be permitted to *use* cloning
without the API being permitted to *perform* it.

Resolution is one-way and forgiving: anything that is not a known handle is
passed through untouched, so every existing `--voice <id>` keeps working and no
id can be shadowed by a name that happens to look like it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from narrate.db.models import RegisteredVoice

# A handle is what gets typed, so it is restricted to what is comfortable to
# type — lowercase, digits, hyphens. Long enough to be descriptive, short enough
# to beat pasting an id.
#
# (That dash is not a style choice. A comment beginning `# type:` is a *type
# comment* to mypy, which then fails to parse the file with "Invalid syntax" and
# points at the line after it.)
_SLUG_OK = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# Provider ids are opaque but consistently shaped. A handle that could be
# mistaken for one is refused, so `--voice` never has to guess which it was
# handed.
_LOOKS_LIKE_ID = re.compile(r"^[A-Za-z0-9]{20,}$")


class VoiceNameTaken(ValueError):
    """That handle already points at a different voice."""


class VoiceAlreadyRegistered(ValueError):
    """That voice id already has a handle."""


class BadVoiceName(ValueError):
    """The handle is not usable as one."""


class UnknownVoice(KeyError):
    """No registered voice by that handle."""


@dataclass(frozen=True)
class VoiceRecord:
    """A registered voice, detached from the session."""

    slug: str
    label: str
    voice_id: str
    category: str
    note: str
    verified: bool

    @property
    def is_clone(self) -> bool:
        return self.category in ("cloned", "professional")


def slugify(name: str) -> str:
    """Turn a label into a handle.

    Applied to the provider's own voice name when no handle is given, which is
    what makes registration a one-argument command most of the time.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:64] or "voice"


def validate_slug(slug: str) -> str:
    """Check a handle is usable, and say precisely why not when it is not.

    `slugify` runs first in `register`, so awkward input is *normalised* rather
    than refused — `--name "My Voice"` becoming `my-voice` is what somebody
    wants, not an error about slug rules. That leaves the id-shaped case as the
    only rejection that fires on that path; the shape check below is a backstop
    for callers that pass a handle straight in.
    """
    if not slug:
        raise BadVoiceName("A name is required.")
    if _LOOKS_LIKE_ID.match(slug):
        raise BadVoiceName(
            f"{slug!r} looks like a voice id rather than a name. A handle exists so you "
            "do not have to type the id — pick something you would say out loud."
        )
    if not _SLUG_OK.match(slug):
        raise BadVoiceName(
            f"{slug!r} is not usable as a name. Use lowercase letters, digits and "
            "hyphens, starting with a letter or digit — for example `my-voice`."
        )
    return slug


def register(
    session: Session,
    voice_id: str,
    *,
    name: str | None = None,
    label: str | None = None,
    category: str = "",
    note: str = "",
    verified: bool = False,
    replace: bool = False,
) -> RegisteredVoice:
    """Give a voice a local handle.

    `replace` repoints an existing handle at a different voice, which is the
    one destructive thing here and so is never the default: silently moving a
    name would change what every future `--voice my-voice` generates, and the
    charge would land before anybody noticed.
    """
    slug = validate_slug(slugify(name) if name else slugify(label or voice_id))

    existing_slug = session.scalars(
        select(RegisteredVoice).where(RegisteredVoice.slug == slug)
    ).one_or_none()
    existing_id = session.scalars(
        select(RegisteredVoice).where(RegisteredVoice.voice_id == voice_id)
    ).one_or_none()

    if existing_slug is not None and existing_slug.voice_id != voice_id and not replace:
        raise VoiceNameTaken(
            f"{slug!r} already points at {existing_slug.voice_id}. Pick another name, "
            "or pass --replace to repoint it."
        )
    if existing_id is not None and existing_id.slug != slug and not replace:
        raise VoiceAlreadyRegistered(
            f"{voice_id} is already registered as {existing_id.slug!r}. "
            "Use that name, or pass --replace to rename it."
        )

    row = existing_slug or existing_id
    if row is None:
        row = RegisteredVoice(slug=slug, label=label or slug, voice_id=voice_id)
        session.add(row)

    row.slug = slug
    row.voice_id = voice_id
    row.label = label or row.label or slug
    row.category = category or row.category
    if note:
        row.note = note
    row.verified_at = datetime.now(UTC).replace(tzinfo=None) if verified else row.verified_at
    session.flush()
    return row


def forget(session: Session, name: str) -> RegisteredVoice:
    """Drop a handle. The voice itself is untouched on the provider."""
    row = session.scalars(
        select(RegisteredVoice).where(RegisteredVoice.slug == slugify(name))
    ).one_or_none()
    if row is None:
        raise UnknownVoice(name)
    session.delete(row)
    session.flush()
    return row


def all_voices(session: Session) -> list[VoiceRecord]:
    rows = session.scalars(select(RegisteredVoice).order_by(RegisteredVoice.slug)).all()
    return [
        VoiceRecord(
            slug=r.slug,
            label=r.label,
            voice_id=r.voice_id,
            category=r.category,
            note=r.note,
            verified=r.verified_at is not None,
        )
        for r in rows
    ]


def resolve(session: Session, value: str | None) -> str | None:
    """Turn a handle into a voice id, passing anything else through.

    Deliberately forgiving. Every `--voice <id>` written before handles existed
    has to keep working, and an id must never be shadowed by a name — so an
    unknown value is returned as given rather than raising. The failure that
    matters (a voice that does not exist) is the provider's to report, and it
    reports it better than a guess here would.
    """
    if not value:
        return value
    row = session.scalars(
        select(RegisteredVoice).where(RegisteredVoice.slug == value.strip().lower())
    ).one_or_none()
    return row.voice_id if row is not None else value


def resolve_required(session: Session, value: str) -> str:
    """`resolve` for a value that cannot be absent.

    Separate rather than a cast at each call site: `voice_id` on a cast member
    is non-nullable, and threading `str | None` into it would need an assertion
    every time.
    """
    resolved = resolve(session, value)
    assert resolved is not None  # `value` is non-empty, so `resolve` returns a str
    return resolved


def label_for(session: Session, voice_id: str) -> str | None:
    """The handle for a voice id, if it has one. For readable output."""
    row = session.scalars(
        select(RegisteredVoice).where(RegisteredVoice.voice_id == voice_id)
    ).one_or_none()
    return row.slug if row is not None else None
