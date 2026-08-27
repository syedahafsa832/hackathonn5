"""
"Real-Time AI Employees" feature — connects three already-existing pieces
that were never wired together for the live email pipeline:

1. message_processor.py already creates the ticket BEFORE calling the AI
   (STAGE 1.8 vs STAGE 5) — this file locks that ordering in with a test.
2. customer_success_agent.process_customer_query() already emits real
   dispatch-point progress ("Finding order #X…", "Shopify order found", ...)
   via an on_progress callback, already forwarded into
   return_actions_integration.handle_return_intent() — but
   generate_channel_appropriate_response() (the only method
   message_processor.py calls) silently dropped that parameter, so none of
   it ever reached the email pipeline or got persisted anywhere.
3. A new `ticket_events` table (migration 050) now persists each such
   callback invocation, exposed via the existing GET /api/tickets/{id}
   (tickets.py) response the dashboard already polls.

These tests prove the wiring end-to-end without re-testing the underlying
emission logic itself (already covered by test_chat_activity_progress.py).
"""
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.workers.message_processor import UnifiedMessageProcessor  # noqa: E402
from src.agent.customer_success_agent import customer_success_agent  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


BRAND_ID = "brand-1"


def _ai_result(**overrides):
    result = {
        "reply_body": "Hey there! Here's what I found.",
        "ai_reply_generated": True,
        "model_used": "mistral-large-latest",
        "ai_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "latency_ms": 1, "attempts": 1},
        "intent": "order_status", "sentiment": "neutral", "risk_level": "low",
        "confidence_score": 90, "escalate": False,
    }
    result.update(overrides)
    return result


def _emitted_progress_result(stages, **overrides):
    """A fake generate_channel_appropriate_response that behaves like the
    real one: it calls the on_progress callback it was given with a fixed
    sequence of (stage, label) pairs before returning - exactly how
    process_customer_query's own _emit() calls behave, just without needing
    to run the real RAG/Shopify/LLM pipeline."""
    async def fake(*args, on_progress=None, **kwargs):
        if on_progress:
            for stage, label in stages:
                await on_progress(stage, label)
        return _ai_result(**overrides)
    return fake


def _base_mocks(agent_side_effect, call_log=None):
    async def _tracking_create_ticket(payload):
        if call_log is not None:
            call_log.append("create_ticket")
        return {"id": "ticket-1"}

    async def _tracking_agent(*args, **kwargs):
        if call_log is not None:
            call_log.append("agent_call")
        return await agent_side_effect(*args, **kwargs)

    return [
        patch("src.workers.message_processor.supabase_select", return_value=[{"id": "tenant-1"}]),
        patch("src.workers.message_processor.supabase_update"),
        patch("src.workers.message_processor.supabase_service.create_ticket", new=AsyncMock(side_effect=_tracking_create_ticket)),
        patch("src.workers.message_processor.supabase_service.get_system_settings", new=AsyncMock(return_value={"ai_mode": "active", "confidence_threshold": 0.65})),
        patch("src.workers.message_processor.supabase_service.get_or_create_customer", new=AsyncMock(return_value={"id": "customer-1"})),
        patch("src.services.plan_service.record_email_processed"),
        patch("src.services.plan_service.can_process_ticket", return_value={"allowed": True}),
        patch("src.services.plan_service.record_ticket_created"),
        patch("src.services.plan_service.check_limit", return_value={"allowed": True}),
        patch("src.services.plan_service.check_ai_entitlement", return_value={"allowed": True, "reason": None, "plan": "trial", "trial_expired": False}),
        patch("src.services.plan_service.record_ai_reply_event"),
        patch("src.workers.message_processor.brand_message_processor._log_conversation", new=AsyncMock()),
        patch("src.workers.message_processor.customer_success_agent.generate_channel_appropriate_response", new=AsyncMock(side_effect=_tracking_agent)),
    ]


def _run(message, agent_side_effect, call_log=None):
    proc = UnifiedMessageProcessor()
    mocks = _base_mocks(agent_side_effect, call_log=call_log)
    with patch("src.workers.message_processor._log_ticket_event") as mock_event:
        for m in mocks:
            m.start()
        try:
            result = run(proc.process_message("email_incoming", message))
        finally:
            for m in mocks:
                m.stop()
    return result, mock_event


def _message(**overrides):
    m = {
        "channel": "email", "content": "Where is my order #1013?",
        "customer_email": "customer@example.com", "customer_name": "Jane Doe",
        "subject": "Order question", "store_id": BRAND_ID,
    }
    m.update(overrides)
    return m


# 1. Ticket is created (visible in Conversations) BEFORE the AI call runs.
def test_ticket_created_before_ai_call():
    call_log = []
    agent_fn = _emitted_progress_result([])
    _run(_message(), agent_fn, call_log=call_log)
    assert call_log == ["create_ticket", "agent_call"]


# 2. Real dispatch-point progress reaches ticket_events, verbatim — no
# fabrication, no relabeling.
def test_agent_progress_events_are_persisted_verbatim():
    stages = [
        ("order_lookup", "Finding order #1013…"),
        ("order_found", "Shopify order found"),
        ("policy_check", "Checking our policies…"),
    ]
    agent_fn = _emitted_progress_result(stages)
    _, mock_event = _run(_message(), agent_fn)

    logged = [(c.args[2], c.args[3]) for c in mock_event.call_args_list]
    for stage, label in stages:
        assert (stage, label) in logged
    # Every one of these came from the same ticket/brand the message belongs to.
    for c in mock_event.call_args_list:
        assert c.args[1] == BRAND_ID  # brand_id
        assert c.args[0] == "ticket-1"  # ticket_id


# Coarse pipeline milestones (not from the agent's own _emit calls) are
# also real and correctly ordered: received -> draft ready -> sent.
def test_coarse_milestones_logged_in_order():
    agent_fn = _emitted_progress_result([])
    _, mock_event = _run(_message(), agent_fn)
    stages_logged = [c.args[2] for c in mock_event.call_args_list]
    assert stages_logged.index("message_received") < stages_logged.index("draft_ready")
    assert stages_logged.index("draft_ready") < stages_logged.index("sent")


# 3. The final response is only recorded (draft_ready/sent) after processing —
# never before the agent has actually returned a reply.
def test_no_draft_or_sent_event_without_a_real_reply():
    agent_fn = _emitted_progress_result([], reply_body="", ai_reply_generated=False)
    _, mock_event = _run(_message(), agent_fn)
    stages_logged = [c.args[2] for c in mock_event.call_args_list]
    assert "draft_ready" not in stages_logged
    assert "sent" not in stages_logged
    assert "message_received" in stages_logged


# Escalated outcomes get a real terminal event too (high risk -> escalated,
# no auto-reply) instead of leaving the timeline silently stuck.
def test_escalated_outcome_logs_an_escalated_event():
    agent_fn = _emitted_progress_result([], risk_level="high", escalate=True, confidence_score=40)
    _, mock_event = _run(_message(), agent_fn)
    stages_logged = [c.args[2] for c in mock_event.call_args_list]
    assert "escalated" in stages_logged


# 6. Brand isolation: events for brand A's ticket are always tagged with
# brand A's id, never a different brand's, regardless of which brand sent the message.
def test_events_are_tagged_with_the_owning_brand():
    agent_fn = _emitted_progress_result([("order_lookup", "Finding order #42…")])
    _, mock_event = _run(_message(store_id="brand-OTHER"), agent_fn)
    for c in mock_event.call_args_list:
        assert c.args[1] == "brand-OTHER"


# Regression guard for the "cancel + refund both created" bug: Stage 9.5
# used to run its own independent action_detect_and_create() call after the
# primary agent call, entirely unaware of what return_actions_integration.py
# (called from inside generate_channel_appropriate_response, two stages
# earlier) had already decided — for a fulfilled order, that produced a
# correctly-typed "refund" action from the primary path AND an incorrectly-
# typed "cancel_order" action from this second, independent path, since each
# path's own dedup check only ever looked for its own action_type. Stage 9.5
# is retired; this must never fire again regardless of what the agent
# returns or detects.
def test_stage_9_5_never_independently_detects_or_creates_an_action():
    agent_fn = _emitted_progress_result([], reply_body="Sure, I'll help with that cancellation.")
    with patch("src.workers.message_processor.actions_service.detect_and_create", new=AsyncMock()) as mock_detect:
        _run(_message(content="can you cancel my order #1002"), agent_fn)
    mock_detect.assert_not_awaited()


# The wiring fix itself: generate_channel_appropriate_response used to drop
# on_progress silently. Confirm it now forwards it into process_customer_query.
def test_generate_channel_appropriate_response_forwards_on_progress():
    captured = {}

    async def fake_process_customer_query(*args, **kwargs):
        captured.update(kwargs)
        return {"reply_body": "ok"}

    async def _cb(stage, label):
        pass

    with patch.object(customer_success_agent, "process_customer_query", new=AsyncMock(side_effect=fake_process_customer_query)):
        run(customer_success_agent.generate_channel_appropriate_response(
            query="hi", customer_info={}, channel="email", ticket_id="t-1", on_progress=_cb,
        ))

    assert captured.get("on_progress") is _cb
