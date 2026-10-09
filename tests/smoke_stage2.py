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
    main._hits.clear()
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


def test_message_cap():
    client = fresh_client()
    r = client.post("/api/chat", json={"message": "x" * 501})
    check("message-cap-422", r.status_code == 422, r.status_code)
    r = client.post("/api/chat", json={"message": "x" * 500})
    check("message-cap-edge", r.status_code != 422, r.status_code)


def test_rate_limit():
    import os
    from unittest.mock import patch as mock_patch

    client = fresh_client()
    clf = {"route": "human", "facts": [], "request_type": None, "room": None, "time": None}
    with mock_patch.dict(os.environ, {"RATE_LIMIT_PER_MINUTE": "2"}), patch.object(
        llm, "classify", return_value=clf
    ):
        s1 = client.post("/api/chat", json={"message": "hi"}).status_code
        s2 = client.post("/api/chat", json={"message": "hi"}).status_code
        s3 = client.post("/api/chat", json={"message": "hi"}).status_code
    check("rate-limit", (s1, s2, s3) == (200, 200, 429), (s1, s2, s3))


def test_daily_model_cap():
    import os
    from unittest.mock import patch as mock_patch

    llm._calls.update(day=None, count=0)
    with mock_patch.dict(os.environ, {"DAILY_MODEL_CALL_CAP": "1"}), patch.object(
        llm, "_client", return_value=(object(), "m")
    ), patch.object(llm, "_generate", return_value="ok"):
        first = llm.phrase("q", ["facts"])
        try:
            llm.phrase("q", ["facts"])
            second_ok = True
        except llm.ModelError:
            second_ok = False
    check("daily-cap", first == "ok" and not second_ok, (first, second_ok))
    llm._calls.update(day=None, count=0)


class _FakeStatus(Exception):
    def __init__(self, code):
        super().__init__(f"fake status {code}")
        self.code = code


def test_retry_then_success():
    calls = {"n": 0}

    def flaky(client, model, system, text):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _FakeStatus(429)
        return "recovered"

    with patch.object(llm, "_call_once", side_effect=flaky), patch("time.sleep") as slp:
        out = llm._generate(object(), "m", "s", "u")
    check("retry-success", out == "recovered" and calls["n"] == 3, (out, calls))
    check("retry-slept-twice", slp.call_count == 2, slp.call_count)


def test_retry_exhausted_raises():
    calls = {"n": 0}

    def always_down(client, model, system, text):
        calls["n"] += 1
        raise _FakeStatus(503)

    with patch.object(llm, "_call_once", side_effect=always_down), patch("time.sleep"):
        try:
            llm._generate(object(), "m", "s", "u")
            raised = False
        except llm.ModelError:
            raised = True
    check("retry-exhausted", raised and calls["n"] == 3, calls)


def test_non_retryable_fails_fast():
    calls = {"n": 0}

    def bad_request(client, model, system, text):
        calls["n"] += 1
        raise _FakeStatus(400)

    with patch.object(llm, "_call_once", side_effect=bad_request), patch("time.sleep") as slp:
        try:
            llm._generate(object(), "m", "s", "u")
            raised = False
        except llm.ModelError:
            raised = True
    check("non-retryable-fast", raised and calls["n"] == 1 and slp.call_count == 0, calls)


def test_pages_and_facts():
    client = fresh_client()
    g = client.get("/")
    check("guest-page", g.status_code == 200 and "SAMPLE DATA, invented" in g.text, g.status_code)
    check("guest-staff-link", '/staff' in g.text, '')
    s = client.get("/staff")
    check("staff-page", s.status_code == 200 and "Approve" in s.text and "no staff login" in s.text, s.status_code)
    f = client.get("/api/facts").json()
    check("facts-labels", any(x["id"] == "parking" for x in f["facts"]), f)


def test_cap_notice():
    import os
    from unittest.mock import patch as mock_patch

    client = fresh_client()
    with mock_patch.dict(os.environ, {"DAILY_MODEL_CALL_CAP": "0"}), mock_patch.object(
        llm, "_client", return_value=(object(), "m")
    ):
        body = client.post("/api/chat", json={"message": "Is parking free?"}).json()
    check("cap-notice", body["route"] == "human" and "Daily model limit" in (body["notice"] or ""), body)
    with mock_patch.object(llm, "_client", side_effect=llm.ModelError("down")):
        body = client.post("/api/chat", json={"message": "Is parking free?"}).json()
    check("no-notice-otherwise", body["notice"] is None, body)
    try:
        raise llm.DailyCapReached("x")
        caught_as_model_error = False
    except llm.ModelError:
        caught_as_model_error = True
    check("cap-is-model-error", caught_as_model_error, '')


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
    test_message_cap()
    test_rate_limit()
    test_daily_model_cap()
    test_retry_then_success()
    test_retry_exhausted_raises()
    test_non_retryable_fails_fast()
    test_pages_and_facts()
    test_cap_notice()
    test_sheet_word_one_passes()
    print(f"\n{len(PASS)} checks passed.")
