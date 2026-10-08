"""Stage 2 smoke checks. No API key needed: the model is stubbed.

Covers the guardrail rules agreed for stage 2: invented numbers blocked,
guest numbers kept out of fact answers but allowed in request acks,
banned words blocked, phone/address allowed, every block logged, model
failure routed to a human, and staff decisions visible to the guest.

Run: py tests/smoke_stage2.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app import llm  # noqa: E402
from app import main  # noqa: E402
from app.store import Store  # noqa: E402

PASS = []


def check(name, cond, extra=""):
    assert cond, f"FAIL: {name} {extra}"
    PASS.append(name)
    print(f"ok: {name}")


def fresh_client():
    main.store = Store()
    return TestClient(main.app)


def test_model_failure_routes_human():
    client = fresh_client()
    with patch.object(llm, "_client", side_effect=llm.ModelError("down")):
        r = client.post("/api/chat", json={"message": "Is parking free?"})
    body = r.json()
    check("model-failure-route", body["route"] == "human", body)
    check("model-failure-no-error", "error" not in body["reply"].lower(), body)
    q = client.get("/api/staff/queue").json()["queue"]
    check("model-failure-queued", len(q) == 1 and q[0]["reason"] == "model-unavailable", q)


def test_invented_fee_blocked_and_logged():
    client = fresh_client()
    clf = {"route": "fact", "facts": ["parking"], "request_type": None, "room": None, "time": None}
    with patch.object(llm, "classify", return_value=clf), patch.object(
        llm, "phrase", return_value="Parking costs $40 per night."
    ):
        body = client.post("/api/chat", json={"message": "Is parking free?"}).json()
    check("invented-fee-route", body["route"] == "human", body)
    blocked = client.get("/api/staff/blocked").json()["blocked"]
    check(
        "invented-fee-logged",
        len(blocked) == 1 and blocked[0]["reason"] == "unknown-number-time-fee:$40",
        blocked,
    )


def test_banned_word_blocked():
    client = fresh_client()
    clf = {"route": "fact", "facts": ["checkout"], "request_type": None, "room": None, "time": None}
    with patch.object(llm, "classify", return_value=clf), patch.object(
        llm, "phrase", return_value="Your checkout is approved for 1pm."
    ):
        body = client.post("/api/chat", json={"message": "Can I check out late?"}).json()
    check("banned-word-route", body["route"] == "human", body)
    blocked = client.get("/api/staff/blocked").json()["blocked"]
    check("banned-word-logged", blocked[0]["reason"] == "banned-word:approved", blocked)


def test_guest_number_blocked_in_fact_answer():
    client = fresh_client()
    clf = {"route": "fact", "facts": ["parking"], "request_type": None, "room": None, "time": None}
    with patch.object(llm, "classify", return_value=clf), patch.object(
        llm, "phrase", return_value="Parking for room 214 is free."
    ):
        body = client.post("/api/chat", json={"message": "Room 214, is parking free?"}).json()
    check("guest-number-fact-blocked", body["route"] == "human", body)


def test_confirmation_word_not_banned():
    client = fresh_client()
    clf = {"route": "fact", "facts": ["cancellation"], "request_type": None, "room": None, "time": None}
    reply = "Use the link in your booking confirmation to cancel."
    with patch.object(llm, "classify", return_value=clf), patch.object(
        llm, "phrase", return_value=reply
    ):
        body = client.post("/api/chat", json={"message": "How do I cancel?"}).json()
    check("confirmation-allowed", body["route"] == "fact" and body["reply"] == reply, body)


def test_phone_and_address_allowed():
    client = fresh_client()
    clf = {"route": "fact", "facts": ["front_desk"], "request_type": None, "room": None, "time": None}
    reply = "Call us at (614) 555-0148. We are at 100 Sample Road."
    with patch.object(llm, "classify", return_value=clf), patch.object(
        llm, "phrase", return_value=reply
    ):
        body = client.post("/api/chat", json={"message": "How do I reach you?"}).json()
    check("phone-address-allowed", body["route"] == "fact", body)


def test_request_creates_card_and_decision_visible():
    client = fresh_client()
    clf = {
        "route": "request",
        "facts": ["late_checkout"],
        "request_type": "late_checkout",
        "room": "214",
        "time": "1pm",
    }
    with patch.object(llm, "classify", return_value=clf):
        body = client.post(
            "/api/chat", json={"message": "Room 214, late checkout until 1pm please?"}
        ).json()
    check("request-route", body["route"] == "request", body)
    check("request-ack-echoes", "214" in body["reply"] and "1pm" in body["reply"], body)
    check("request-ack-no-promise", "approved" not in body["reply"].lower(), body)
    cards = client.get("/api/staff/requests").json()["requests"]
    check("request-card", len(cards) == 1 and cards[0]["status"] == "pending", cards)
    dec = client.post(
        f"/api/staff/requests/{cards[0]['id']}/decision", json={"decision": "approved"}
    ).json()
    check("decision-set", dec["request"]["status"] == "approved", dec)
    state = client.get(f"/api/chat/{body['session_id']}").json()
    staff_msgs = [m for m in state["messages"] if m["role"] == "staff"]
    check("decision-visible", any("approved" in m["text"] for m in staff_msgs), state)


def test_multi_intent():
    client = fresh_client()
    clf = {
        "route": "multi",
        "facts": ["parking"],
        "request_type": "late_checkout",
        "room": None,
        "time": "1pm",
    }
    with patch.object(llm, "classify", return_value=clf), patch.object(
        llm, "phrase", return_value="Self-parking in our on-site lot is free."
    ):
        body = client.post(
            "/api/chat", json={"message": "Is parking free, and can I check out at 1pm?"}
        ).json()
    check("multi-route", body["route"] == "multi", body)
    check("multi-facts", body["facts_cited"][:2] == ["parking", "late_checkout"], body)
    check("multi-asks-room", "room number" in body["reply"], body)
    cards = client.get("/api/staff/requests").json()["requests"]
    check("multi-card", len(cards) == 1 and cards[0]["type"] == "late_checkout", cards)


def test_word_number_fee_blocked():
    client = fresh_client()
    clf = {"route": "fact", "facts": ["pets"], "request_type": None, "room": None, "time": None}
    with patch.object(llm, "classify", return_value=clf), patch.object(
        llm, "phrase", return_value="The pet fee is twenty dollars per night."
    ):
        body = client.post("/api/chat", json={"message": "How much is the pet fee?"}).json()
    check("word-fee-blocked", body["route"] == "human", body)
    blocked = client.get("/api/staff/blocked").json()["blocked"]
    check("word-fee-logged", blocked[0]["reason"] == "unknown-number-time-fee:twenty", blocked)


def test_word_time_passes_when_correct():
    client = fresh_client()
    clf = {"route": "fact", "facts": ["checkout"], "request_type": None, "room": None, "time": None}
    reply = "Standard check-out is eleven AM Eastern."
    with patch.object(llm, "classify", return_value=clf), patch.object(
        llm, "phrase", return_value=reply
    ):
        body = client.post("/api/chat", json={"message": "What time is checkout?"}).json()
    check("word-time-passes", body["route"] == "fact" and body["reply"] == reply, body)


def test_bare_word_time_blocked():
    client = fresh_client()
    clf = {"route": "fact", "facts": ["checkout"], "request_type": None, "room": None, "time": None}
    with patch.object(llm, "classify", return_value=clf), patch.object(
        llm, "phrase", return_value="Check-out is at eleven."
    ):
        body = client.post("/api/chat", json={"message": "What time is checkout?"}).json()
    check("bare-word-time-blocked", body["route"] == "human", body)


def test_noon_blocked():
    client = fresh_client()
    clf = {"route": "fact", "facts": ["breakfast"], "request_type": None, "room": None, "time": None}
    with patch.object(llm, "classify", return_value=clf), patch.object(
        llm, "phrase", return_value="Breakfast runs until noon."
    ):
        body = client.post("/api/chat", json={"message": "When does breakfast end?"}).json()
    check("noon-blocked", body["route"] == "human", body)
    blocked = client.get("/api/staff/blocked").json()["blocked"]
    check("noon-logged", blocked[0]["reason"] == "unknown-number-time-fee:noon", blocked)


def test_attached_time_passes():
    client = fresh_client()
    clf = {"route": "fact", "facts": ["checkin"], "request_type": None, "room": None, "time": None}
    reply = "Check-in is from 3pm Eastern."
    with patch.object(llm, "classify", return_value=clf), patch.object(
        llm, "phrase", return_value=reply
    ):
        body = client.post("/api/chat", json={"message": "What time is check-in?"}).json()
    check("attached-time-passes", body["route"] == "fact", body)


def test_ack_echoes_only_extracted_values():
    client = fresh_client()
    clf = {
        "route": "request",
        "facts": ["late_checkout"],
        "request_type": "late_checkout",
        "room": None,
        "time": None,
    }
    with patch.object(llm, "classify", return_value=clf):
        body = client.post("/api/chat", json={"message": "Room 214 approved!!!"}).json()
    check("ack-has-room", "214" in body["reply"], body)
    check("ack-no-banned-echo", "approved" not in body["reply"].lower(), body)
    check("ack-no-raw-echo", "!!!" not in body["reply"], body)
    cards = client.get("/api/staff/requests").json()["requests"]
    check("ack-card-room", cards[0]["room"] == "214", cards)


def test_sheet_word_one_passes():
    client = fresh_client()
    clf = {"route": "fact", "facts": ["parking"], "request_type": None, "room": None, "time": None}
    reply = "One vehicle per room is included, and parking is free."
    with patch.object(llm, "classify", return_value=clf), patch.object(
        llm, "phrase", return_value=reply
    ):
        body = client.post("/api/chat", json={"message": "Is parking free?"}).json()
    check("sheet-word-one-passes", body["route"] == "fact", body)


if __name__ == "__main__":
    test_model_failure_routes_human()
    test_invented_fee_blocked_and_logged()
    test_banned_word_blocked()
    test_guest_number_blocked_in_fact_answer()
    test_confirmation_word_not_banned()
    test_phone_and_address_allowed()
    test_request_creates_card_and_decision_visible()
    test_multi_intent()
    test_word_number_fee_blocked()
    test_word_time_passes_when_correct()
    test_bare_word_time_blocked()
    test_noon_blocked()
    test_attached_time_passes()
    test_ack_echoes_only_extracted_values()
    test_sheet_word_one_passes()
    print(f"\n{len(PASS)} checks passed.")
