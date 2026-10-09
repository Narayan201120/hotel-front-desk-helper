"""Run all 40 test questions against the API and score them.

Scoring uses the expected values in tests/test_questions.json, never the
model's own output. Number/time/fee tokens in replies are checked against
the fact sheet with the same rule the backend enforces: guest-typed numbers
count only in request and multi replies.

Pacing defaults stay under the Groq free tier for gpt-oss-120b
(30 req/min, 1000 req/day): 15s between questions is at most ~8 model
calls per minute. A question that falls back because the model call itself
failed (queue reason model-unavailable) is re-run once at the end and
marked inconclusive if it still fails. Inconclusive questions are never
counted as bot results and never counted as mismatches.

Needs GROQ_API_KEY in the environment (GROQ_MODEL optional).

Usage:
    py tests/run_eval.py [--chunk 10] [--pause-secs 15] [--chunk-pause-secs 60]
                         [--out tests/results.json]
Exit code 1 when any conclusive question mismatches.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app import llm, main  # noqa: E402
from app.facts import FactSheet  # noqa: E402
from app.guardrails import BANNED_WORDS, build_allowed, extract_tokens, normalize_token  # noqa: E402
from app.store import Store  # noqa: E402
from unittest.mock import patch  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REQUEST_ROUTES = ("request", "multi")


def cause_of(exc: Exception | None) -> str | None:
    """Classify a model-call failure. Eval-only instrumentation."""
    if exc is None:
        return "classify-level (transport ok; likely bad JSON)"
    code = getattr(exc, "code", getattr(exc, "status_code", None))
    if code == 429 or "rate_limit" in str(exc).lower():
        return "429"
    if "timeout" in type(exc).__name__.lower() or "timed out" in str(exc).lower():
        return "timeout"
    if isinstance(exc, llm.ModelError) and "empty" in str(exc).lower():
        return "empty-output"
    if isinstance(code, int) and 500 <= code <= 599:
        return f"5xx ({code})"
    return f"other: {type(exc).__name__}: {str(exc)[:100]}"


def ask(client: TestClient, sheet_allowed: set[str], store: Store, q: dict) -> dict:
    before_blocked = len(store.blocked)
    seen: dict = {"exc": None, "clf": None}
    real_gen = llm._generate
    real_classify = llm.classify

    def spy(api_client, model, system, text):
        try:
            return real_gen(api_client, model, system, text)
        except Exception as e:  # noqa: BLE001 - recorded, then re-raised
            seen["exc"] = e
            raise

    def clf_spy(message, catalog):
        out = real_classify(message, catalog)
        seen["clf"] = out
        return out

    try:
        with patch.object(llm, "_generate", side_effect=spy), patch.object(
            llm, "classify", side_effect=clf_spy
        ):
            r = client.post("/api/chat", json={"message": q["question"]})
        body = r.json()
    except Exception as exc:  # noqa: BLE001 - a crashed question is recorded, not raised
        return {
            "id": q["id"],
            "category": q["category"],
            "question": q["question"],
            "expected_route": q["expected_route"],
            "expected_facts": q["expected_facts"],
            "actual_route": "error",
            "actual_facts": [],
            "reply": f"REQUEST FAILED: {exc}",
            "request_id": None,
            "blocked": [],
            "queue_reason": "request-failed",
            "match": False,
            "reran": False,
            "inconclusive": False,
            "failure_cause": "request-failed",
            "ack_has_room_and_time": None,
        }
    new_blocks = store.blocked[before_blocked:]
    actual_route = body.get("route")
    actual_facts = body.get("facts_cited", [])
    queue_reason = None
    for entry in reversed(store.human_queue):
        if entry["session_id"] == body.get("session_id"):
            queue_reason = entry["reason"]
            break
    guest_allowed = {normalize_token(t) for t in extract_tokens(q["question"])}
    if actual_route in REQUEST_ROUTES:
        allowed = sheet_allowed | guest_allowed
    else:
        allowed = sheet_allowed
    unknown = [
        t
        for t in extract_tokens(body.get("reply", ""))
        if normalize_token(t) not in allowed
    ]
    banned = BANNED_WORDS.search(body.get("reply", "") or "")
    inconclusive = actual_route == "human" and queue_reason == "model-unavailable"
    cause = cause_of(seen["exc"]) if inconclusive else None
    ack_ok = None
    if actual_route in REQUEST_ROUTES and body.get("request_id"):
        card = store.get_request(body["request_id"])
        ack_ok = bool(card and card.get("room") and card.get("time"))
    route_ok = actual_route == q["expected_route"]
    facts_ok = set(actual_facts) == set(q["expected_facts"])
    bad = (not route_ok) or (not facts_ok) or unknown or banned
    return {
        "id": q["id"],
        "category": q["category"],
        "question": q["question"],
        "expected_route": q["expected_route"],
        "expected_facts": q["expected_facts"],
        "actual_route": actual_route,
        "actual_facts": actual_facts,
        "reply": body.get("reply", ""),
        "request_id": body.get("request_id"),
        "blocked": [{"reason": b["reason"], "reply": b["reply"]} for b in new_blocks],
        "queue_reason": queue_reason,
        "unknown_tokens": unknown,
        "banned_word": banned.group() if banned else None,
        "match": (not inconclusive) and not bad,
        "reran": False,
        "inconclusive": inconclusive,
        "failure_cause": cause,
        "ack_has_room_and_time": ack_ok,
        # Eval-only debug: what the classifier returned. Never shown to
        # guests. Contains routes and fact ids only, never keys.
        "classifier": seen["clf"],
    }


def print_row(rec: dict) -> None:
    mark = ""
    if rec["inconclusive"]:
        mark = " INCONCLUSIVE"
    elif not rec["match"]:
        mark = " MISMATCH"
    print(
        f"{rec['id']} [{rec['category']}] "
        f"exp={rec['expected_route']}/{rec['expected_facts']} "
        f"got={rec['actual_route']}/{rec['actual_facts']}{mark}",
        flush=True,
    )
    for b in rec["blocked"]:
        print(f"    blocked: {b['reason']}: {b['reply'][:120]}", flush=True)
    if rec["unknown_tokens"]:
        print(f"    unknown tokens: {rec['unknown_tokens']}", flush=True)
    if rec["banned_word"]:
        print(f"    banned word: {rec['banned_word']}", flush=True)
    if rec.get("failure_cause"):
        print(f"    cause: {rec['failure_cause']}", flush=True)
    if rec.get("classifier"):
        c = rec["classifier"]
        print(f"    clf: route={c.get('route')} facts={c.get('facts')} type={c.get('request_type')} room={c.get('room')} time={c.get('time')}", flush=True)


def run_eval(chunk: int, pause_secs: float, chunk_pause_secs: float, out: str,
             only: list[str] | None = None, merge: bool = False,
             questions_file: str = "tests/test_questions.json") -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    all_questions = json.load(open(ROOT / questions_file, encoding="utf-8"))
    questions = all_questions
    if only:
        wanted = set(only)
        questions = [q for q in all_questions if q["id"] in wanted]
        missing = wanted - {q["id"] for q in questions}
        if missing:
            print(f"unknown ids: {sorted(missing)}", flush=True)
            return 2
    sheet = FactSheet()
    sheet_allowed = build_allowed(sheet.all_values_entries())
    main.store = Store()
    client = TestClient(main.app)
    model = os.environ.get("GROQ_MODEL") or llm.DEFAULT_MODEL

    records = []
    for i, q in enumerate(questions):
        if i:
            time.sleep(chunk_pause_secs if i % chunk == 0 else pause_secs)
        rec = ask(client, sheet_allowed, main.store, q)
        records.append(rec)
        print_row(rec)

    rerun_ids = [r["id"] for r in records if r["inconclusive"]]
    if rerun_ids:
        print(f"\nre-running {len(rerun_ids)} inconclusive questions: {rerun_ids}", flush=True)
        time.sleep(chunk_pause_secs)
        by_id = {r["id"]: r for r in records}
        for n, qid in enumerate(rerun_ids):
            if n:
                time.sleep(pause_secs)
            q = next(x for x in questions if x["id"] == qid)
            rec = ask(client, sheet_allowed, main.store, q)
            rec["reran"] = True
            by_id[qid] = rec
            print_row(rec)
        records = [by_id[q["id"]] for q in questions]

    if merge:
        existing = json.load(open(ROOT / out, encoding="utf-8"))
        merged = {r["id"]: r for r in existing["questions"]}
        for rec in records:
            merged[rec["id"]] = rec
        records = [merged[q["id"]] for q in all_questions]

    by_route = {"fact": 0, "request": 0, "multi": 0, "human": 0}
    for r in records:
        if r["actual_route"] in by_route:
            by_route[r["actual_route"]] += 1
    blocked_total = sum(len(r["blocked"]) for r in records)
    invented = [r["id"] for r in records if r["unknown_tokens"] or r["banned_word"]]
    mismatches = [r["id"] for r in records if not r["match"] and not r["inconclusive"]]
    inconclusive = [r["id"] for r in records if r["inconclusive"]]
    buckets = {
        "clean_pass": [],
        "blocked_then_verbatim": [],
        "degraded_ack": [],
        "mismatch": [],
        "inconclusive": [],
    }
    for r in records:
        if r["inconclusive"]:
            buckets["inconclusive"].append(r["id"])
            r["bucket"] = "inconclusive"
        elif not r["match"]:
            buckets["mismatch"].append(r["id"])
            r["bucket"] = "mismatch"
        elif not r["blocked"]:
            buckets["clean_pass"].append(r["id"])
            r["bucket"] = "clean_pass"
        elif r["actual_route"] == "fact":
            buckets["blocked_then_verbatim"].append(r["id"])
            r["bucket"] = "blocked_then_verbatim"
        else:
            buckets["degraded_ack"].append(r["id"])
            r["bucket"] = "degraded_ack"
    assert sum(len(v) for v in buckets.values()) == len(records), "buckets must cover every question"

    summary = {
        "resolved_as_fact": by_route["fact"],
        "resolved_as_request": by_route["request"],
        "resolved_as_multi": by_route["multi"],
        "resolved_as_human": by_route["human"] - len(inconclusive),
        "inconclusive_count": len(inconclusive),
        "blocked_count": blocked_total,
        "invented_fact_answers": invented,
        "mismatches": mismatches,
        "inconclusive": inconclusive,
        "buckets": buckets,
    }
    print("\n--- summary ---", flush=True)
    print(f"resolved as fact:    {by_route['fact']}", flush=True)
    print(f"resolved as request: {by_route['request']}", flush=True)
    print(f"resolved as multi:   {by_route['multi']}", flush=True)
    print(f"resolved as human:   {by_route['human'] - len(inconclusive)}", flush=True)
    print(f"blocked replies:     {blocked_total}", flush=True)
    print(f"invented facts:      {invented if invented else 'none'}", flush=True)
    print(f"mismatches:          {mismatches if mismatches else 'none'}", flush=True)
    for name in ("clean_pass", "blocked_then_verbatim", "degraded_ack", "mismatch", "inconclusive"):
        print(f"bucket {name}: {len(buckets[name])} {buckets[name]}", flush=True)
    print(f"inconclusive:        {len(inconclusive)} {inconclusive if inconclusive else ''}", flush=True)

    result = {
        "model": model,
        "provider": "groq",
        "date": datetime.date.today().isoformat(),
        "summary": summary,
        "questions": records,
    }
    out_path = ROOT / out
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"wrote {out_path}", flush=True)
    return 1 if mismatches else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunk", type=int, default=10)
    parser.add_argument("--pause-secs", type=float, default=15)
    parser.add_argument("--chunk-pause-secs", type=float, default=60)
    parser.add_argument("--out", default="tests/results.json")
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--merge", action="store_true")
    parser.add_argument("--questions", default="tests/test_questions.json")
    args = parser.parse_args()
    sys.exit(run_eval(args.chunk, args.pause_secs, args.chunk_pause_secs, args.out,
                      args.only, args.merge, args.questions))
