"""ffmpeg and ffprobe wrappers.

Plain `subprocess` rather than a wrapper library. The two things that matter
here are exact control of the filter graph and getting ffmpeg's stderr back
when something fails — a wrapper that swallows stderr turns a one-line
diagnosis ("Invalid data found when processing input") into a guessing game.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

INSTALL_HINT = "ffmpeg is not on PATH. Install it with:  brew install ffmpeg"


class FFmpegMissing(RuntimeError):
    pass


class FFmpegFailed(RuntimeError):
    def __init__(self, command: list[str], stderr: str) -> None:
        # Only the tail of stderr — ffmpeg's banner is long and the error is last.
        tail = "\n".join(stderr.strip().splitlines()[-12:])
        super().__init__(f"ffmpeg failed:\n  {' '.join(command[:6])} ...\n{tail}")
        self.command = command
        self.stderr = stderr


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def ffprobe_path() -> str | None:
    return shutil.which("ffprobe")


def require_ffmpeg() -> str:
    path = ffmpeg_path()
    if path is None:
        raise FFmpegMissing(INSTALL_HINT)
    return path


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise FFmpegFailed(command, result.stderr)
    return result


def duration_seconds(path: Path) -> float:
    """Exact duration, for the cost-per-finished-minute metric (C7)."""
    probe = ffprobe_path()
    if probe is None:
        raise FFmpegMissing(INSTALL_HINT)
    result = _run(
        [probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]
    )
    raw = result.stdout.strip()
    try:
        return float(raw)
    except ValueError:
        # A zero-length or malformed file probes as "N/A" rather than failing.
        return 0.0


def make_silence(path: Path, seconds: float, sample_rate: int = 44100) -> Path:
    """Generate a mono silence file, used as the inter-chunk gap on export."""
    ffmpeg = require_ffmpeg()
    path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r={sample_rate}:cl=mono",
            "-t",
            f"{seconds:.3f}",
            "-c:a",
            "pcm_s16le",
            str(path),
        ]
    )
    return path


def make_tone(path: Path, seconds: float, frequency: int = 440, sample_rate: int = 44100) -> Path:
    """Generate a short audible tone, fading in and out.

    This is what an offline voice auditions as. Silence would be the obvious
    stand-in and is the wrong choice: a preview button that plays nothing is
    indistinguishable from a preview button that is broken, which is the exact
    confusion this feature exists to remove. A tone is unmistakably *working*.

    The fades matter — a raw sine that starts at full amplitude clicks, and a
    click is what a broken decoder sounds like.
    """
    ffmpeg = require_ffmpeg()
    path.parent.mkdir(parents=True, exist_ok=True)
    fade = min(0.08, seconds / 4)
    _run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate={sample_rate}:duration={seconds:.3f}",
            "-af",
            f"volume=0.25,afade=t=in:d={fade:.3f},"
            f"afade=t=out:st={max(0.0, seconds - fade):.3f}:d={fade:.3f}",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(path),
        ]
    )
    return path


@dataclass(frozen=True)
class ConcatResult:
    path: Path
    duration_s: float
    inputs: int


def concat(
    inputs: list[Path],
    output: Path,
    sample_rate: int = 44100,
    channels: int = 1,
) -> ConcatResult:
    """Join audio files into one, in order.

    Uses the **concat filter**, not the concat demuxer. The inputs are
    heterogeneous — mp3 takes interleaved with generated wav silence — and the
    demuxer expects matching stream parameters. The filter decodes everything
    to PCM first, which also sidesteps the encoder-delay gap that mp3-to-mp3
    concatenation leaves at every seam.

    Each input is passed through `aformat` first so a take that came back at a
    different sample rate or channel layout cannot fail the graph.
    """
    if not inputs:
        raise ValueError("Nothing to concatenate.")
    missing = [p for p in inputs if not p.exists()]
    if missing:
        raise FileNotFoundError(f"Missing audio: {', '.join(str(p) for p in missing)}")

    ffmpeg = require_ffmpeg()
    output.parent.mkdir(parents=True, exist_ok=True)

    command = [ffmpeg, "-y", "-loglevel", "error"]
    for path in inputs:
        command += ["-i", str(path)]

    layout = "mono" if channels == 1 else "stereo"
    fmt = f"aformat=sample_fmts=s16:sample_rates={sample_rate}:channel_layouts={layout}"
    stages = "".join(f"[{i}:a]{fmt}[a{i}];" for i in range(len(inputs)))
    labels = "".join(f"[a{i}]" for i in range(len(inputs)))
    graph = f"{stages}{labels}concat=n={len(inputs)}:v=0:a=1[out]"

    command += [
        "-filter_complex",
        graph,
        "-map",
        "[out]",
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        "-c:a",
        "pcm_s16le",
        str(output),
    ]
    _run(command)
    return ConcatResult(path=output, duration_s=duration_seconds(output), inputs=len(inputs))


@dataclass(frozen=True)
class Overlay:
    """One sound laid over a base track: an effect in the episode mix."""

    path: Path
    start_s: float
    gain_db: float = 0.0
    # Loop the sound to fill exactly this long — an ambience under a whole
    # passage. None plays it once, at its own length.
    length_s: float | None = None
    fade_in_s: float = 0.0
    fade_out_s: float = 0.0


def mix(
    base: Path,
    overlays: list[Overlay],
    output: Path,
    sample_rate: int = 44100,
    channels: int = 1,
) -> Path:
    """Lay sounds over a base track, each at its own time and level.

    The base sets the length: an overlay running past its end is cut there.
    Nothing is normalised on the way in — `amix` would otherwise divide every
    input by the number of inputs and quietly turn the narration down — and a
    limiter on the way out catches the rare peak where a sound meets a loud
    word. Its auto-level is off, so the episode's loudness is the narration's.
    """
    if not base.exists():
        raise FileNotFoundError(f"Missing audio: {base}")
    missing = [o.path for o in overlays if not o.path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing audio: {', '.join(str(p) for p in missing)}")
    ffmpeg = require_ffmpeg()
    output.parent.mkdir(parents=True, exist_ok=True)

    command = [ffmpeg, "-y", "-loglevel", "error", "-i", str(base)]
    for overlay in overlays:
        if overlay.length_s is not None:
            command += ["-stream_loop", "-1"]
        command += ["-i", str(overlay.path)]

    layout = "mono" if channels == 1 else "stereo"
    fmt = f"aformat=sample_fmts=fltp:sample_rates={sample_rate}:channel_layouts={layout}"
    stages = [f"[0:a]{fmt}[base]"]
    for index, overlay in enumerate(overlays, start=1):
        chain = [fmt]
        if overlay.length_s is not None:
            chain += [f"atrim=0:{overlay.length_s:.3f}", "asetpts=N/SR/TB"]
        if overlay.fade_in_s:
            chain.append(f"afade=t=in:st=0:d={overlay.fade_in_s:.3f}")
        if overlay.fade_out_s and overlay.length_s is not None:
            start = max(0.0, overlay.length_s - overlay.fade_out_s)
            chain.append(f"afade=t=out:st={start:.3f}:d={overlay.fade_out_s:.3f}")
        chain.append(f"volume={overlay.gain_db:.2f}dB")
        chain.append(f"adelay=delays={round(overlay.start_s * 1000)}:all=1")
        stages.append(f"[{index}:a]{','.join(chain)}[e{index}]")
    labels = "[base]" + "".join(f"[e{i}]" for i in range(1, len(overlays) + 1))
    stages.append(
        f"{labels}amix=inputs={len(overlays) + 1}:duration=first:dropout_transition=0:"
        "normalize=0,alimiter=limit=0.95:level=0:latency=1[out]"
    )

    command += [
        "-filter_complex",
        ";".join(stages),
        "-map",
        "[out]",
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        "-c:a",
        "pcm_s16le",
        str(output),
    ]
    _run(command)
    return output


# The integrated-loudness line of ebur128's closing summary.
_INTEGRATED = re.compile(r"^\s*I:\s+(-?[\d.]+|-?inf)\s+LUFS", re.MULTILINE)


def loudness_lufs(path: Path) -> float | None:
    """Integrated loudness (EBU R128) as it will be heard in a mono mix.

    Measured on the mono downmix `mix` makes, not the file as stored: a stereo
    effect reads up to 8 LU louder than the same effect folded to mono, and a
    level set from the stereo figure would land that far short. Padded to one
    400 ms gating block, so a short click still has a loudness. `ebur128`, not
    `loudnorm`: only the integrated figure is used, at a twentieth of the time.

    None for silence, or for anything ffmpeg cannot read.
    """
    ffmpeg = require_ffmpeg()
    try:
        result = _run(
            [
                ffmpeg,
                "-hide_banner",
                "-nostats",
                "-i",
                str(path),
                "-af",
                "aformat=channel_layouts=mono,apad=whole_dur=0.4,ebur128=framelog=quiet",
                "-f",
                "null",
                "-",
            ]
        )
    except FFmpegFailed:
        return None
    found = _INTEGRATED.findall(result.stderr)
    if not found:
        return None
    try:
        value = float(found[-1])
    except ValueError:
        return None
    # Silence reads as -inf, or as ebur128's -70 floor, which means nothing.
    return value if value > -70.0 else None


@dataclass(frozen=True)
class AudioFormat:
    """One delivery format: a container, its encoder, and what to call it."""

    key: str
    suffix: str
    label: str
    codec: str
    lossless: bool
    # `{bitrate}` is substituted at encode time so one table can describe both
    # constant-quality and bitrate-driven encoders.
    args: tuple[str, ...]
    note: str = ""

    def command_args(self, bitrate: str) -> list[str]:
        return [a.format(bitrate=bitrate) for a in self.args]


# Delivery formats. Keyed by the extension a person would ask for.
#
# `m4a` is the one worth explaining: it is AAC audio in an MP4 container, which
# is what "mp4" means for an audio-only deliverable and what Premiere, Resolve
# and Final Cut import directly. `+faststart` moves the index to the front so
# it streams and scrubs without reading the whole file first.
FORMATS: dict[str, AudioFormat] = {
    "wav": AudioFormat(
        key="wav",
        suffix=".wav",
        label="WAV",
        codec="pcm_s16le",
        lossless=True,
        args=("-c:a", "pcm_s16le"),
        note="Lossless master. The correct input for later loudness work.",
    ),
    "mp3": AudioFormat(
        key="mp3",
        suffix=".mp3",
        label="MP3",
        codec="mp3",
        lossless=False,
        args=("-c:a", "libmp3lame", "-b:a", "{bitrate}"),
        note="Plays everywhere.",
    ),
    "m4a": AudioFormat(
        key="m4a",
        suffix=".m4a",
        label="M4A (AAC)",
        codec="aac",
        lossless=False,
        args=("-c:a", "aac", "-b:a", "{bitrate}", "-movflags", "+faststart"),
        note="AAC in an MP4 container — what an NLE imports.",
    ),
    "flac": AudioFormat(
        key="flac",
        suffix=".flac",
        label="FLAC",
        codec="flac",
        lossless=True,
        args=("-c:a", "flac"),
        note="Lossless and compressed. Good for archiving.",
    ),
    "opus": AudioFormat(
        key="opus",
        suffix=".opus",
        label="Opus",
        codec="opus",
        lossless=False,
        args=("-c:a", "libopus", "-b:a", "96k"),
        note="Smallest at listenable quality. Web delivery.",
    ),
}

DEFAULT_FORMATS = ("wav", "mp3")


class UnknownFormat(ValueError):
    pass


def resolve_format(key: str) -> AudioFormat:
    try:
        return FORMATS[key.lower().lstrip(".")]
    except KeyError:
        known = ", ".join(FORMATS)
        raise UnknownFormat(f"Unknown format {key!r}. Choose from: {known}.") from None


def parse_formats(raw: str | list[str] | None) -> list[AudioFormat]:
    """`"wav,m4a"` or `["wav", "m4a"]` to formats, order and duplicates handled."""
    if raw is None:
        keys: list[str] = list(DEFAULT_FORMATS)
    elif isinstance(raw, str):
        keys = [part for part in (p.strip() for p in raw.split(",")) if part]
    else:
        keys = list(raw)

    seen: dict[str, AudioFormat] = {}
    for key in keys or list(DEFAULT_FORMATS):
        fmt = resolve_format(key)
        seen.setdefault(fmt.key, fmt)
    return list(seen.values())


def encode(source: Path, output: Path, fmt: AudioFormat | str, bitrate: str = "192k") -> Path:
    """Transcode `source` into `output` using one delivery format.

    Encoding always happens from the lossless master rather than from another
    lossy file — going mp3 to AAC would stack two lossy passes for no reason.
    """
    audio_format = fmt if isinstance(fmt, AudioFormat) else resolve_format(fmt)
    ffmpeg = require_ffmpeg()
    output.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(source),
            *audio_format.command_args(bitrate),
            str(output),
        ]
    )
    return output


def to_mp3(source: Path, output: Path, bitrate: str = "192k") -> Path:
    """Encode to MP3. Kept for the mock provider, which only ever wants mp3."""
    return encode(source, output, FORMATS["mp3"], bitrate)


def codec_of(path: Path) -> str:
    """The audio codec inside a file, so a container can be checked for its contents."""
    probe = ffprobe_path()
    if probe is None:
        raise FFmpegMissing(INSTALL_HINT)
    result = _run(
        [
            probe,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "csv=p=0",
            str(path),
        ]
    )
    return result.stdout.strip()
