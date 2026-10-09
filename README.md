SAMPLE DATA: invented hotel, no real guest data.

# Hotel front desk helper

Placeholder. Full README arrives in stage 5.

## Run steps

Copy `.env.example` to `.env` and fill in `GROQ_API_KEY` and
`GROQ_MODEL` before the last step. Then open
http://127.0.0.1:8000/api/health.

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

## What it does

TODO in stage 5.

## What is cut and why

TODO in stage 5.

## Test results

TODO in stage 4.

## Assumptions

- Stage 2: the store is in-memory, so restarts lose chats, request cards, and the human queue. Fine for a demo, stated here instead of hidden.
- Request acknowledgments are fixed templates, not model text, so the bot cannot accidentally promise a late checkout or early check-in.
- Human handoffs are canned, except two sentences taken verbatim from the sheet (service animals, third-party bookings).
- The number check covers digit tokens, $ amounts, and clock times. Number words ("eleven") can slip past it. The phrase prompt plus temperature 0 are the mitigation.
- Model provider is Groq. llm.py calls the Groq chat API, with the key from GROQ_API_KEY and the model name from GROQ_MODEL (default openai/gpt-oss-120b). Temperature 0, reasoning effort low. The scored eval ran on openai/gpt-oss-120b. The free tier allows 30 requests and 8,000 tokens per minute and 1,000 requests per day for this model, so eval results apply to that model and budget only.
- Shared demo: one memory store and no login, so every visitor sees every chat. Both pages warn against entering real personal information.
- The staff reset button clears chats, request cards, the human queue, the blocked log, and rate-limit counters. It does not reset the daily model-call budget.

## Next steps

TODO in stage 5.
