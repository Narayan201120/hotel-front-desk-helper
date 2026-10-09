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
FEE_RE = re.compile(
    r"\b(fee|fees|charge|charges|charged|cost|costs|price|prices|how much)\b",
    re.IGNORECASE,
)

HUMAN_REPLY = "Thanks — a team member will reply here shortly. I've passed along our full chat."
CAP_NOTICE = "Daily model limit reached. The night team will handle all chats until it resets."
NO_PROMISE = (
    "They will approve or decline it, and you will see the decision here. "
    "I can't promise it in advance."
)

REQUEST_LABELS = {"late_checkout": "late checkout", "early_checkin": "early check-in"}
MULTI_PARTIAL = (
    "One part of your question needs a person. "
    "I've asked a team member to answer that part here."
)


def _catalog(sheet: FactSheet) -> list[dict]:
    return [{"id": fid, "label": f["label"], "text": f["answer"]} for fid, f in sheet.facts.items()]


def _sheet_allowed(sheet: FactSheet) -> set[str]:
    return build_allowed(sheet.all_values_entries())


def _guest_allowed(message: str) -> set[str]:
    return {normalize_token(t) for t in extract_tokens(message)}


def _human(store: Store, session: dict, reason: str, reply: str = HUMAN_REPLY,
           facts_cited: list[str] | None = None, notice: str | None = None) -> dict:
    store.add_message(session, "assistant", reply, facts_cited or [])
    store.queue_human(session, reason)
    return {
        "session_id": session["id"],
        "route": "human",
        "reply": reply,
        "facts_cited": facts_cited or [],
        "request_id": None,
        "request_status": None,
        "notice": notice,
    }


def _extract_details(message: str, clf: dict) -> tuple[str | None, str | None]:
    # Candidates come from guest-verbatim regex first, classifier second.
    # A candidate counts only if its normalized form already appears in the
    # guest message. Classifier-invented values fail closed to missing.
    guest_toks = {normalize_token(t) for t in extract_tokens(message or "")}
    room_raw = None
    m = ROOM_RE.search(message or "")
    if m:
        room_raw = m.group(1)
    elif clf.get("room"):
        room_raw = clf.get("room")
    time_raw = None
    m = TIME_RE.search(message or "")
    if m:
        time_raw = m.group(0)
    elif clf.get("time"):
        time_raw = clf.get("time")
    room = _format_room(room_raw)
    if room is not None and normalize_token(room) not in guest_toks:
        room = None
    time = _format_time(time_raw)
    if time is not None and normalize_token(time) not in guest_toks:
        time = None
    return room, time


def _format_room(raw: str | None) -> str | None:
    room = (raw or "").strip()
    return room if re.fullmatch(r"\d{1,4}", room) else None


def _format_time(raw: str | None) -> str | None:
    """Canonicalize an extracted time to 'H:MM AM/PM'. None if unparseable."""
    if not raw:
        return None
    t = raw.strip().lower().replace(".", "")
    if t == "noon":
        return "12:00 PM"
    if t == "midnight":
        return "12:00 AM"
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*([ap])m", t)
    if not m:
        return None
    hour, minute, suffix = int(m.group(1)), m.group(2) or "00", m.group(3).upper() + "M"
    if not (1 <= hour <= 12 and minute.isdigit() and int(minute) < 60):
        return None
    return f"{hour}:{minute} {suffix}"


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
    # Guest numbers count in acks. Verified card values are guest tokens by
    # construction, so no extra allowance is needed or given.
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
        "notice": None,
    }


def _has_money_value(sheet: FactSheet, fact_id: str) -> bool:
    fact = sheet.get(fact_id)
    return bool(fact) and any(
        str(v).strip().startswith("$") for v in fact.get("values", []) or []
    )


def _fee_without_price(sheet: FactSheet, message: str, route: str,
                       fact_ids: list[str], request_type: str | None) -> bool:
    # Deterministic rule, checked before routing: a question about a fee,
    # charge, cost, or price whose topic fact lists no money value goes to
    # a human, even if the classifier saw a request. The model must never
    # get the chance to invent the amount.
    if route == "human" or not FEE_RE.search(message or ""):
        return False
    topic_ids = list(fact_ids)
    if route in ("request", "multi") and request_type in REQUEST_LABELS:
        topic_ids.append(request_type)
    if not topic_ids:
        return False
    return not any(_has_money_value(sheet, f) for f in topic_ids)


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
    except llm.DailyCapReached:
        return _human(store, session, "model-unavailable", notice=CAP_NOTICE)
    except llm.ModelError:
        return _human(store, session, "model-unavailable")

    route = clf.get("route", "human")
    fact_ids = [f for f in clf.get("facts", []) if sheet.get(f)]

    if _fee_without_price(sheet, message, route, fact_ids, clf.get("request_type")):
        return _human(store, session, "fee-not-in-sheet")

    if route == "fact":
        if not fact_ids:
            return _human(store, session, "no-known-fact")
        try:
            reply = llm.phrase(message, [sheet.answer_text(f) for f in fact_ids])
        except llm.ModelError:
            return _human(store, session, "model-unavailable")
        if llm.MISSING_FACT_SENTINEL in reply:
            store.log_blocked(session["id"], reply, "missing-fact")
            logger.warning("blocked sentinel reply (session=%s)", session["id"])
            reply = None
        else:
            # Fact answers: sheet values only. Guest-typed numbers stay out.
            # The number-echo rule and the ban list below are unchanged.
            ok, reason = check_reply(reply, _sheet_allowed(sheet))
            if not ok:
                store.log_blocked(session["id"], reply, reason)
                logger.warning("blocked reply (session=%s, reason=%s)", session["id"], reason)
                reply = None
        if reply is None:
            # Verbatim fallback: serve the sheet text itself. It passes the
            # self-test by construction; verify anyway, escalate if it fails.
            reply = "\n\n".join(sheet.answer_text(f) for f in fact_ids)
            ok, reason = check_reply(reply, _sheet_allowed(sheet))
            if not ok:
                return _human(store, session, f"guardrail:{reason}")
        store.add_message(session, "assistant", reply, fact_ids)
        return {
            "session_id": session["id"],
            "route": "fact",
            "reply": reply,
            "facts_cited": fact_ids,
            "request_id": None,
            "request_status": None,
            "notice": None,
        }

    if route in ("request", "multi"):
        fact_reply = None
        partial = False
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
                    logger.warning("blocked multi fact part (session=%s, reason=%s)", session["id"], reason)
                    fact_reply = None
            # Never silently drop the fact intent: the request card is still
            # created below, and the reply says a person will cover the rest.
            partial = not fact_reply
        result = _handle_request(store, sheet, session, message, clf, route, fact_reply, fact_ids)
        if partial:
            result["reply"] = result["reply"] + "\n\n" + MULTI_PARTIAL
            session["messages"][-1]["text"] = result["reply"]
            store.queue_human(session, "multi-partial")
        return result

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
