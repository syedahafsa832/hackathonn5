"""
Chat widget activity-streaming endpoint (`POST /widget/chat?stream=1`).

Covers the route-level half of the activity/status feature: the endpoint is
opt-in (existing consumers - the vanilla embed script `static/widget.js` and
the dashboard's ChatWidget.jsx - never pass `stream=1` and must keep getting
back the exact same plain ChatResponse JSON they always have), and when a
caller does opt in, it gets newline-delimited JSON: zero or more
`{"type": "status", ...}` activity frames sourced from the agent's real
on_progress callback, followed by exactly one final `{"type": "result", ...}`
frame carrying the same fields as ChatResponse. A failure after the reply
was already generated (e.g. persisting the ticket) must degrade to a single
sanitized `{"type": "error", ...}` frame - never a stack trace, never a dead
connection with no frame at all.
"""
import os
import sys
import json
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes import v2_chat_widget  # noqa: E402

app = FastAPI()
app.include_router(v2_chat_widget.router, prefix="/api/v2")
client = TestClient(app)

BRAND_ROW = {"id": "brand-1", "tenant_id": "tenant-1", "name": "Test Brand", "agent_name": "Luna"}


def setup_function():
    # The route's in-memory rate limiter is a module-level dict shared across
    # tests in this process - reset it so an earlier test's requests never
    # cause a later one to see 429 instead of the response under test.
    v2_chat_widget._rate_buckets.clear()


def _fake_supabase_select(table, params=None):
    if table == "brands":
        return [BRAND_ROW]
    if table == "tickets":
        return []  # no existing session -> _get_or_create_session inserts one
    return []


def _fake_supabase_insert(table, data):
    if table == "tickets":
        return {**data, "id": "ticket-1"}
    return {**data, "id": "generated-id"}


def _request_body(message="where is my order #1234?"):
    return {
        "brand_id": "brand-1",
        "session_id": "sess-1",
        "message": message,
    }


def _parse_ndjson(text: str):
    return [json.loads(line) for line in text.strip().split("\n") if line.strip()]


def _agent_result(reply="Your order shipped yesterday!"):
    return {
        "reply_body": reply,
        "confidence_score": 91,
        "intent": "order_status_inquiry",
        "status": "auto_resolved",
        "escalate": False,
        "order_data": None,
        "action_taken": None,
        "ai_reply_generated": True,
        "model_used": "test-model",
    }


def _common_patches():
    """Patches shared by every test below: brand/ticket lookups and the
    daily/plan limit gates that must pass before the endpoint ever reaches
    the streaming branch under test."""
    return [
        patch("src.api.routes.v2_chat_widget.supabase_select", side_effect=_fake_supabase_select),
        patch("src.api.routes.v2_chat_widget.supabase_insert", side_effect=_fake_supabase_insert),
        patch("src.services.auth_service.auth_service.check_daily_ticket_limit", new=AsyncMock(return_value=True)),
        patch("src.services.plan_service.check_limit", return_value={"allowed": True}),
        patch("src.services.plan_service.record_ai_reply_event"),
    ]


# ── 1. Existing (non-streaming) consumers are unaffected ──────────────────

def test_default_no_stream_param_returns_plain_json_unchanged():
    """widget.js and the dashboard's ChatWidget.jsx never pass ?stream=1 -
    they must keep getting the exact same shape back: a single JSON object,
    not an NDJSON envelope."""
    patches = _common_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
         patch("src.api.routes.v2_chat_widget.supabase_update"), \
         patch("src.agent.customer_success_agent.customer_success_agent.process_customer_query",
               new=AsyncMock(return_value=_agent_result())):
        resp = client.post("/api/v2/widget/chat", json=_request_body())

    assert resp.status_code == 200
    assert "ndjson" not in resp.headers.get("content-type", "")
    body = resp.json()
    assert body["reply"] == "Your order shipped yesterday!"
    assert "type" not in body  # plain ChatResponse shape, no NDJSON envelope


# ── 2. ?stream=1 surfaces real activity frames, then one result frame ─────

def test_stream_param_returns_status_frames_then_one_result_frame():
    async def fake_process_customer_query(*, query, customer_info, tenant_id, store_id, ticket_id, on_progress=None):
        if on_progress:
            await on_progress("thinking", "Reading your message…")
            await on_progress("order_lookup", "Looking up your order…")
            await on_progress("preparing", "Preparing your answer…")
        return _agent_result()

    patches = _common_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
         patch("src.api.routes.v2_chat_widget.supabase_update"), \
         patch("src.agent.customer_success_agent.customer_success_agent.process_customer_query",
               new=fake_process_customer_query):
        resp = client.post("/api/v2/widget/chat?stream=1", json=_request_body())

    assert resp.status_code == 200
    assert "application/x-ndjson" in resp.headers["content-type"]

    frames = _parse_ndjson(resp.text)
    # 3 stages from the mocked agent + the route's own "sending" stage
    # (emitted by _generate_reply once the reply is ready, before it's
    # persisted) + the final result.
    assert [f["type"] for f in frames] == ["status", "status", "status", "status", "result"]
    assert frames[0] == {"type": "status", "stage": "thinking", "label": "Reading your message…"}
    assert frames[1]["stage"] == "order_lookup"
    assert frames[2]["stage"] == "preparing"
    assert frames[3]["stage"] == "sending"

    result = frames[-1]
    assert result["reply"] == "Your order shipped yesterday!"
    assert result["confidence"] == 91
    # The final frame is the same shape as the plain ChatResponse - the
    # customer's UI can render it identically either way.
    assert set(result.keys()) >= {"reply", "session_id", "confidence", "resolution_step", "resolution_complete"}


def test_stream_with_no_tool_dispatch_still_ends_with_a_result_frame():
    """A query with nothing to look up still gets a clean end state - never
    a stream that just stops with no result frame (the 'endless spinner'
    case the frontend has to protect against)."""
    async def fake_process_customer_query(*, query, customer_info, tenant_id, store_id, ticket_id, on_progress=None):
        if on_progress:
            await on_progress("thinking", "Reading your message…")
            await on_progress("preparing", "Preparing your answer…")
        return _agent_result(reply="We're open 9-5, Monday to Friday!")

    patches = _common_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
         patch("src.api.routes.v2_chat_widget.supabase_update"), \
         patch("src.agent.customer_success_agent.customer_success_agent.process_customer_query",
               new=fake_process_customer_query):
        resp = client.post("/api/v2/widget/chat?stream=1", json=_request_body("what are your store hours?"))

    frames = _parse_ndjson(resp.text)
    assert frames[-1]["type"] == "result"
    assert frames[-1]["reply"] == "We're open 9-5, Monday to Friday!"


# ── 3. Failures degrade to one sanitized error frame - never a leak ───────

def test_stream_failure_after_reply_generation_yields_sanitized_error_frame():
    """Simulates a failure in the post-reply persistence step (ticket
    update) - something that happens after the agent already produced a
    reply. The customer must see a generic apology, never the exception
    text, a stack trace, or any internal detail."""
    async def fake_process_customer_query(*, query, customer_info, tenant_id, store_id, ticket_id, on_progress=None):
        if on_progress:
            await on_progress("thinking", "Reading your message…")
        return _agent_result()

    patches = _common_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
         patch("src.api.routes.v2_chat_widget.supabase_update",
               side_effect=RuntimeError("Traceback: connection to db://user:hunter2@10.0.0.5 failed")), \
         patch("src.agent.customer_success_agent.customer_success_agent.process_customer_query",
               new=fake_process_customer_query):
        resp = client.post("/api/v2/widget/chat?stream=1", json=_request_body())

    frames = _parse_ndjson(resp.text)
    assert frames[-1]["type"] == "error"
    assert "hunter2" not in resp.text
    assert "Traceback" not in resp.text
    assert "10.0.0.5" not in resp.text
    assert frames[-1]["message"] == "Sorry, something went wrong on our end. Please try again."


# ── 4. Early-exit gates (rate/plan limits) are unaffected by streaming ────

def test_daily_ticket_limit_short_circuits_to_plain_json_even_with_stream_param():
    """The daily-limit/quota early-exit paths run before any tool would ever
    be dispatched, so they stay plain JSON even when the caller asked for
    stream=1 - there is nothing real to stream yet."""
    patches = _common_patches()
    with patches[0], patches[1], \
         patch("src.services.auth_service.auth_service.check_daily_ticket_limit", new=AsyncMock(return_value=False)), \
         patch("src.api.routes.v2_chat_widget.supabase_update"):
        resp = client.post("/api/v2/widget/chat?stream=1", json=_request_body())

    assert resp.status_code == 200
    assert "ndjson" not in resp.headers.get("content-type", "")
    body = resp.json()
    assert "free ticket limit" in body["reply"]
