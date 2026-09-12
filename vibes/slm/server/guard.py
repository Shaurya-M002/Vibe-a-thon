"""The PRESERVE guard.

A small model asked to "clean up" a transcript will sometimes helpfully invent
the thing it thinks you meant: an email address for a name, a phone number, a
domain. Prompting alone does not stop that, so we check the output against the
input and strip anything that was never spoken.

Everything here is plain string work. No model calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict

EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
URL = re.compile(r"\b(?:https?://|www\.)[\w.-]+(?:/[\w./?%&=+-]*)?", re.IGNORECASE)
PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d\s-]{6,}\d)(?!\w)")

DIGIT_WORDS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "twenty": "20",
    "thirty": "30", "forty": "40", "fifty": "50", "sixty": "60",
    "seventy": "70", "eighty": "80", "ninety": "90", "hundred": "100",
    "thousand": "1000", "lakh": "100000", "crore": "10000000",
    "double": "", "triple": "",
}


@dataclass
class Violation:
    kind: str
    value: str
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


def _letters(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _spoken_digits(text: str) -> str:
    """Digits the speaker could plausibly have uttered, as one string."""
    out = []
    for token in re.findall(r"[a-z]+|\d+", text.lower()):
        if token.isdigit():
            out.append(token)
        elif token in DIGIT_WORDS:
            out.append(DIGIT_WORDS[token])
    return "".join(out)


def _supported_by(parts: list[str], haystack: str) -> bool:
    """Every meaningful chunk must be traceable to something in the input."""
    return all(len(p) < 2 or p in haystack for p in parts)


def find_violations(raw: str, cleaned: str) -> list[Violation]:
    """Contact details in `cleaned` that cannot be traced back to `raw`."""
    haystack = _letters(raw)
    raw_digits = _spoken_digits(raw)
    violations: list[Violation] = []

    for match in EMAIL.findall(cleaned):
        local, _, domain = match.partition("@")
        parts = re.split(r"[.\-_+]", local) + re.split(r"[.\-]", domain)
        if not _supported_by([p.lower() for p in parts if p], haystack):
            violations.append(
                Violation("email", match, "no address was spoken in the transcript")
            )

    for match in URL.findall(cleaned):
        parts = [p for p in re.split(r"[./:-]", match.lower()) if p and p not in ("https", "http", "www")]
        if not _supported_by(parts, haystack):
            violations.append(Violation("url", match, "no link was spoken in the transcript"))

    for match in PHONE.findall(cleaned):
        digits = re.sub(r"\D", "", match)
        if len(digits) >= 7 and digits not in raw_digits:
            violations.append(
                Violation("phone", match.strip(), "no number was spoken in the transcript")
            )

    return violations


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([.,!?])", r"\1", text)
    text = re.sub(r"\(\s*\)|<\s*>", "", text)
    text = re.sub(r"\s+(at|to)\s*([.,!?])", r"\2", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def strip_violations(cleaned: str, violations: list[Violation]) -> str:
    out = cleaned
    for violation in violations:
        out = out.replace(violation.value, "")
    return _tidy(out)


def looks_degenerate(raw: str, cleaned: str) -> bool:
    """Small models sometimes loop. Cleaning never triples the length."""
    if not cleaned:
        return False
    if len(cleaned) > max(80, len(raw) * 3):
        return True
    words = cleaned.split()
    if len(words) > 12:
        tail = words[-8:]
        if len(set(tail)) <= 2:
            return True
    return False


def apply_guard(raw: str, cleaned: str) -> tuple[str, list[dict]]:
    """Return text safe to paste, plus whatever we had to refuse."""
    reported: list[Violation] = []

    if looks_degenerate(raw, cleaned):
        reported.append(
            Violation("degenerate", cleaned[:120], "model looped; fell back to the raw transcript")
        )
        return _tidy(raw), [v.as_dict() for v in reported]

    violations = find_violations(raw, cleaned)
    if violations:
        return strip_violations(cleaned, violations), [v.as_dict() for v in violations]

    return _tidy(cleaned), []
