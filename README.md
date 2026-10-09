SAMPLE DATA: invented hotel, no real guest data.

# Hotel front desk helper

## What it does

A guest chat page plus a staff screen for a fictional 80-room economy
hotel. The bot handles three routes. Facts (parking, pets, breakfast
hours, cancellation policy) are answered from one YAML sheet, and the
reply names the fact it came from. Requests (late checkout, early
check-in) are never promised: the bot takes room and time, opens a card,
and the guest sees the staff Approve or Decline. Everything else (money,
refunds, booking changes, complaints, off-topic) goes to a "needs a
human" queue with the full chat. A deterministic check runs after every
model reply: any number, time, or fee not in the sheet blocks the reply
and escalates instead. With no key configured, the chips replay saved
eval responses labeled "Recorded run, not live".

## Run steps

Copy `.env.example` to `.env` and fill in `GROQ_API_KEY` and
`GROQ_MODEL` first. Then open http://127.0.0.1:8000 for guests and
http://127.0.0.1:8000/staff for staff.

PowerShell (5 commands):

```
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

Bash (5 commands):

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

## Model and eval

Provider Groq, model openai/gpt-oss-120b, temperature 0, reasoning
effort low. Test set: 40 questions (22 normal, 13 tricky, 5 off-topic)
in tests/test_questions.json, scored by tests/run_eval.py against the
expected values, never the model's own output. Full per-question detail
in tests/results.json.

## Eval results (2026-10-09, Groq openai/gpt-oss-120b)

Resolved as fact 20, request 5, multi 2, human 13. Inconclusive 0.
Mismatches none. Blocked replies 0. Answers with a fact not in the
sheet none. Buckets: clean pass 40, blocked-then-verbatim 0,
degraded ack 0, mismatch 0, inconclusive 0. The guardrail never fired
on real model output in this run. Every mismatch from earlier runs
(n05 number echo, n23 "booked" ban, t02 fee-as-request, t13 dropped
parking part, t07 sentinel under injection framing) was fixed and the
fixes above re-verified in this run.

## Held-out check (2026-10-09, same model)

10 new questions written before running (tests/heldout_questions.json),
scored the same way, detail in tests/heldout_results.json. 8 of 10 match
(fact 3, request 1, multi 1, human 5, inconclusive 0, invented 0).
Misses: h02 (the classifier sent the over-limit dog to a human instead of
answering no from the sheet), h04 (the fee rule fired on the word
"charge" in "free of charge" and escalated an answerable question). No
product change was made in response.

## What was cut and why

- Phone and voice. The night clerk already answers phones; the demo covers the chat queue only.
- Property management system integration. No test system exists, and availability stays a human call.
- Live availability. Late checkout and early check-in depend on occupancy, so only staff decide.
- Payments, refunds, booking changes. Money needs a human; the bot never states or implies them.
- Multi-hotel admin. One config file per hotel (data/hotel_facts.yaml).
- Login. Demo build with a shared store; both pages say so.
- SMS. The chat page is the only channel.

## Assumptions

- The store is in-memory, so restarts lose chats, request cards, and the human queue. Single persistent process only.
- Request acknowledgments are fixed templates, never model text, so the bot cannot promise anything.
- Ack room and time are canonicalized in code ("noon" to "12:00 PM") and must already appear in the guest message, or they count as missing.
- On a fact route, a blocked or empty phrasing falls back to the sheet text verbatim, with the block still logged.
- Human handoffs are canned, except two sentences taken verbatim from the sheet (service animals, third-party bookings).
- The number check covers digit tokens, $ amounts, clock times, and number words zero through thousand. Ordinals ("first") are not covered.
- The free tier allows 30 requests and 8,000 tokens per minute and 1,000 requests per day for this model. Over that, calls fall back to human after retries. Results apply to this model and budget only.
- Shared demo: one store and no login, so every visitor sees every chat. Both pages warn against real personal information. The staff reset button clears demo data but not the daily model budget.

## Known limits

- The classifier varies between runs (t13 and n23 routed differently across runs). Retries and fallbacks cover it, but exact routing is not deterministic.
- The banned-word list overreaches by design: "you booked" trips the "booked" ban, as seen on n23 before the verbatim fallback existed.
- Verbatim fallback answers are sheet text, not conversational phrasing. A guest who asks twice may get a stiffer answer the second time.
- Held-out h02: the classifier routes "no" answers to a human even when it cites the right fact.
- Held-out h04: the fee rule matches the word "charge" inside "free of charge", escalating questions the sheet could answer. Kept as ordered.
- Staff can resolve queue entries but cannot reply into the guest chat from the screen.
- Recorded-run mode replays saved answers. Anything off the chips needs a key.

## Next steps

- Let staff reply into queued chats from the staff screen.
- Persist the store (SQLite) so restarts and multi-worker deploys keep state.
- Staff login before any real deployment.
- Refresh data/recorded_runs.json whenever the sheet or model changes.
- Deploy the Render blueprint in render.yaml.
