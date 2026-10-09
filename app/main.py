"""FastAPI app: guest chat API plus staff request queue and human queue."""
from __future__ import annotations

import os
import time
from collections import deque
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import service
from app.facts import FactSheet
from app.store import Store

app = FastAPI(title="Hotel front desk helper")
store = Store()
sheet = FactSheet()
STATIC = Path(__file__).parent / "static"

# In-memory per-IP rate limit. Single persistent process only: it resets on
# restart and does not share across workers. See README Assumptions.
_hits: dict[str, deque] = {}


def _rate_limit_per_minute() -> int:
    try:
        return int(os.environ.get("RATE_LIMIT_PER_MINUTE", "60"))
    except ValueError:
        return 60


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate_limit(request: Request) -> None:
    now = time.time()
    ip = _client_ip(request)
    window = _hits.setdefault(ip, deque())
    while window and window[0] <= now - 60:
        window.popleft()
    if len(window) >= _rate_limit_per_minute():
        raise HTTPException(status_code=429, detail="Too many requests. Try again shortly.")
    window.append(now)


class ChatIn(BaseModel):
    session_id: str | None = None
    message: str = Field(min_length=1, max_length=500)


class DecisionIn(BaseModel):
    decision: str


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/")
def guest_page() -> FileResponse:
    return FileResponse(STATIC / "guest.html")


@app.get("/staff")
def staff_page() -> FileResponse:
    return FileResponse(STATIC / "staff.html")


@app.get("/api/facts")
def fact_labels() -> dict:
    return {"facts": [{"id": fid, "label": f["label"]} for fid, f in sheet.facts.items()]}


@app.post("/api/chat")
def chat(body: ChatIn, request: Request) -> dict:
    _check_rate_limit(request)
    return service.handle_message(store, sheet, body.message, body.session_id)


@app.get("/api/chat/{session_id}")
def chat_state(session_id: str) -> dict:
    session = store.sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="unknown session")
    return {
        "session_id": session_id,
        "messages": session["messages"],
        "requests": store.session_requests(session_id),
    }


@app.get("/api/staff/requests")
def staff_requests() -> dict:
    return {"requests": store.open_requests()}


@app.post("/api/staff/requests/{request_id}/decision")
def staff_decision(request_id: str, body: DecisionIn) -> dict:
    try:
        card = service.decide(store, request_id, body.decision)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"request": card}


@app.get("/api/staff/queue")
def staff_queue() -> dict:
    return {"queue": store.open_queue()}


@app.post("/api/staff/queue/{entry_id}/resolve")
def staff_resolve(entry_id: str) -> dict:
    entry = store.resolve_queue(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="unknown queue entry")
    return {"entry": entry}


@app.get("/api/staff/blocked")
def staff_blocked() -> dict:
    return {"blocked": store.blocked}


@app.post("/api/staff/reset")
def staff_reset() -> dict:
    # Demo only: clears chats, cards, queue, blocked log, and rate counters.
    # It does not reset the daily model-call budget.
    global store
    store = Store()
    _hits.clear()
    return {"ok": True}
