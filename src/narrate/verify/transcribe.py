"""Speech to text, behind a seam.

One real backend and one fake. The real one is faster-whisper running locally:
free, offline once its model is on disk, no torch. It is an optional extra, so
this module imports it only when asked to transcribe, and everything that needs
it says so plainly when it is absent instead of failing on an import.

The settings are not defaults, they are measurements. On the take that dropped
"Them", `small.en` caught the drop in every configuration tried; `base.en` and
`tiny.en` "heard" a "them" that is not there. Beam 5 over greedy, because greedy
decoding produced a ten-word hallucinated loop. No voice-activity filter,
because its short-silence setting deleted a real sentence. And **never** a
prompt containing the script: primed with the expected text, every model skipped
the forty words it was told to expect, which is precisely how a real drop would
be hidden.

The model is never downloaded implicitly. It is about 480MB, the download has
been seen to stall, and a command that silently fetches half a gigabyte is not
one anybody should run by accident — so loading is always local-only, and the
download is its own explicit step.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import metadata
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Protocol

from narrate.verify.compare import Word

DEFAULT_MODEL = "small.en"
DEFAULT_COMPUTE_TYPE = "int8"

# What `small.en` weighs, for the prompt that asks before downloading it.
MODEL_SIZE_MB = {"tiny.en": 75, "base.en": 145, "small.en": 484, "medium.en": 1530}

# The files a converted CTranslate2 model directory must contain to load.
_REQUIRED = ("model.bin", "config.json", "tokenizer.json")

# narrate is not on PyPI, and a package with that name there is somebody
# else's — so the pip form installs from the clone, never by name.
INSTALL_HINT = (
    "Install the extra with:  uv sync --extra verify   "
    "(or, from the repository: pip install --prefer-binary -e '.[verify]')"
)


class SttUnavailable(RuntimeError):
    """The optional speech-to-text extra is not installed."""


class ModelMissing(RuntimeError):
    """The speech model has not been downloaded yet."""


class ModelBroken(RuntimeError):
    """The model directory exists but will not load — usually a partial download."""


class Transcriber(Protocol):
    """What verification needs from speech to text."""

    @property
    def identity(self) -> str:
        """Everything that affects the output, recorded beside each result."""
        ...

    def transcribe(self, path: Path, window: tuple[float, float] | None = None) -> list[Word]:
        """Words heard in a file, or in one window of it, timed from the file's start."""
        ...


def stt_available() -> bool:
    return find_spec("faster_whisper") is not None


def model_dir(name: str = DEFAULT_MODEL) -> Path:
    """Where a model lives on this machine.

    `NARRATE_STT_MODEL_DIR` points at a directory already containing one — for
    an offline machine, or a model copied from elsewhere. Otherwise it sits with
    the database, out of any synced folder.
    """
    from narrate.settings import get_settings

    override = get_settings().stt_model_dir
    if override:
        return Path(override).expanduser()
    from narrate.db.locate import data_home

    return data_home() / "models" / f"faster-whisper-{name}"


def model_ready(name: str = DEFAULT_MODEL) -> bool:
    directory = model_dir(name)
    return all((directory / f).is_file() and (directory / f).stat().st_size > 0 for f in _REQUIRED)


def download(name: str = DEFAULT_MODEL) -> Path:
    """Fetch a model into `model_dir`. The one place anything is downloaded."""
    if not stt_available():
        raise SttUnavailable(INSTALL_HINT)
    import faster_whisper

    target = model_dir(name)
    target.mkdir(parents=True, exist_ok=True)
    faster_whisper.download_model(name, output_dir=str(target))
    if not model_ready(name):
        raise ModelBroken(
            f"The download to {target} finished without a complete model. Delete the "
            "directory and try again."
        )
    return target


@dataclass
class LocalWhisper:
    """faster-whisper on the CPU, loaded once and reused.

    Loading is the slow part — a couple of seconds and half a gigabyte of
    memory — so one instance serves every take in a run, and the API server
    keeps one for its lifetime.
    """

    model: str = DEFAULT_MODEL
    compute_type: str = DEFAULT_COMPUTE_TYPE
    threads: int = field(default_factory=lambda: min(10, os.cpu_count() or 4))
    _loaded: Any = field(default=None, init=False, repr=False)

    @property
    def identity(self) -> str:
        # The decoding backend is part of the identity: whether a confidently
        # spoken extra word comes back at p=0.94 or p=0.26 was measured to
        # depend on int8 against float32, so results from different builds are
        # not interchangeable. Read from package metadata, so it is the same
        # before the model loads as after.
        try:
            backend = f":ct2-{metadata.version('ctranslate2')}"
        except metadata.PackageNotFoundError:
            backend = ""
        return f"fw:{self.model}:{self.compute_type}:b5{backend}"

    def _whisper(self) -> Any:
        if self._loaded is not None:
            return self._loaded
        if not stt_available():
            raise SttUnavailable(INSTALL_HINT)
        if not model_ready(self.model):
            size = MODEL_SIZE_MB.get(self.model)
            weight = f" (about {size} MB)" if size else ""
            raise ModelMissing(
                f"The {self.model} speech model is not downloaded yet{weight}. "
                "Fetch it once with:  narrate verify --download-model"
            )
        from faster_whisper import WhisperModel

        try:
            self._loaded = WhisperModel(
                str(model_dir(self.model)),
                device="cpu",
                compute_type=self.compute_type,
                cpu_threads=self.threads,
                local_files_only=True,
            )
        except Exception as exc:  # ctranslate2 raises bare RuntimeErrors and decode errors
            raise ModelBroken(
                f"The model in {model_dir(self.model)} would not load ({exc}). It is "
                "usually a partial download: delete that directory and run "
                "`narrate verify --download-model`."
            ) from exc
        return self._loaded

    def transcribe(self, path: Path, window: tuple[float, float] | None = None) -> list[Word]:
        whisper = self._whisper()
        from faster_whisper import decode_audio

        samples = decode_audio(str(path), sampling_rate=16_000)
        offset = 0.0
        if window is not None:
            start, end = max(0.0, window[0]), window[1]
            samples = samples[int(start * 16_000) : int(end * 16_000)]
            offset = start

        segments, _ = whisper.transcribe(
            samples,
            language="en",
            word_timestamps=True,
            beam_size=5,
            vad_filter=False,
            initial_prompt=None,
            condition_on_previous_text=False,
        )
        return [
            Word(
                text=w.word,
                start=round(w.start + offset, 3),
                end=round(w.end + offset, 3),
                probability=float(w.probability),
            )
            for segment in segments
            for w in segment.words or []
        ]


@dataclass
class MockTranscriber:
    """A transcriber that reports what it is told to.

    `script` maps an audio path to the words "heard" in it. A window request
    returns the words inside the window, or — when `windows` has an entry for
    that path — a different decode, so the confirmation step can be tested
    against a phantom that disappears on a second listen.
    """

    script: dict[Path, list[Word]] = field(default_factory=dict)
    windows: dict[Path, list[Word]] = field(default_factory=dict)
    calls: list[tuple[Path, tuple[float, float] | None]] = field(default_factory=list)
    on_call: Callable[[Path], None] | None = None

    @property
    def identity(self) -> str:
        return "mock"

    def transcribe(self, path: Path, window: tuple[float, float] | None = None) -> list[Word]:
        self.calls.append((path, window))
        if self.on_call:
            self.on_call(path)
        source = self.windows.get(path) if window is not None and path in self.windows else None
        words = source if source is not None else self.script.get(path, [])
        if window is None:
            return list(words)
        lo, hi = window
        return [w for w in words if w.end > lo and w.start < hi]
