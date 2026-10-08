"""FastAPI app: guest chat API plus staff request queue and human queue."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app import service
from app.facts import FactSheet
from app.store import Store

app = FastAPI(title="Hotel front desk helper")
store = Store()
sheet = FactSheet()


class ChatIn(BaseModel):
    session_id: str | None = None
    message: str = Field(min_length=1)


class DecisionIn(BaseModel):
    decision: str


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/chat")
def chat(body: ChatIn) -> dict:
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
