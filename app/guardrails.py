"""Deterministic checks that run after every model reply.

A reply that fails any check is never sent. It is logged with its reason
and the chat is escalated to a human.

The check covers digit tokens, $-amounts, and clock times. Number words
("eleven") can slip past it; the phrase prompt plus temperature 0 are the
mitigation for that gap. See README Assumptions.
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
TIME = re.compile(r"\b\d{1,2}:\d{2}\s*[AP]\.?M\.?\b", re.IGNORECASE)
NUMBER = re.compile(r"\d[\d,]*")


def normalize_token(token: str) -> str:
    t = token.upper().replace(" ", "").replace(",", "")
    t = t.replace(":00", "")
    t = t.lstrip("0") or "0"
    return t


def extract_tokens(text: str) -> list[str]:
    """Pull money, time, and bare-number tokens out of free text."""
    found: list[str] = []
    spans: list[tuple[int, int]] = []
    for pattern in (MONEY, TIME):
        for m in pattern.finditer(text or ""):
            found.append(m.group())
            spans.append(m.span())
    for m in NUMBER.finditer(text or ""):
        if not any(start <= m.start() < end for start, end in spans):
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
