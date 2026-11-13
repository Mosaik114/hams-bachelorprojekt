"""Checks a cleanup answer before it is pasted.

The cleanup may rewrite what the user said, and whatever it returns is pasted into someone else's text field. A
wrong answer is worse than no cleanup, so each check here falls back to the raw transcript: that is what Whisper
heard, never worse than a dictation without cleanup.

The thresholds were tuned on the outputs of `tools/cleanup_eval.py` (docs/evaluation.md, "LLM cleanup quality"):
none of them trips on a legitimate cleanup in those runs.

- **Meta line.** "Here is the corrected text:" in front of the text is removed; the rest is checked as usual.
- **Prompt leak.** The answer repeats a sentence of the instructions or the glossary hint.
- **Too long.** More than 1.5 times the input plus 20 characters. Removing fillers only ever shortens a text; the
  longest legitimate answer in the evaluation was 1.25 times its input.
- **Digits changed.** The digits, read left to right, must stay the same. A phone number with two extra digits looks
  just as plausible as the right one. Spelled-out numbers ("6" → "sechs") also fall back, which costs nothing: the
  raw text says what was dictated.
- **Words dropped.** Less than half of the dictated words survive (fillers aside, at least four distinct words).
  This is an answer, a translation or a rewrite, not a cleanup: "Antworte nur mit Ja oder Nein: …" → "Nein".
"""

from __future__ import annotations

import re
from typing import NamedTuple

#: Growth an answer may show before it counts as "more than a cleanup".
MAX_GROWTH_FACTOR = 1.5
MAX_GROWTH_CHARS = 20
#: Share of the dictated words that must survive, and the input size from which the share counts.
MIN_KEPT_SHARE = 0.5
MIN_WORDS_FOR_KEPT_SHARE = 4
#: A sentence of the instructions counts as leaked from this many words on.
MIN_LEAK_WORDS = 5

#: Fillers the cleanup is meant to remove (German and English, lower case).
FILLERS = frozenset({"äh", "ähm", "äähm", "öhm", "hm", "hmm", "mhm", "eh", "ehm", "uh", "uhm", "um", "erm", "m"})

#: A first line that talks *about* the text: "Here is the corrected text:", "Hier ist der korrigierte Text:".
#: It must name both the correction and the text, so "Hier ist meine Idee: …" stays.
_META_LINE_RE = re.compile(
    r"\A[ \t]*(?P<line>[^\n:]{0,80}?"
    r"(?:korrigiert|bereinigt|überarbeitet|verbessert|corrected|cleaned|revised|fixed)"
    r"[^\n:]{0,40}?(?:text|transkript|fassung|version|satz|transcript|sentence)[^\n:]{0,20}?)"
    r"[ \t]*:[ \t]*\n?",
    re.IGNORECASE,
)

_WORD_RE = re.compile(r"\w+")
_SENTENCE_SPLIT_RE = re.compile(r"[\n.:!?;]")


class GuardResult(NamedTuple):
    """The text to paste and why the answer was changed: None, "meta_line" or a fallback reason."""

    text: str
    reason: str | None


def check(raw: str, answer: str, instructions: str = "") -> GuardResult:
    """The answer, without a leading meta line, or `raw` if it fails a check.

    `instructions` is everything sent besides the dictated text (prompt template and glossary hint); an answer
    that repeats it is a leak.
    """
    text, stripped = _strip_meta_line(raw, answer)
    reason = _fallback_reason(raw, text, instructions)
    if reason:
        return GuardResult(raw, reason)
    return GuardResult(text, "meta_line" if stripped else None)


def _fallback_reason(raw: str, answer: str, instructions: str) -> str | None:
    if not answer.strip():
        return "empty"
    if _leaks(raw, answer, instructions):
        return "prompt_leak"
    if len(answer) > MAX_GROWTH_FACTOR * len(raw) + MAX_GROWTH_CHARS:
        return "too_long"
    if digits(answer) != digits(raw):
        return "digits_changed"
    if _kept_share(raw, answer) < MIN_KEPT_SHARE:
        return "words_dropped"
    return None


def _strip_meta_line(raw: str, answer: str) -> tuple[str, bool]:
    """Removes "Here is the corrected text:" in front of the answer, unless the user dictated those words."""
    match = _META_LINE_RE.match(answer)
    if match is None or set(words(match.group("line"))) <= set(words(raw)):
        return answer, False
    rest = answer[match.end() :].strip()
    if not rest:
        return answer, False
    return _strip_quotes(rest), True


def _strip_quotes(text: str) -> str:
    """Quotes the meta line put around the text: "…", „…“, »…«."""
    pairs = {'"': '"', "„": "“", "“": "”", "»": "«", "'": "'"}
    if len(text) >= 2 and pairs.get(text[0]) == text[-1]:
        return text[1:-1].strip()
    return text


def digits(text: str) -> str:
    """All digits in reading order: "0151 23456" → "015123456"."""
    return "".join(ch for ch in text if ch.isdigit())


def words(text: str) -> list[str]:
    """Lower-case words without fillers."""
    return [w for w in _WORD_RE.findall(text.lower()) if w not in FILLERS]


def _kept_share(raw: str, answer: str) -> float:
    """Share of the distinct dictated words that are still in the answer; 1.0 for very short inputs."""
    before = set(words(raw))
    if len(before) < MIN_WORDS_FOR_KEPT_SHARE:
        return 1.0
    return len(before & set(words(answer))) / len(before)


def _normalised(text: str) -> str:
    return " ".join(_WORD_RE.findall(text.lower()))


def instruction_sentences(instructions: str) -> list[str]:
    """The sentences and lines of the instructions that are long enough to recognise, normalised."""
    parts = (_normalised(part) for part in _SENTENCE_SPLIT_RE.split(instructions))
    return [part for part in parts if len(part.split()) >= MIN_LEAK_WORDS]


def _leaks(raw: str, answer: str, instructions: str) -> bool:
    if not instructions:
        return False
    said, answered = f" {_normalised(raw)} ", f" {_normalised(answer)} "
    return any(f" {part} " in answered and f" {part} " not in said for part in instruction_sentences(instructions))
