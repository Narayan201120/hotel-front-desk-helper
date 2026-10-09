"""Deterministic checks that run after every model reply.

A reply that fails any check is never sent. It is logged with its reason
and the chat is escalated to a human.

The check covers digit tokens, $-amounts, clock times (including "11am"
and "11 am" forms), and number words ("eleven", "twenty-five") plus
"noon" and "midnight", which normalize to 12PM and 12AM. Anything that
cannot be resolved to a sheet value fails closed.
"""
from __future__ import annotations

import re

# Words the bot must never output on its own. Staff decisions are shown
# through structured status fields, never model text, so these words have
# no legitimate place in a bot reply. "confirmation" (as in the booking
# confirmation link) is deliberately NOT matched: \b boundaries exclude it,
# and a smoke test pins that behavior.
BANNED_WORDS = re.compile(r"\b(approved|confirmed|booked|refund|refunded)\b", re.IGNORECASE)

MONEY = re.compile(r"\$\s?\d[\d,]*")
TIME = re.compile(r"\b\d{1,2}(?::\d{2})?\s*[AP]\.?M\.?\b", re.IGNORECASE)
NUMBER = re.compile(r"\d[\d,]*")

ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_WORD = "|".join([*ONES, *TENS, "hundred", "thousand"])
WORD_NUM_RE = re.compile(
    rf"\b(?:noon|midnight|{_WORD})(?:[-\s](?:{_WORD}))*(?:\s*[AP]\.?M\.?)?\b",
    re.IGNORECASE,
)


def _word_value(words: list[str]) -> int | None:
    total, current = 0, 0
    for w in words:
        if w in ONES:
            current += ONES[w]
        elif w in TENS:
            current += TENS[w]
        elif w == "hundred":
            current = max(current, 1) * 100
        elif w == "thousand":
            current = max(current, 1) * 1000
            total += current
            current = 0
        else:
            return None
    return total + current


def normalize_token(token: str) -> str:
    # Strip ALL unicode whitespace (regular spaces, no-break spaces U+00A0,
    # narrow no-break spaces U+202F, ...). Models emit these interchangeably
    # in times like "6:30 AM", and a literal " " replace misses them.
    t = re.sub(r"\s+", "", token.upper().replace(",", "").replace(".", ""))
    if t == "NOON":
        return "12PM"
    if t == "MIDNIGHT":
        return "12AM"
    if not re.search(r"\d", t):
        # Word-number form, possibly with an AM/PM suffix ("ELEVENAM").
        # Non-greedy prefix so a trailing AM/PM splits off instead of
        # being swallowed into the word.
        m = re.match(r"^(.*?)(AM|PM)$", t)
        if m and m.group(1):
            words_part, suffix = m.group(1), m.group(2)
        else:
            words_part, suffix = t, None
        value = _word_value([w.lower() for w in words_part.split("-")])
        if value is None:
            return t  # unknown word: fail closed downstream
        return f"{value}{suffix or ''}"
    t = t.replace(":00", "")
    t = t.lstrip("0") or "0"
    return t


def extract_tokens(text: str) -> list[str]:
    """Pull money, time, number, and number-word tokens out of free text."""
    found: list[str] = []
    spans: list[tuple[int, int]] = []

    def overlapped(start: int) -> bool:
        return any(s <= start < e for s, e in spans)

    for pattern in (MONEY, TIME, WORD_NUM_RE):
        for m in pattern.finditer(text or ""):
            if not overlapped(m.start()):
                found.append(m.group())
                spans.append(m.span())
    for m in NUMBER.finditer(text or ""):
        if not overlapped(m.start()):
            found.append(m.group())
    return found


def build_allowed(entries: list[str]) -> set[str]:
    """Build the normalized allow-set from fact-sheet values entries."""
    allowed: set[str] = set()
    for entry in entries:
        for token in extract_tokens(entry):
            allowed.add(normalize_token(token))
    return allowed


def check_reply(reply: str, allowed: set[str]) -> tuple[bool, str]:
    """Return (ok, reason). reason is "ok" when the reply passes."""
    banned = BANNED_WORDS.search(reply or "")
    if banned:
        return False, f"banned-word:{banned.group().lower()}"
    for token in extract_tokens(reply):
        if normalize_token(token) not in allowed:
            return False, f"unknown-number-time-fee:{token}"
    return True, "ok"
