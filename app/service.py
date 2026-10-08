"""Chat orchestration: classify, phrase, guard, route.

Rules enforced here:
- Facts come only from the sheet. The model phrases, never adds.
- Requests are never promised. Acknowledgments are fixed templates, and
  the words approved/confirmed/booked only ever appear in the staff
  decision message, which is a server template written after a staff click.
- Anything else, any model failure, or any failed guardrail check routes
  the chat to the human queue with a canned reply. The guest never sees
  an error and the bot never guesses.
"""
from __future__ import annotations

import logging
import re

from app import llm
from app.facts import FactSheet
from app.guardrails import build_allowed, check_reply, extract_tokens, normalize_token
from app.store import Store

logger = logging.getLogger(__name__)

ROOM_RE = re.compile(r"\broom\s*(\d{1,4})\b", re.IGNORECASE)
TIME_RE = re.compile(r"\b\d{1,2}(?::\d{2})?\s?[AP]\.?M\.?\b|\bnoon\b|\bmidnight\b", re.IGNORECASE)
THIRD_PARTY_RE = re.compile(
    r"expedia|booking\.com|hotels\.com|priceline|orbitz|travelocity|kayak|airbnb|third.party",
    re.IGNORECASE,
)

HUMAN_REPLY = "Thanks — a team member will reply here shortly. I've passed along our full chat."
NO_PROMISE = (
    "They will approve or decline it, and you will see the decision here. "
    "I can't promise it in advance."
)

REQUEST_LABELS = {"late_checkout": "late checkout", "early_checkin": "early check-in"}


def _catalog(sheet: FactSheet) -> list[dict]:
    return [{"id": fid, "label": f["label"]} for fid, f in sheet.facts.items()]


def _sheet_allowed(sheet: FactSheet) -> set[str]:
    return build_allowed(sheet.all_values_entries())


def _guest_allowed(message: str) -> set[str]:
    return {normalize_token(t) for t in extract_tokens(message)}


def _human(store: Store, session: dict, reason: str, reply: str = HUMAN_REPLY,
           facts_cited: list[str] | None = None) -> dict:
    store.add_message(session, "assistant", reply, facts_cited or [])
    store.queue_human(session, reason)
    return {
        "session_id": session["id"],
        "route": "human",
        "reply": reply,
        "facts_cited": facts_cited or [],
        "request_id": None,
        "request_status": None,
    }


def _blocked(store: Store, session: dict, reply: str, reason: str) -> dict:
    store.log_blocked(session["id"], reply, reason)
    logger.warning("blocked reply (session=%s, reason=%s): %r", session["id"], reason, reply)
    return _human(store, session, f"guardrail:{reason}")


def _extract_details(message: str, clf: dict) -> tuple[str | None, str | None]:
    room = clf.get("room")
    if not room:
        m = ROOM_RE.search(message or "")
        room = m.group(1) if m else None
    time = clf.get("time")
    if not time:
        m = TIME_RE.search(message or "")
        time = m.group(0) if m else None
    return room, time


def _infer_type(message: str, clf_type: str | None) -> str:
    if clf_type in REQUEST_LABELS:
        return clf_type
    if re.search(r"early|check.?in|arriv", message or "", re.IGNORECASE):
        return "early_checkin"
    return "late_checkout"


def _upsert_request(store: Store, session: dict, request_type: str,
                    room: str | None, time: str | None) -> dict:
    for card in store.session_requests(session["id"]):
        if card["type"] == request_type and card["status"] == "pending" \
                and (not card["room"] or not card["time"]):
            if room:
                card["room"] = room
            if time:
                card["time"] = time
            return card
    return store.create_request(session["id"], request_type, room, time)


def _request_ack(request_type: str, room: str | None, time: str | None) -> str:
    # Built only from the extracted room/time values, never from raw guest
    # text. Banned or abusive words in the guest message cannot leak in.
    label = REQUEST_LABELS[request_type]
    parts = []
    if room:
        parts.append(f"room {room}")
    if time:
        parts.append(time)
    detail = f" for {' and '.join(parts)}" if parts else ""
    ask = ""
    if not room or not time:
        need = " and ".join(
            name for name, value in (("room number", room), ("requested time", time)) if not value
        )
        ask = f" Could you share your {need}?"
    return f"Thanks — I've sent your {label} request{detail} to the front desk.{ask} {NO_PROMISE}"


def _handle_request(store: Store, sheet: FactSheet, session: dict, message: str,
                    clf: dict, route: str, fact_reply: str | None = None,
                    fact_ids: list[str] | None = None) -> dict:
    request_type = _infer_type(message, clf.get("request_type"))
    room, time = _extract_details(message, clf)
    card = _upsert_request(store, session, request_type, room, time)
    ack = _request_ack(request_type, card["room"], card["time"])
    allowed = _sheet_allowed(sheet) | _guest_allowed(message)
    ok, reason = check_reply(ack, allowed)
    if not ok:
        store.log_blocked(session["id"], ack, f"request-ack:{reason}")
        logger.warning("blocked request ack (session=%s, reason=%s)", session["id"], reason)
        ack = (
            "Thanks — I've sent your request to the front desk. "
            "They will approve or decline it, and you will see the decision here."
        )
    reply = f"{fact_reply}\n\n{ack}" if fact_reply else ack
    cited = list(fact_ids or []) + [request_type]
    store.add_message(session, "assistant", reply, cited)
    return {
        "session_id": session["id"],
        "route": route,
        "reply": reply,
        "facts_cited": cited,
        "request_id": card["id"],
        "request_status": card["status"],
    }


def _third_party_sentence(sheet: FactSheet) -> str | None:
    answer = sheet.answer_text("cancellation") or ""
    for sentence in answer.split(". "):
        if "third-party" in sentence.lower():
            return sentence.strip().rstrip(".") + "."
    return None


def handle_message(store: Store, sheet: FactSheet, message: str,
                   session_id: str | None = None) -> dict:
    session = store.get_session(session_id)
    store.add_message(session, "guest", message)

    try:
        clf = llm.classify(message, _catalog(sheet))
    except llm.ModelError:
        return _human(store, session, "model-unavailable")

    route = clf.get("route", "human")
    fact_ids = [f for f in clf.get("facts", []) if sheet.get(f)]

    if route == "fact":
        if not fact_ids:
            return _human(store, session, "no-known-fact")
        try:
            reply = llm.phrase(message, [sheet.answer_text(f) for f in fact_ids])
        except llm.ModelError:
            return _human(store, session, "model-unavailable")
        if llm.MISSING_FACT_SENTINEL in reply:
            store.log_blocked(session["id"], reply, "missing-fact")
            return _human(store, session, "missing-fact")
        # Fact answers: sheet values only. Guest-typed numbers stay out.
        ok, reason = check_reply(reply, _sheet_allowed(sheet))
        if not ok:
            return _blocked(store, session, reply, reason)
        store.add_message(session, "assistant", reply, fact_ids)
        return {
            "session_id": session["id"],
            "route": "fact",
            "reply": reply,
            "facts_cited": fact_ids,
            "request_id": None,
            "request_status": None,
        }

    if route in ("request", "multi"):
        fact_reply = None
        if route == "multi" and fact_ids:
            try:
                fact_reply = llm.phrase(message, [sheet.answer_text(f) for f in fact_ids])
            except llm.ModelError:
                fact_reply = None
            if fact_reply and llm.MISSING_FACT_SENTINEL in fact_reply:
                fact_reply = None
            if fact_reply:
                ok, reason = check_reply(fact_reply, _sheet_allowed(sheet))
                if not ok:
                    store.log_blocked(session["id"], fact_reply, reason)
                    return _human(store, session, f"guardrail:{reason}")
        return _handle_request(store, sheet, session, message, clf, route, fact_reply, fact_ids)

    if "service_animals" in fact_ids:
        reply = sheet.answer_text("service_animals") + " I've asked a team member to follow up here with the specifics."
        return _human(store, session, "service-animal", reply, ["service_animals"])

    if fact_ids == ["cancellation"] and THIRD_PARTY_RE.search(message or ""):
        sentence = _third_party_sentence(sheet)
        reply = (sentence + " I've asked a team member to follow up here."
                 if sentence else HUMAN_REPLY)
        return _human(store, session, "third-party-booking", reply, ["cancellation"])

    return _human(store, session, "route-human")


def decide(store: Store, request_id: str, decision: str) -> dict:
    if decision not in ("approved", "declined"):
        raise ValueError("decision must be approved or declined")
    card = store.get_request(request_id)
    if card is None:
        raise KeyError(f"unknown request: {request_id}")
    card["status"] = decision
    session = store.sessions[card["session_id"]]
    label = REQUEST_LABELS[card["type"]]
    detail = f"room {card['room']}" if card["room"] else "your request"
    if card["time"]:
        detail += f" at {card['time']}"
    # Server template written after a staff click. This is the only place
    # these words may appear, and they never pass through the model.
    store.add_message(
        session, "staff", f"Front desk update: your {label} request ({detail}) was {decision}."
    )
    return card
