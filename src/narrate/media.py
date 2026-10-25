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
import zipfile
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
    # When the file was last written, as a Unix time: what "newest first" sorts by.
    modified: float = 0.0

    @classmethod
    def of(
        cls, path: Path, root: Path, kind: str, *, address: str | None = None, **extra: object
    ) -> MediaFile:
        stat = path.stat()
        return cls(
            name=path.name,
            # Relative to the media root: an absolute path in a URL invites
            # exactly the traversal the resolver exists to refuse. A file
            # outside the root has an `address` instead.
            path=address or str(path.relative_to(root)),
            kind=kind,
            bytes=stat.st_size,
            playable=path.suffix.lower() in AUDIO_SUFFIXES,
            modified=stat.st_mtime,
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


# An export written to `export_dir` sits outside the media root, in a folder
# beside other work — which may hold anything, this project's `.env` included.
# So it is addressed by episode and file name, never by path, and only a file
# directly inside that episode's recorded export folder can be served.
EXPORT_PREFIX = "@export/"


def resolve_export_file(session: Session, candidate: str) -> Path | None:
    """The file `@export/<script id>/<name>` stands for; None for any other path."""
    if not candidate.startswith(EXPORT_PREFIX):
        return None
    script, _, name = candidate.removeprefix(EXPORT_PREFIX).partition("/")
    if not script.isdigit() or name != Path(name).name or name.startswith("."):
        raise OutsideMediaRoot(f"{candidate!r} is not a file in an export")
    directory = export_dir(session, int(script))
    if directory is None:
        raise OutsideMediaRoot(f"Script {script} has not been exported")
    path = (directory / name).resolve()
    # Resolved, so a link inside the folder cannot point out of it.
    if path.parent != directory.resolve():
        raise OutsideMediaRoot(f"{candidate!r} is not a file in an export")
    return path


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


def archive_name(settings: Settings, directory: Path, title: str, script_id: int) -> str:
    """What an export's zip, and the folder it unzips to, are called.

    In `export_dir`, the episode folder's own name — "004" — so the zip is that
    folder, packed. Elsewhere, the episode's title, as the download always was.
    """
    if settings.export_dir is not None and (
        directory.resolve().parent == settings.export_dir.resolve()
    ):
        return directory.name
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in title).strip("-")
    return safe or f"script-{script_id}"


def write_archive(directory: Path, target: Path, folder: str) -> Path:
    """Zip an export so that it unzips as `folder/`, whatever unzips it.

    Loose files at the top of a zip land in a folder only as the tool's
    courtesy: Archive Utility makes one named after the zip, `unzip` leaves
    them wherever it ran. Written beside `target` and moved into place, so an
    interrupted save never leaves half a zip where a whole one was.
    """
    partial = target.with_name(f".{target.name}.partial")
    with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as bundle:
        for item in sorted(directory.iterdir()):
            # Working files are not deliverables, and nor is the Finder's
            # .DS_Store from somebody having opened the folder.
            if item.is_file() and not item.name.startswith(("_", ".")):
                bundle.write(item, f"{folder}/{item.name}")
    partial.replace(target)
    return target


def zip_beside(directory: Path, name: str) -> tuple[Path, bool]:
    """Pack an export folder into `<name>.zip` beside it — `004.zip` next to `004/`.

    With `export_dir` set that is the same folder for every episode, so the zips
    are kept together rather than wherever a browser saved each. A zip of the
    same name is replaced: it is that episode, packed again. Returns the zip,
    and whether one was replaced.
    """
    target = directory.parent / f"{name}.zip"
    replaced = target.exists()
    return write_archive(directory, target, name), replaced


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
        inside = directory.resolve().is_relative_to(root)
        for path in sorted(directory.iterdir(), key=lambda p: (p.suffix != ".wav", p.name)):
            if not path.is_file() or path.name.startswith("_"):
                continue
            address = None if inside else f"{EXPORT_PREFIX}{script.id}/{path.name}"
            group.files.append(
                MediaFile.of(path, root, kind="export", script_id=script.id, address=address)
            )
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
