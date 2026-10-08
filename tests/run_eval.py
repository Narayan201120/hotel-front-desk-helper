"""Run all 40 test questions against the API and score them.

Scoring uses the expected values in tests/test_questions.json, never the
model's own output. Number/time/fee tokens in replies are checked against
the fact sheet with the same rule the backend enforces: guest-typed numbers
count only in request and multi replies.

Needs GEMINI_API_KEY and GEMINI_MODEL in the environment.

Usage:
    py tests/run_eval.py
Exit code 1 when anything mismatches.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.facts import FactSheet  # noqa: E402
from app.guardrails import BANNED_WORDS, build_allowed, extract_tokens, normalize_token  # noqa: E402
from app.store import Store  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def run_eval() -> int:
    questions = json.load(open(ROOT / "tests" / "test_questions.json", encoding="utf-8"))
    sheet = FactSheet()
    sheet_allowed = build_allowed(sheet.all_values_entries())
    main.store = Store()
    client = TestClient(main.app)

    resolved = staff_req = human_q = invented = 0
    mismatches = []
    for q in questions:
        before = len(main.store.blocked)
        try:
            r = client.post("/api/chat", json={"message": q["question"]})
            body = r.json()
        except Exception as exc:  # noqa: BLE001 - a crashed question is a mismatch
            print(f"{q['id']} [{q['category']}] REQUEST FAILED: {exc}", flush=True)
            mismatches.append(q["id"])
            continue
        new_blocks = main.store.blocked[before:]
        actual_route = body.get("route")
        actual_facts = body.get("facts_cited", [])
        guest_allowed = {normalize_token(t) for t in extract_tokens(q["question"])}
        if actual_route in ("request", "multi"):
            allowed = sheet_allowed | guest_allowed
        else:
            allowed = sheet_allowed
        unknown = [
            t
            for t in extract_tokens(body.get("reply", ""))
            if normalize_token(t) not in allowed
        ]
        banned = BANNED_WORDS.search(body.get("reply", "") or "")
        route_ok = actual_route == q["expected_route"]
        facts_ok = set(actual_facts) == set(q["expected_facts"])
        bad = (not route_ok) or (not facts_ok) or unknown or banned
        if actual_route == "fact":
            resolved += 1
        if actual_route in ("request", "multi") and body.get("request_id"):
            staff_req += 1
        if actual_route == "human":
            human_q += 1
        if unknown or banned:
            invented += 1
        mark = " MISMATCH" if bad else ""
        print(
            f"{q['id']} [{q['category']}] "
            f"exp={q['expected_route']}/{q['expected_facts']} "
            f"got={actual_route}/{actual_facts}{mark}",
            flush=True,
        )
        for b in new_blocks:
            print(f"    blocked: {b['reason']}: {b['reply'][:100]}", flush=True)
        if unknown:
            print(f"    unknown tokens: {unknown}", flush=True)
        if banned:
            print(f"    banned word: {banned.group()}", flush=True)
        if bad:
            mismatches.append(q["id"])
    print(
        f"\nresolved by bot: {resolved}, staff requests: {staff_req}, "
        f"human queue: {human_q}, invented-fact answers: {invented}",
        flush=True,
    )
    print(f"mismatches: {mismatches if mismatches else 'none'}", flush=True)
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(run_eval())
