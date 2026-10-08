"""In-memory demo store. No login, no persistence.

Restarts lose chats, request cards, and the human queue. That is a stated
demo limit, not an oversight. See README Assumptions.
"""
from __future__ import annotations

import time
import uuid


def _now() -> float:
    return time.time()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class Store:
    def __init__(self) -> None:
        self.sessions: dict[str, dict] = {}
        self.requests: dict[str, dict] = {}
        self.human_queue: list[dict] = []
        self.blocked: list[dict] = []

    def get_session(self, session_id: str | None) -> dict:
        session = self.sessions.get(session_id or "")
        if session is None:
            session = {"id": session_id or _id("s"), "messages": [], "created_at": _now()}
            self.sessions[session["id"]] = session
        return session

    def add_message(self, session: dict, role: str, text: str, facts_cited: list[str] | None = None) -> dict:
        message = {"role": role, "text": text, "facts_cited": facts_cited or [], "at": _now()}
        session["messages"].append(message)
        return message

    def create_request(self, session_id: str, request_type: str, room: str | None, time: str | None) -> dict:
        card = {
            "id": _id("req"),
            "session_id": session_id,
            "type": request_type,
            "room": room,
            "time": time,
            "status": "pending",
            "created_at": _now(),
        }
        self.requests[card["id"]] = card
        return card

    def session_requests(self, session_id: str) -> list[dict]:
        return [r for r in self.requests.values() if r["session_id"] == session_id]

    def get_request(self, request_id: str) -> dict | None:
        return self.requests.get(request_id)

    def open_requests(self) -> list[dict]:
        return sorted(self.requests.values(), key=lambda r: r["created_at"], reverse=True)

    def queue_human(self, session: dict, reason: str) -> dict:
        entry = {
            "id": _id("q"),
            "session_id": session["id"],
            "reason": reason,
            "chat": [dict(m) for m in session["messages"]],
            "status": "open",
            "created_at": _now(),
        }
        self.human_queue.append(entry)
        return entry

    def open_queue(self) -> list[dict]:
        return [e for e in self.human_queue if e["status"] == "open"]

    def resolve_queue(self, entry_id: str) -> dict | None:
        for entry in self.human_queue:
            if entry["id"] == entry_id:
                entry["status"] = "resolved"
                return entry
        return None

    def log_blocked(self, session_id: str, reply: str, reason: str) -> dict:
        record = {"session_id": session_id, "reply": reply, "reason": reason, "at": _now()}
        self.blocked.append(record)
        return record
