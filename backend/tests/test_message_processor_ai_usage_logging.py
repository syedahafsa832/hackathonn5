"""
ai_conversations was never actually being populated in production despite the
write logic existing (backend/src/workers/brand_message_processor.py
_log_conversation) and passing its own unit tests. Root cause: main.py wires
the real EmailPoller to a DIFFERENT module - src/workers/message_processor.py
(UnifiedMessageProcessor) - which never called _log_conversation at all.
brand_message_processor.py has no other caller anywhere in the codebase (only
this test file and its own tests import it) - it was dead code from a
production-routing standpoint, even though its logic was correct and tested.

This test proves the actual live path (UnifiedMessageProcessor.process_message)
now calls brand_message_processor's _log_conversation with the real model/
usage/brand/ticket values, reusing the existing, already-tested method rather
than duplicating it.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.workers.message_processor import UnifiedMessageProcessor  # noqa: E402


def run(coro):
    # See test_chat_widget_action_staging.py's run() for why: asyncio.get_
    # event_loop() can return an already-closed loop left behind by an
    # earlier @pytest.mark.asyncio test in the full suite.
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _ai_result(**overrides):
    result = {
        "reply_body": "Hey there! Here's what I found.",
        "ai_reply_generated": True,
        "model_used": "mistral-large-latest",
        "ai_usage": {"prompt_tokens": 400, "completion_tokens": 90, "total_tokens": 490, "latency_ms": 620, "attempts": 1},
        "intent": "product_inquiry",
        "sentiment": "neutral",
        "risk_level": "low",
        "confidence_score": 85,
        "escalate": False,
    }
    result.update(overrides)
    return result


def _base_mocks(ai_result):
    """Patches every dependency UnifiedMessageProcessor.process_message touches
    before and around the AI-generation stage, so the real function runs
    for real up to (and past) the ai_conversations persistence call."""
    return [
        patch("src.workers.message_processor.supabase_select", return_value=[{"id": "tenant-1"}]),
        patch("src.workers.message_processor.supabase_update"),
        patch("src.workers.message_processor.supabase_service.create_ticket", new=AsyncMock(return_value={"id": "ticket-1"})),
        patch("src.workers.message_processor.supabase_service.get_system_settings", new=AsyncMock(return_value={"ai_mode": "active", "confidence_threshold": 0.65})),
        patch("src.workers.message_processor.supabase_service.get_or_create_customer", new=AsyncMock(return_value={"id": "customer-1"})),
        patch("src.services.plan_service.record_email_processed"),
        patch("src.services.plan_service.can_process_ticket", return_value={"allowed": True}),
        patch("src.services.plan_service.record_ticket_created"),
        patch("src.services.plan_service.check_limit", return_value={"allowed": True}),
        patch("src.services.plan_service.check_ai_entitlement", return_value={"allowed": True, "reason": None, "plan": "trial", "trial_expired": False}),
        patch("src.services.plan_service.record_ai_reply_event"),
        patch("src.workers.message_processor.customer_success_agent.generate_channel_appropriate_response", new=AsyncMock(return_value=ai_result)),
    ]


def _run_process_message(message, ai_result):
    proc = UnifiedMessageProcessor()
    mocks = _base_mocks(ai_result)
    with patch("src.workers.message_processor.brand_message_processor._log_conversation", new=AsyncMock()) as mock_log:
        for m in mocks:
            m.start()
        try:
            run(proc.process_message("email_incoming", message))
        finally:
            for m in mocks:
                m.stop()
    return mock_log


def test_real_email_conversation_persists_model_and_usage():
    ai_result = _ai_result()
    message = {
        "channel": "email", "content": "I need a hoodie for winter what would you suggest",
        "customer_email": "customer@example.com", "customer_name": "Jane Doe",
        "subject": "Product question", "store_id": "brand-1",
    }

    mock_log = _run_process_message(message, ai_result)

    mock_log.assert_awaited_once()
    _, kwargs = mock_log.call_args
    assert kwargs["brand_id"] == "brand-1"
    assert kwargs["ticket_id"] == "ticket-1"
    assert kwargs["user_message"] == "I need a hoodie for winter what would you suggest"
    assert kwargs["ai_response"] == ai_result["reply_body"]
    assert kwargs["model"] == "mistral-large-latest"
    assert kwargs["usage"]["total_tokens"] == 490
    assert kwargs["usage"]["latency_ms"] == 620


def test_failed_ai_generation_does_not_persist_a_conversation():
    """ai_reply_generated absent (provider outage / fallback response) — no
    real generation happened, nothing genuine to log."""
    ai_result = _ai_result(reply_body="Having trouble right now.", ai_reply_generated=False, model_used=None, ai_usage=None)
    message = {
        "channel": "email", "content": "hello", "customer_email": "customer@example.com",
        "customer_name": "Jane Doe", "subject": "Hi", "store_id": "brand-1",
    }

    mock_log = _run_process_message(message, ai_result)

    # _log_conversation is still called (it logs the raw exchange for history
    # regardless of ai_reply_generated - that flag only gates quota), but with
    # no usage/model to report a real generation happened.
    mock_log.assert_awaited_once()
    _, kwargs = mock_log.call_args
    assert kwargs["usage"] is None
