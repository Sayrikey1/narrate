"""Did every word in the script get spoken? Comparing a script to a transcript.

Pure: words in, findings out. No audio, no model, no database — which is what
lets every rule below be pinned by a test built from a word list.

The difficulty is not finding differences. A naive comparison of this episode's
script against its transcript produced 28 suspects, and 23 of them were the
transcriber's spelling rather than the narrator's mistake: "$10 million" against
"ten million dollars", "favour" against "favor", "Cashflow" against "cash flow".
A detector that cries wolf that often teaches its user to ignore it, so most of
this module is about *not* reporting things.

Three layers do that:

1. **Normalisation** puts both sides in one written form (Whisper's own English
   normaliser, vendored). Numbers, spellings and contractions stop differing.
2. **Alignment** is a word-level Needleman-Wunsch, not `difflib`. `difflib`
   merged a spoken "Good" into a neighbouring substitution and hid it; a scored
   alignment keeps an inserted word an insertion.
3. **Rules** decide which surviving differences matter, from what was lost and
   how, rather than from a single gap threshold. The thresholds are measured —
   the numbers cited beside each one come from the episode that motivated this
   and from defects deliberately spliced into clean takes.

Severity is deliberately coarse:

* `fail` — something the listener will hear as wrong: a word missing, a word
  that was never in the script, a word reduced to a noise.
* `review` — probably wrong, worth thirty seconds of listening.
* `info` — a difference that is almost always the transcriber, kept for
  completeness and never surfaced by default.

What this cannot see, stated plainly: a word clipped short but still heard *as
that word* by the transcriber, and a plural or tense change ("instrument" spoken
as "instruments"), which is indistinguishable from the transcriber's own
inflection errors and is reported only as `info`. Clean output here means no
issues were found, never that the take is perfect.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any

FAIL = "fail"
REVIEW = "review"
INFO = "info"

# Bumped whenever a rule or threshold changes, so a take checked under old rules
# can be found and re-checked. Recorded in `Take.verifier`.
RULES_VERSION = 2

# -- thresholds, each with the measurement behind it ---------------------------

# A pure deletion with at least this much silence where the word belongs. The
# dropped "Them." left 0.92s; ordinary words running together leave ~0.
GAP_S = 0.25

# A neighbouring word the transcriber stretched across a hole: long and unsure.
# base.en stretched "So" 0.8s at p≈0.05 over the missing "Them"; a spliced-out
# "them." was covered by a 0.88s "they" at p=0.55 — hence 0.6, not 0.3.
STRETCH_S = 0.6
STRETCH_P = 0.6

# An inserted word this confident is not a transcriber phantom. The unscripted
# "God" and "Good" came back at 0.94 and 0.95; every phantom tail word in the
# episode was below 0.25. Between the two, the caller re-decodes to confirm.
INSERT_P = 0.5
INSERT_CONFIRM_P = 0.15

# A substitution that is a different word, said confidently: "The" -> "Our"
# (similarity 0.0, p=0.98). Transcriber substitutions are near-spellings.
REWORD_SIM = 0.5
REWORD_P = 0.8

# Below this, a substituted content word is probably a noise the transcriber
# guessed at, rather than a word it misheard.
GARBLE_P = 0.5

# Two sides of a region this similar as strings are one word spelled two ways:
# "Ardnamurchan" / "Ardna merchant" (0.88) is a name, not a defect.
SPELLING_SIM = 0.75

# One heard "word" covering this much *voiced* audio is covering for more than
# one word — a repeat the transcriber collapsed. The false stretches in the
# episode had 0.27 to 0.47s of voiced audio; a real repeated phrase had 1.07s.
LONG_WORD_S = 1.0
LONG_WORD_VOICED_S = 0.8

# Words whose loss is still a fail when there is a gap, but not on its own. A
# dropped "the" with no audible hole is more often the transcriber's than the
# narrator's.
STOP = frozenset(
    """a an the and or but so of to in on at by for with from it its is are was
    were be been am i you he she we they them us me him her this that these those
    then than there their our your my do did does not no yes as if""".split()
)

_NUMBER_WORDS = {
    w: str(i)
    for i, w in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve".split()
    )
}
# Letters said as their names, so "the B quadrant" and "the bee quadrant" agree.
_LETTER_NAMES = {
    "bee": "b",
    "be": "b",
    "ess": "s",
    "ee": "e",
    "eye": "i",
    "oh": "o",
    "pee": "p",
    "tee": "t",
    "em": "m",
}
_NUMERIC = re.compile(r"[\d$%.,]+")
# Whisper sometimes emits " 200" then ",000", or " 100" then "%", as two words.
_PIECE = re.compile(r"^(?:[,.%]\d*%?|,\d{3})$")


@dataclass(frozen=True)
class Word:
    """One word as the transcriber heard it, with its timing and confidence."""

    text: str
    start: float
    end: float
    probability: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True)
class Finding:
    """One difference between what was written and what was heard.

    Times are seconds into the take. `confirm` marks an inserted word whose
    confidence sits in the band where transcriber phantoms also live; the
    caller is expected to re-decode that window before trusting it.
    """

    severity: str
    kind: str
    expected: str = ""
    heard: str = ""
    start_s: float = 0.0
    end_s: float | None = None
    probability: float | None = None
    gap_s: float | None = None
    context: str = ""
    confirm: bool = False
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        # Empty fields are left out to keep stored findings small — but by
        # identity, not equality: `0.0 == False` in Python, and a finding at
        # the very start of a take would otherwise lose its time.
        return {
            k: v for k, v in asdict(self).items() if v is not None and v != "" and v is not False
        }

    @property
    def summary(self) -> str:
        """One line a person can act on."""
        expected, heard = _brief(self.expected), _brief(self.heard)
        if self.kind == "missing":
            hole = f" ({self.gap_s:.2f}s gap)" if self.gap_s else ""
            count = len(self.expected.split())
            many = f" — {count} words" if count > _BRIEF_WORDS else ""
            return f'"{expected}" not spoken{hole}{many}'
        if self.kind == "extra":
            return f'extra word "{heard}" — not in the script'
        if self.kind == "reworded":
            return f'said "{heard}" where the script says "{expected}"'
        if self.kind == "garbled":
            return f'"{expected}" unclear — heard as "{heard}"'
        if self.kind == "burst":
            return "a short isolated sound between two silences — possibly a clipped word"
        if self.kind == "stretched":
            return f'"{heard}" runs long — possibly a repeated or merged phrase'
        if self.kind == "spelling":
            return f'"{expected}" / "{heard}"'
        return self.note or self.kind

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Finding:
        known = {f for f in Finding.__dataclass_fields__}
        return Finding(**{k: v for k, v in data.items() if k in known})


# Long enough to read a phrase whole; beyond it, a span is shown by its ends.
_BRIEF_WORDS = 10


def _brief(text: str) -> str:
    """A span short enough for one line: a truncated take can miss hundreds of
    words, and printing them all hides the one fact that matters — where."""
    words = text.split()
    if len(words) <= _BRIEF_WORDS:
        return text
    return f"{' '.join(words[:6])} … {' '.join(words[-3:])}"


# -- normalisation -------------------------------------------------------------


@lru_cache(maxsize=1)
def _normaliser() -> Callable[[str], str]:
    # Imported lazily: it reads a 1,700-line spelling table on construction.
    from narrate.verify._whisper_normalizer import EnglishTextNormalizer

    normaliser: Callable[[str], str] = EnglishTextNormalizer()  # type: ignore[no-untyped-call]
    return normaliser


# Symbols a narrator reads out as words. The normaliser drops them, which would
# leave the spoken "and" in "research & development" looking like an extra word.
_SPOKEN_SYMBOLS = {"&": " and ", "+": " plus ", "@": " at ", "=": " equals "}
# Whisper's normaliser deletes anything in brackets or parentheses — sensible for
# its own transcripts, where that is sound-event annotation, and wrong here,
# where an aside in parentheses is read aloud like any other words.
_BRACKETS = str.maketrans(dict.fromkeys("()[]{}<>", " "))


# Written shorthand a narrator reads out in full. Applied to both sides, so a
# transcript that writes the shorthand back still matches. Only unambiguous
# forms: a suffix letter means a quantity only after a currency sign ("$5M"),
# because after a bare number it is as often a name or a code ("3M", seat
# "12B", a "5k" race). Dashes between numbers are left alone — "50-50", "24-7"
# and phone numbers are not ranges; a "to" heard between two numbers is
# handled where extra words are judged.
_MONEY = r"([$\u00a3\u20ac]\d[\d,.]*)\s?"
_SHORTHAND = [
    (re.compile(r"(\d)\s*/\s*(?=[A-Za-z])"), r"\1 per "),  # $100/hour
    (re.compile(r"#\s?(?=\d)"), "number "),  # the #1 mistake
    (re.compile(r"\betc\b\.?", re.IGNORECASE), "et cetera"),
    # "a hundred" is "100"; the normaliser only knows "one hundred", and keeps a
    # stray "a" before a figure it wrote itself ("a 100").
    (re.compile(r"\ba\s+(?=(?:hundred|thousand|million|billion)\b)", re.IGNORECASE), "one "),
    (re.compile(r"\ba\s+(?=[$\u00a3\u20ac]?(?:1\d{2}|1,?000|1,?000,?000)\b)", re.IGNORECASE), ""),
    (re.compile(_MONEY + r"[kK]\b"), r"\1 thousand"),  # $50k
    (re.compile(_MONEY + r"(?:M|m|mn)\b"), r"\1 million"),  # $5M, £5m
    (re.compile(_MONEY + r"(?:B|bn)\b"), r"\1 billion"),  # $2B, $2bn
]


def normalise(text: str) -> list[str]:
    """Text as the list of tokens both sides are compared in."""
    for pattern, spoken in _SHORTHAND:
        text = pattern.sub(spoken, text)
    for symbol, word in _SPOKEN_SYMBOLS.items():
        text = text.replace(symbol, word)
    return _normaliser()(text.translate(_BRACKETS)).split()


# Words interchangeable in speech, compared as one.
_SAME_WORD = {"an": "a", "per": "a"}


def canon(tokens: list[str], *, interchangeable: bool = True) -> str:
    """A spacing- and spelling-blind key for a run of tokens.

    Catches what the normaliser leaves open: compounds split or joined
    ("cashflow" / "cash flow"), acronyms ("O.P.T." normalises to "0 p t"), small
    numbers written either way, letter names, and stray symbols.
    """
    out: list[str] = []
    single_letters = any(len(t) == 1 for t in tokens)
    for index, token in enumerate(tokens):
        t = token.replace("$", "").replace("'", "").replace("-", "").replace("%", "")
        t = _NUMBER_WORDS.get(t, t)
        t = _LETTER_NAMES.get(t, t)
        if t == "0" and single_letters:
            t = "o"
        if t == "a" and index + 1 < len(tokens) and tokens[index + 1][:1].isdigit():
            t = "1"
        # "$100 an hour", "$100 a hour" and "$100 per hour" say the same thing —
        # when comparing a region. Not when looking for one particular word on a
        # second listen, where a scripted "per" must not stand in for an "a".
        if interchangeable:
            t = _SAME_WORD.get(t, t)
        out.append(t)
    return "".join(out)


def join_pieces(words: list[Word]) -> list[Word]:
    """Re-attach the fragments Whisper splits a number into."""
    out: list[Word] = []
    for word in words:
        piece = word.text.strip()
        if out and _PIECE.match(piece):
            prev = out[-1]
            out[-1] = Word(
                text=prev.text + piece,
                start=prev.start,
                end=word.end,
                probability=min(prev.probability, word.probability),
            )
        else:
            out.append(word)
    return out


@dataclass(frozen=True)
class _Heard:
    """A normalised heard token and the span of words it came from."""

    token: str
    start: float
    end: float
    probability: float
    # How many transcribed words it came from: "nineteen ninety eight" is one
    # token from three words, and a span like that is long without being odd.
    words: int = 1


def heard_tokens(words: list[Word]) -> list[_Heard]:
    """Normalise the transcript *with context*, keeping each token's timing.

    Numbers need their neighbours — "ten", "million", "dollars" is one token —
    so the running transcript is re-normalised as each word arrives, and a
    token that re-forms keeps the start of its first word and takes the end of
    the latest. Mapping tokens to words by position alone shifted every later
    timestamp by one whenever a phrase like "a hundred and five" shrank.
    """
    spans: list[tuple[int, int]] = []
    previous: list[str] = []
    for index in range(len(words)):
        current = normalise("".join(w.text for w in words[: index + 1]))
        same = 0
        while same < min(len(current), len(previous)) and current[same] == previous[same]:
            same += 1
        rebuilt = spans[:same]
        for k in range(same, len(current)):
            first = spans[k][0] if k < len(spans) else index
            rebuilt.append((first, index))
        spans, previous = rebuilt, current

    return [
        _Heard(
            token=token,
            start=words[first].start,
            end=words[last].end,
            probability=min(w.probability for w in words[first : last + 1]),
            words=last - first + 1,
        )
        for token, (first, last) in zip(previous, spans, strict=True)
    ]


# -- alignment -----------------------------------------------------------------


@dataclass(frozen=True)
class _Op:
    op: str  # "=" match, "~" substitution, "-" expected only, "+" heard only
    e: int | None
    h: int | None


def _substitution_cost(a: str, b: str) -> float:
    if a == b:
        return 0.0
    return 0.4 + 1.2 * (1 - difflib.SequenceMatcher(None, a, b).ratio())


def align(expected: list[str], heard: list[str]) -> list[_Op]:
    """Word-level Needleman-Wunsch.

    A substitution between similar words is cheaper than a deletion plus an
    insertion, so a misspelling stays a substitution; a substitution between
    unrelated words costs more than an insertion, so a spoken extra word stays
    an insertion rather than being absorbed by its neighbour.
    """
    n, m = len(expected), len(heard)
    cache: dict[tuple[str, str], float] = {}

    def sub(a: str, b: str) -> float:
        key = (a, b)
        if key not in cache:
            cache[key] = _substitution_cost(a, b)
        return cache[key]

    cost = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        cost[i][0] = float(i)
    for j in range(1, m + 1):
        cost[0][j] = float(j)
    for i in range(1, n + 1):
        row, above, word = cost[i], cost[i - 1], expected[i - 1]
        for j in range(1, m + 1):
            row[j] = min(above[j - 1] + sub(word, heard[j - 1]), above[j] + 1, row[j - 1] + 1)

    ops: list[_Op] = []
    i, j = n, m
    while i or j:
        if (
            i
            and j
            and abs(cost[i][j] - (cost[i - 1][j - 1] + sub(expected[i - 1], heard[j - 1]))) < 1e-9
        ):
            ops.append(_Op("=" if expected[i - 1] == heard[j - 1] else "~", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i and abs(cost[i][j] - (cost[i - 1][j] + 1)) < 1e-9:
            ops.append(_Op("-", i - 1, None))
            i -= 1
        else:
            ops.append(_Op("+", None, j - 1))
            j -= 1
    ops.reverse()
    return ops


# -- rules ---------------------------------------------------------------------


def _inflection(a: str, b: str) -> bool:
    def stem(word: str) -> str:
        return re.sub(r"(es|s|ed|d|ing)$", "", word)

    if stem(a) == stem(b):
        return True
    return (a.startswith(b) or b.startswith(a)) and abs(len(a) - len(b)) <= 3


def _soundex(word: str) -> str:
    letters = re.sub(r"[^a-z]", "", word.lower())
    if not letters:
        return ""
    codes = {
        **dict.fromkeys("bfpv", "1"),
        **dict.fromkeys("cgjkqsxz", "2"),
        **dict.fromkeys("dt", "3"),
        "l": "4",
        **dict.fromkeys("mn", "5"),
        "r": "6",
    }
    out, previous = letters[0], codes.get(letters[0], "")
    for char in letters[1:]:
        digit = codes.get(char, "")
        if digit and digit != previous:
            out += digit
        if char not in "hw":
            previous = digit
    return (out + "000")[:4]


def _is_numeric(tokens: list[str]) -> bool:
    return bool(tokens) and all(
        _NUMERIC.fullmatch(t) or t in _NUMBER_WORDS or t in _QUANTITY_LETTERS for t in tokens
    )


# A letter after a bare figure — "50k", "3M", "12B" — is a quantity or part of a
# name. Either reading is plausible, so a mismatch there is worth a listen, not
# a verdict.
_QUANTITY_LETTERS = frozenset({"k", "m", "b", "bn", "mn"})


def _spoken_as_several(token: str) -> bool:
    if any(c in "$\u00a3\u20ac%" for c in token):
        return True
    # Up to twenty is one spoken word ("twelve"); beyond it, a figure is several.
    digits = "".join(c for c in token if c.isdigit())
    return len(digits) >= 2 and (not token.isdigit() or int(token) > 20)


# Words a narrator adds between two figures when reading them: "3-4" as "three
# to four", "1:1" as "one on one".
_CONNECTORS = frozenset({"to", "on"})


def _context(expected: list[str], first: int, last: int, mark: tuple[int, int] | None) -> str:
    lo, hi = max(0, first - 6), min(len(expected), last + 7)
    words = []
    for index in range(lo, hi):
        token = expected[index]
        if mark and mark[0] <= index <= mark[1]:
            token = f"[{token}]"
        words.append(token)
    return " ".join(words)


def classify(
    expected_spoken: str,
    words: list[Word],
    *,
    voiced: Callable[[float, float], float] | None = None,
) -> list[Finding]:
    """Everything that differs between the script and what was heard.

    `expected_spoken` is the text as it should sound — audio tags and speaker
    labels already removed. `voiced(start, end)` returns seconds of audible
    signal in a span; without it the long-word rule is skipped rather than
    guessed, because it is the rule most prone to false alarms.
    """
    words = join_pieces(words)
    expected = normalise(expected_spoken)
    heard = heard_tokens(words)
    ops = align(expected, [h.token for h in heard])

    findings: list[Finding] = []
    k = 0
    while k < len(ops):
        if ops[k].op == "=":
            k += 1
            continue
        start = k
        while k < len(ops) and ops[k].op != "=":
            k += 1
        findings.extend(_region(ops, start, k, expected, heard))

    if voiced is not None:
        for h in heard:
            if (
                h.words == 1
                # A figure is one token but several spoken words — "2024" is
                # "twenty twenty-four", "$5" is "five dollars" — so its length
                # says nothing. A single digit is one short word, and still counts.
                and not _spoken_as_several(h.token)
                and h.end - h.start >= LONG_WORD_S
                and h.probability < STRETCH_P
                and voiced(h.start, h.end) >= LONG_WORD_VOICED_S
            ):
                findings.append(
                    Finding(
                        severity=REVIEW,
                        kind="stretched",
                        heard=h.token,
                        start_s=h.start,
                        end_s=h.end,
                        probability=round(h.probability, 2),
                    )
                )
    return sorted(findings, key=lambda f: f.start_s)


def _region(
    ops: list[_Op], start: int, stop: int, expected: list[str], heard: list[_Heard]
) -> list[Finding]:
    region = ops[start:stop]
    e_idx = [o.e for o in region if o.e is not None]
    h_idx = [o.h for o in region if o.h is not None]
    e_side = [expected[i] for i in e_idx]
    h_side = [heard[j].token for j in h_idx]

    if canon(e_side) == canon(h_side):
        return []

    prev_h = next((ops[t].h for t in range(start - 1, -1, -1) if ops[t].h is not None), None)
    next_h = next((ops[t].h for t in range(stop, len(ops)) if ops[t].h is not None), None)
    before = heard[prev_h] if prev_h is not None else None
    after = heard[next_h] if next_h is not None else None
    anchor = before.end if before else (heard[h_idx[0]].start if h_idx else 0.0)

    mark = (min(e_idx), max(e_idx)) if e_idx else None
    around = next((ops[t].e for t in range(start, -1, -1) if ops[t].e is not None), 0) or 0
    context = _context(expected, mark[0] if mark else around, mark[1] if mark else around, mark)

    # A real extra or missing word can sit right beside a compound or a name the
    # transcriber spelled its own way — "Cashflow" heard as "cash flow god". The
    # whole region is not one spelling difference then: peel the words off the
    # ends until the rest agrees, and judge only what was peeled.
    peeled = _peel(e_idx, h_idx, expected, heard, canon_equal)
    if peeled is None:
        # The same, where the rest is spelled only *nearly* alike — "the
        # instrument them" heard as "the instruments", a name heard as two
        # words with an unscripted one after. A peel is taken only if it leaves
        # the two sides *more* alike than the whole region was: peeling a piece
        # of the same word makes the rest less alike, never more.
        whole = _likeness(e_side, h_side)
        peeled = _peel(
            e_idx,
            h_idx,
            expected,
            heard,
            # One scripted word, respelled or inflected — never two, or a second
            # dropped word could hide inside the "spelling".
            lambda a, b: len(a) == 1 and _spelled_alike(a, b) and _likeness(a, b) > whole,
        )
    if peeled is not None:
        e_keep, h_keep, side = peeled
        # The rest of the region is the same words spelled differently; it now
        # borders the peeled word, so the gap is measured from it, not across it.
        same = sorted(set(h_idx) - h_keep)
        if same and side == "trailing":
            before = heard[same[-1]]
        elif same and side == "leading":
            after = heard[same[0]]
        anchor = before.end if before else anchor
        # Rebuilt by which side was peeled — never by the op's original pairing,
        # which may have matched the peeled word against one it has nothing to
        # do with.
        region = [_Op("+", None, j) for j in sorted(h_keep)] + [
            _Op("-", i, None) for i in sorted(e_keep)
        ]
        e_idx = sorted(e_keep)
        h_idx = sorted(h_keep)
        e_side = [expected[i] for i in e_idx]
        h_side = [heard[j].token for j in h_idx]

    if _spelled_alike(e_side, h_side):
        return [
            Finding(
                severity=INFO,
                kind="spelling",
                expected=" ".join(e_side),
                heard=" ".join(h_side),
                start_s=heard[h_idx[0]].start,
                end_s=heard[h_idx[-1]].end,
                context=context,
            )
        ]

    numeric = _is_numeric(e_side + h_side)

    def cap(severity: str) -> str:
        # Numbers were the largest single source of false alarms.
        return REVIEW if numeric and severity == FAIL else severity

    out: list[Finding] = []
    deleted = [expected[o.e] for o in region if o.op == "-" and o.e is not None]
    inserted = [o.h for o in region if o.op == "+" and o.h is not None]
    substituted = [
        (expected[o.e], heard[o.h])
        for o in region
        if o.op == "~" and o.e is not None and o.h is not None
    ]

    if deleted:
        pure = not inserted and not substituted
        gap = (after.start - before.end) if (pure and before and after) else 0.0
        near = [h for h in (before, after) if h] + [heard[j] for j in h_idx]
        stretched = any(h.end - h.start >= STRETCH_S and h.probability < STRETCH_P for h in near)
        content = [d for d in deleted if d not in STOP]
        severity = FAIL if (gap >= GAP_S or stretched or content or len(deleted) >= 2) else INFO
        out.append(
            Finding(
                severity=cap(severity),
                kind="missing",
                expected=" ".join(deleted),
                start_s=anchor,
                end_s=after.start if after else None,
                gap_s=round(gap, 2) if gap else None,
                context=context,
            )
        )

    for j in inserted:
        h = heard[j]
        between_figures = (
            h.token in _CONNECTORS
            and 0 < j < len(heard) - 1
            and _is_numeric([heard[j - 1].token])
            and _is_numeric([heard[j + 1].token])
        )
        if between_figures:
            # A range or a ratio read aloud: "3-4" as "three to four".
            severity, confirm = INFO, False
        elif h.probability >= INSERT_P:
            severity, confirm = FAIL, True
        elif h.probability >= INSERT_CONFIRM_P:
            severity, confirm = REVIEW, True
        else:
            severity, confirm = INFO, False
        out.append(
            Finding(
                severity=cap(severity),
                kind="extra",
                heard=h.token,
                start_s=h.start,
                end_s=h.end,
                probability=round(h.probability, 2),
                context=context,
                confirm=confirm,
            )
        )

    for expected_word, h in substituted:
        similarity = difflib.SequenceMatcher(None, expected_word, h.token).ratio()
        inflected = _inflection(expected_word, h.token)
        reworded = similarity < REWORD_SIM and h.probability >= REWORD_P and not inflected
        garbled = (
            expected_word not in STOP
            and h.probability < GARBLE_P
            and _soundex(expected_word) != _soundex(h.token)
            and not inflected
        )
        kind = "garbled" if garbled and not reworded else "reworded"
        severity = REVIEW if (reworded or garbled) else INFO
        out.append(
            Finding(
                severity=cap(severity),
                kind=kind,
                expected=expected_word,
                heard=h.token,
                start_s=h.start,
                end_s=h.end,
                probability=round(h.probability, 2),
                context=context,
            )
        )
    return out


def canon_equal(a: list[str], b: list[str]) -> bool:
    return canon(a) == canon(b)


def _likeness(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, "".join(a), "".join(b)).ratio()


def _spelled_alike(a: list[str], b: list[str]) -> bool:
    """The same words spelled differently: close as strings *and* in length.

    The length test matters: a whole extra word tacked onto a spelling
    difference keeps the strings similar but not the same size.
    """
    if not a or not b:
        return False
    x, y = len("".join(a)), len("".join(b))
    return _likeness(a, b) >= SPELLING_SIM and min(x, y) / max(x, y) >= 0.8


def _peel(
    e_idx: list[int],
    h_idx: list[int],
    expected: list[str],
    heard: list[_Heard],
    same: Callable[[list[str], list[str]], bool],
) -> tuple[set[int], set[int], str] | None:
    """Tokens to keep, after peeling up to two off either end of either side.

    Returns the expected and heard indices that are left over — the genuine
    extra or missing words — when removing them makes the two sides the same
    text by `same`. `None` when no such peeling exists, and the region is
    judged whole.
    """
    e_tokens = [expected[i] for i in e_idx]
    h_tokens = [heard[j].token for j in h_idx]
    if not e_tokens or not h_tokens:
        return None
    for k in (1, 2):
        if len(h_tokens) > k:
            if same(e_tokens, h_tokens[k:]):
                return set(), set(h_idx[:k]), "leading"
            if same(e_tokens, h_tokens[:-k]):
                return set(), set(h_idx[-k:]), "trailing"
        if len(e_tokens) > k:
            if same(e_tokens[k:], h_tokens):
                return set(e_idx[:k]), set(), "leading"
            if same(e_tokens[:-k], h_tokens):
                return set(e_idx[-k:]), set(), "trailing"
    # A scripted word dropped on each side of one heard its own way: "them
    # Ardnamurchan now" heard as "Ardna merchant".
    if len(e_tokens) > 2 and same(e_tokens[1:-1], h_tokens):
        return {e_idx[0], e_idx[-1]}, set(), "both"
    return None


def verdict(findings: list[Finding]) -> str:
    """The take's status from its findings.

    `clear` means the checks ran and found nothing. It is deliberately not
    called "pass" and is never shown as a tick: a word clipped short but still
    recognised is invisible to every check here.
    """
    severities = {f.severity for f in findings}
    if FAIL in severities:
        return "suspect"
    if REVIEW in severities:
        return "review"
    return "clear"
