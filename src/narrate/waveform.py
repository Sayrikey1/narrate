"""Waveforms for the player, the media library and the effects, cached.

Peaks are worked out once per file and kept in narrate's own assets folder —
never beside the file, which may sit in a folder of the user's own — keyed by
the file's path, size and modification time, so a file written again is
measured again.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from narrate import audio
from narrate.settings import Settings
from narrate.timeline import Timeline

MIN_BUCKETS = 4
MAX_BUCKETS = 400
# Part of the cache key: changing how a bar is measured measures every file again.
METHOD = "rms-1"


def cache_dir(settings: Settings) -> Path:
    return Path(settings.assets_dir) / ".cache" / "peaks"


def peaks_for(path: Path, buckets: int, settings: Settings) -> list[float]:
    """`buckets` peaks for one file, from the cache when it has them."""
    buckets = max(MIN_BUCKETS, min(buckets, MAX_BUCKETS))
    stat = path.stat()
    key = hashlib.sha1(
        f"{METHOD}|{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{buckets}".encode()
    ).hexdigest()
    cached = cache_dir(settings) / f"{key}.json"
    if cached.is_file():
        try:
            values = json.loads(cached.read_text())
            if isinstance(values, list) and len(values) == buckets:
                return [float(v) for v in values]
        except (OSError, ValueError):
            pass
    values = audio.peaks(path, buckets)
    cached.parent.mkdir(parents=True, exist_ok=True)
    partial = cached.with_suffix(".partial")
    partial.write_text(json.dumps(values))
    partial.replace(cached)
    return values


def episode_peaks(timeline: Timeline, buckets: int, settings: Settings) -> list[float]:
    """The narration track as one waveform: each chunk's share of the bars is
    its share of the runtime, and the gaps between chunks are silence. A chunk
    with no audio yet is silence too."""
    buckets = max(MIN_BUCKETS, min(buckets, MAX_BUCKETS))
    runtime = timeline.runtime_s
    if runtime <= 0:
        return [0.0] * buckets
    out = [0.0] * buckets
    for entry in timeline.narration:
        start = int(entry.start_s / runtime * buckets)
        end = max(start + 1, int(entry.end_s / runtime * buckets))
        end = min(end, buckets)
        if entry.source_path is None or not entry.source_path.is_file() or start >= buckets:
            continue
        out[start:end] = peaks_for(entry.source_path, max(MIN_BUCKETS, end - start), settings)[
            : end - start
        ]
    return out
