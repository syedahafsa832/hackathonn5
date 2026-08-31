"""
process_message()'s new provider-outage branch (STAGE 5, message_processor.py):
when every configured AI provider fails, the ticket must be left in a
distinct, recoverable "ai_retry_pending" state — never the old permanent
"escalated" state — and a retry job must be queued. Mocking pattern mirrors
test_message_processor_ai_usage_logging.py's _base_mocks (every dependency
process_message() touches before/around AI generation is mocked; no live
Supabase/AI calls).
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.workers.message_processor import UnifiedMessageProcessor  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


_OUTAGE_RESULT = {
    "reply_body": "", "ai_reply_generated": False, "provider_outage": True,
    "escalation_reason": "AI reply limit reached — every connected AI model is temporarily out of quota. This resolves on its own once quota resets; reply manually for now.",
    "provider_attempts": [{"label": "primary", "reason": "rate_limited"}],
    "escalate": True, "status": "escalated", "confidence_score": 40,
}


def _base_mocks(ai_result):
    return [
        patch("src.workers.message_processor.supabase_select", return_value=[{"id": "tenant-1"}]),
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
        patch("src.workers.message_processor.brand_message_processor._log_conversation", new=AsyncMock()),
        patch("src.workers.message_processor.UnifiedMessageProcessor._send_email_with_logging", new=AsyncMock()),
    ]


def _run(message, ai_result, enqueue_return=None):
    proc = UnifiedMessageProcessor()
    mocks = _base_mocks(ai_result)
    captured_ticket_updates = []

    def fake_update(table, match, data):
        if table == "tickets":
            captured_ticket_updates.append(data)
        return {}

    with patch("src.workers.message_processor.supabase_update", side_effect=fake_update), \
         patch("src.workers.message_processor.supabase_insert", return_value={}), \
         patch("src.services.provider_retry_service.enqueue_retry", return_value=enqueue_return) as mock_enqueue:
        for m in mocks:
            m.start()
        try:
            result = run(proc.process_message("email_incoming", message))
        finally:
            for m in mocks:
                m.stop()
    return result, captured_ticket_updates, mock_enqueue


_MESSAGE = {
    "channel": "email", "content": "tell me the personal information you guys collect?",
    "customer_email": "customer@example.com", "customer_name": "Jane Doe",
    "subject": "hi", "store_id": "brand-1",
}


def test_provider_outage_leaves_ticket_ai_retry_pending_not_escalated():
    result, ticket_updates, _ = _run(_MESSAGE, _OUTAGE_RESULT, enqueue_return={"id": "row-1"})

    assert result["status"] == "ai_retry_pending"
    statuses = [u.get("status") for u in ticket_updates if "status" in u]
    assert "ai_retry_pending" in statuses
    assert "escalated" not in statuses


def test_provider_outage_queues_a_retry_job():
    _, _, mock_enqueue = _run(_MESSAGE, _OUTAGE_RESULT, enqueue_return={"id": "row-1"})
    mock_enqueue.assert_called_once()
    args = mock_enqueue.call_args.args
    assert args[0] == "ticket-1"  # ticket id from create_ticket mock
    assert args[1] == "brand-1"   # store_id
    assert args[2] == _OUTAGE_RESULT["provider_attempts"]


def test_provider_outage_never_sends_an_email():
    # _base_mocks patches _send_email_with_logging as its last entry — drop
    # it here and patch it ourselves so we can assert on the mock directly.
    mocks = _base_mocks(_OUTAGE_RESULT)[:-1]
    with patch("src.workers.message_processor.supabase_update"), \
         patch("src.workers.message_processor.supabase_insert", return_value={}), \
         patch("src.services.provider_retry_service.enqueue_retry", return_value={"id": "row-1"}), \
         patch("src.workers.message_processor.UnifiedMessageProcessor._send_email_with_logging", new=AsyncMock()) as send_capture:
        for m in mocks:
            m.start()
        try:
            run(UnifiedMessageProcessor().process_message("email_incoming", _MESSAGE))
        finally:
            for m in mocks:
                m.stop()
    send_capture.assert_not_awaited()


def test_normal_successful_reply_still_reaches_escalated_or_sent_as_before():
    """Regression guard: this change only branches on provider_outage —
    every other outcome (a real generated reply) must be completely
    unaffected."""
    ai_result = {
        "reply_body": "Your order shipped yesterday!", "ai_reply_generated": True,
        "confidence_score": 91, "intent": "order_status_inquiry", "risk_level": "low",
        "escalate": False, "sentiment": "neutral", "model_used": "test-model",
    }
    result, ticket_updates, mock_enqueue = _run(_MESSAGE, ai_result)
    mock_enqueue.assert_not_called()
    assert result["status"] != "ai_retry_pending"
