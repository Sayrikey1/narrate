"""Text worth a second look before any of it is paid for.

Advisory only — nothing here blocks an ingest or a generation. Each rule names a
pattern that has gone wrong in practice and says what to try instead, and each
is deliberately narrow, because a warning that fires on every script teaches
the reader to skip warnings.

Two rules:

* **A stranded one-word sentence** — a single word ending its paragraph, right
  after a long sentence. That is the exact shape of "…the one thing that cannot
  scale. Them.", which an expressive model rendered as a 200ms noise. The rule
  is a heuristic from that case, not documented provider behaviour: of 38
  one-word sentences in the episode it came from, it fires on two, one of which
  was the word that was lost. Firing on every one-word sentence would have been
  37 false alarms.
* **Audio tags on a model that does not honour them.** `[fast-paced]` is a
  direction to eleven_v3; to any other model it is two words, read aloud and
  billed for.

It does not attempt the other defects `narrate verify` finds — an unscripted
word at a paragraph start, a swapped word — because nothing in the text
predicts them. Those are caught by listening, or by `narrate verify`.
"""

from __future__ import annotations

import re

from narrate.registry import ModelSpec

# How long the sentence before a one-word sentence has to be for the pair to be
# worth flagging. A one-word reply after a short line is ordinary rhythm.
LONG_SENTENCE_WORDS = 10

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_AUDIO_TAG = re.compile(r"\[[^\]\n]{1,60}\]")


def _sentences(paragraph: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(paragraph.strip()) if s]


def stranded_words(text: str) -> list[str]:
    """One-word sentences that end a paragraph after a long sentence."""
    found: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        sentences = _sentences(_AUDIO_TAG.sub(" ", paragraph))
        if len(sentences) < 2:
            continue
        last, before = sentences[-1], sentences[-2]
        if len(last.split()) == 1 and len(before.split()) >= LONG_SENTENCE_WORDS:
            found.append(last.strip())
    return found


def lint_chunks(chunks: list[tuple[int, str]], spec: ModelSpec) -> list[str]:
    """Warnings for a script's chunks, as `(ordinal, text)` pairs."""
    warnings: list[str] = []

    stranded = [(ordinal, word) for ordinal, text in chunks for word in stranded_words(text)]
    if stranded and spec.long_form == "expressive":
        shown = ", ".join(f"{word!r} (chunk {ordinal})" for ordinal, word in stranded[:4])
        more = f" and {len(stranded) - 4} more" if len(stranded) > 4 else ""
        warnings.append(
            f"One-word sentence(s) ending a paragraph after a long sentence: {shown}{more}. "
            "An expressive model once rendered exactly this as a 200ms noise. If it "
            "matters, join it to the sentence before — 'cannot scale: them.' — and "
            "check the take with `narrate verify`."
        )

    if not spec.audio_tags:
        tagged = sorted({ordinal for ordinal, text in chunks if _AUDIO_TAG.search(text)})
        if tagged:
            listed = ", ".join(map(str, tagged[:8])) + (" …" if len(tagged) > 8 else "")
            warnings.append(
                f"Chunk(s) {listed} contain [bracketed] text. {spec.label} does not treat "
                "these as audio tags — it will read them aloud, and bill for them. Remove "
                "them, or use a model from the eleven_v3 family."
            )
    return warnings
