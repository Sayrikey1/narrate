"""Locating and safely serving the local media tree.

Everything the tool generates lives under `settings.assets_dir`:

    assets/<Project>/script-<n>/chunk-003-take-02.mp3   takes
    assets/<Project>/effects/<slug>.mp3                 the effect library
    assets/exports/script-<n>/                          deliverables + plan.md

**`resolve_media_path` is a security boundary, not a convenience.** Serving a
file named by the caller is a directory-traversal hole unless it is closed
deliberately — `?path=../../.env` would otherwise hand over the API key. Every
path is resolved and rejected unless it lies inside the media root, and that
check lives here alone so there is one thing to get right and one thing to
test.
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from narrate.db.models import Chunk, Cut, Effect, Export, Project, Script, Take
from narrate.effects import placements
from narrate.settings import Settings

AUDIO_SUFFIXES = frozenset(
    {".mp3", ".wav", ".opus", ".pcm", ".flac", ".m4a", ".aac", ".mp4", ".ogg", ".alac"}
)

# `mimetypes` gets these wrong for audio-only files: `.m4a` comes back as
# `audio/mp4a-latm` and `.mp4` as `video/mp4`, and a browser will play neither
# as audio. Both are AAC in an MP4 container, which is `audio/mp4`.
MEDIA_TYPE_OVERRIDES = {
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
}


class OutsideMediaRoot(PermissionError):
    """The requested path is not inside the media directory."""


def resolve_media_path(candidate: str | Path, settings: Settings) -> Path:
    """Resolve a caller-supplied path, refusing anything outside the media root.

    Both arms matter: `..` segments are collapsed by `resolve()`, and an
    absolute path pointing elsewhere is caught by the containment check.
    Symlinks are resolved too, so a link inside the tree cannot point out of it.
    """
    root = Path(settings.assets_dir).resolve()
    path = Path(candidate)
    resolved = (path if path.is_absolute() else root / path).resolve()

    if resolved != root and root not in resolved.parents:
        raise OutsideMediaRoot(f"{candidate!r} is outside the media directory")
    return resolved


def media_type(path: Path) -> str:
    """The type to serve a file as, correcting what `mimetypes` gets wrong."""
    suffix = path.suffix.lower()
    if suffix in MEDIA_TYPE_OVERRIDES:
        return MEDIA_TYPE_OVERRIDES[suffix]
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed:
        return guessed
    return "audio/mpeg" if suffix in AUDIO_SUFFIXES else "application/octet-stream"


@dataclass(frozen=True)
class MediaFile:
    """One artifact on disk, addressable by the API."""

    name: str
    path: str
    kind: str
    bytes: int
    label: str = ""
    duration_s: float | None = None
    playable: bool = True
    take_id: int | None = None
    effect_id: int | None = None
    script_id: int | None = None

    @classmethod
    def of(cls, path: Path, root: Path, kind: str, **extra: object) -> MediaFile:
        return cls(
            name=path.name,
            # Relative to the media root: an absolute path in a URL invites
            # exactly the traversal the resolver exists to refuse.
            path=str(path.relative_to(root)),
            kind=kind,
            bytes=path.stat().st_size,
            playable=path.suffix.lower() in AUDIO_SUFFIXES,
            **extra,  # type: ignore[arg-type]
        )


@dataclass
class MediaGroup:
    title: str
    files: list[MediaFile] = field(default_factory=list)

    @property
    def bytes(self) -> int:
        return sum(f.bytes for f in self.files)


@dataclass
class ProjectMedia:
    project_id: int
    project_name: str
    exports: list[MediaGroup] = field(default_factory=list)
    effects: MediaGroup = field(default_factory=lambda: MediaGroup("Effects"))
    takes: MediaGroup = field(default_factory=lambda: MediaGroup("Takes"))

    @property
    def total_bytes(self) -> int:
        return sum(g.bytes for g in self.exports) + self.effects.bytes + self.takes.bytes

    @property
    def file_count(self) -> int:
        return (
            sum(len(g.files) for g in self.exports)
            + len(self.effects.files)
            + len(self.takes.files)
        )


def export_dir(session: Session, script_id: int) -> Path | None:
    """Where a script's export was written.

    Derived from the master's recorded path rather than rebuilt from settings,
    so an export written to a custom `--out` is still found.
    """
    path = session.scalar(
        select(Export.path)
        .where(Export.script_id == script_id)
        .order_by(Export.created_at.desc())
        .limit(1)
    )
    return Path(path).parent if path else None


def project_media(session: Session, project_id: int, settings: Settings) -> ProjectMedia:
    """Every artifact belonging to one project, grouped for browsing."""
    project = session.get(Project, project_id)
    if project is None:
        raise ValueError(f"No project with id {project_id}")

    root = Path(settings.assets_dir).resolve()
    media = ProjectMedia(project_id=project_id, project_name=project.name)

    scripts = list(session.scalars(select(Script).where(Script.project_id == project_id)).all())

    for script in scripts:
        directory = export_dir(session, script.id)
        if directory is None or not directory.is_dir():
            continue
        group = MediaGroup(title=f"{script.title} — export")
        # Masters first, then the timeline-named pieces in chronological order
        # (their names sort that way by construction), then the plan.
        for path in sorted(directory.iterdir(), key=lambda p: (p.suffix != ".wav", p.name)):
            if not path.is_file() or path.name.startswith("_"):
                continue
            group.files.append(MediaFile.of(path, root, kind="export", script_id=script.id))
        if group.files:
            media.exports.append(group)

    for effect in session.scalars(
        select(Effect).where(Effect.project_id == project_id, Effect.status == "succeeded")
    ).all():
        if not effect.asset_path:
            continue
        path = Path(effect.asset_path)
        if not path.exists():
            continue
        uses = len(placements(session, effect.id))
        media.effects.files.append(
            MediaFile.of(
                path,
                root,
                kind="effect",
                effect_id=effect.id,
                duration_s=effect.actual_duration_s,
                label=f"{effect.slug} · {uses} placement{'' if uses == 1 else 's'}",
            )
        )

    script_ids = [s.id for s in scripts]
    if script_ids:
        selected = {
            c.take_id
            for c in session.scalars(select(Cut).where(Cut.script_id.in_(script_ids))).all()
        }
        rows = session.execute(
            select(Take, Chunk.ordinal, Chunk.script_id)
            .join(Chunk, Chunk.id == Take.chunk_id)
            .where(Chunk.script_id.in_(script_ids), Take.status == "succeeded")
            .order_by(Chunk.script_id, Chunk.ordinal, Take.ordinal)
        ).all()
        for take, ordinal, script_id in rows:
            if not take.asset_path:
                continue
            path = Path(take.asset_path)
            if not path.exists():
                continue
            in_cut = take.id in selected
            media.takes.files.append(
                MediaFile.of(
                    path,
                    root,
                    kind="take",
                    take_id=take.id,
                    script_id=script_id,
                    duration_s=take.duration_s,
                    label=(
                        f"chunk {ordinal} · take {take.ordinal}{' · in the cut' if in_cut else ''}"
                    ),
                )
            )

    return media


def disk_usage(settings: Settings) -> tuple[int, int]:
    """`(files, bytes)` under the media root, for `doctor` and `narrate media`."""
    root = Path(settings.assets_dir)
    if not root.is_dir():
        return 0, 0
    files = [p for p in root.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:,.0f} {unit}" if unit == "B" else f"{value:,.1f} {unit}"
        value /= 1024
    return f"{value:,.1f} GB"
