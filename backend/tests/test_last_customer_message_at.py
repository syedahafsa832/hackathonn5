"""
tickets.last_customer_message_at (migration 056): set ONLY when a genuine
inbound customer message is persisted (STAGE 1.5 thread continuation, STAGE
1.8 new ticket) - never by AI processing (STAGE 9), draft creation, or any
other ticket update. Preferred timestamp hierarchy: Gmail's own received
timestamp (message['received_at'], threaded through from
brand_gmail_service.py's internalDate capture) > current time as a last
resort - never fabricated.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
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


BRAND_ID = "brand-1"


def _emitted_progress_result(**overrides):
    result = {
        "reply_body": "Hey there!", "ai_reply_generated": True, "model_used": "test-model",
        "ai_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "latency_ms": 1, "attempts": 1},
        "intent": "order_status", "sentiment": "neutral", "risk_level": "low",
        "confidence_score": 90, "escalate": False,
    }
    result.update(overrides)

    async def fake(*args, on_progress=None, **kwargs):
        return result
    return fake


def _run(message, select_side_effect=None):
    created_payloads = []

    async def _tracking_create_ticket(payload):
        created_payloads.append(payload)
        return {"id": "ticket-1"}

    default_select = select_side_effect or (lambda table, params=None: [{"id": "tenant-1"}] if table == "tenants" else [])

    mocks = [
        patch("src.workers.message_processor.supabase_select", side_effect=default_select),
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
        patch("src.workers.message_processor.customer_success_agent.generate_channel_appropriate_response", new=AsyncMock(side_effect=_emitted_progress_result())),
        patch("src.workers.message_processor._log_ticket_event"),
    ]
    started = []
    try:
        for m in mocks:
            started.append(m.start())
        mock_update = started[1]
        result = run(UnifiedMessageProcessor().process_message("email_incoming", message))
    finally:
        for m in mocks:
            m.stop()
    return result, created_payloads, mock_update


def _message(**overrides):
    m = {
        "channel": "email", "content": "Where is my order?",
        "customer_email": "customer@example.com", "customer_name": "Jane Doe",
        "subject": "Order question", "store_id": BRAND_ID,
    }
    m.update(overrides)
    return m


# ── 1-2. New ticket (STAGE 1.8) ──────────────────────────────────────────

def test_new_ticket_sets_last_customer_message_at_from_gmail_timestamp():
    gmail_ts = "2026-08-27T21:23:00+00:00"
    _, created_payloads, _ = _run(_message(received_at=gmail_ts))
    assert created_payloads[0]["last_customer_message_at"] == gmail_ts
    assert created_payloads[0]["messages"][0]["received_at"] == gmail_ts


def test_new_ticket_falls_back_to_now_when_no_trusted_timestamp_available():
    before = datetime.now(timezone.utc)
    _, created_payloads, _ = _run(_message())  # no received_at supplied
    ts = datetime.fromisoformat(created_payloads[0]["last_customer_message_at"])
    assert ts >= before  # a real "now", not fabricated or stale


# ── 3. STAGE 9 (AI results) never touches last_customer_message_at ──────

def test_ai_processing_update_never_includes_last_customer_message_at():
    existing_ticket = {
        "id": "ticket-existing", "messages": [], "status": "processing",
        "store_id": BRAND_ID, "gmail_thread_id": "thread-1",
    }

    def select_side_effect(table, params=None):
        if table == "tenants":
            return [{"id": "tenant-1"}]
        if table == "tickets" and params and params.get("gmail_thread_id"):
            return [existing_ticket]
        return []

    _, _, mock_update = _run(_message(gmail_thread_id="thread-1", received_at="2026-08-27T21:23:00+00:00"), select_side_effect)

    # STAGE 9's update (the AI-results write, keyed on early_ticket_id) must
    # never carry this field - only STAGE 1.5's own append-update (checked
    # separately below) is allowed to.
    for c in mock_update.call_args_list:
        update_fields = c.args[2]
        if "messages" not in update_fields:  # STAGE 9 deliberately excludes messages; STAGE 1.5's append includes it
            assert "last_customer_message_at" not in update_fields


# ── 4-5. STAGE 1.5 thread continuation ───────────────────────────────────

def test_thread_continuation_sets_last_customer_message_at_on_append():
    existing_ticket = {
        "id": "ticket-existing", "messages": [{"from": "customer@example.com", "body": "first", "direction": "inbound"}],
        "status": "resolved", "store_id": BRAND_ID, "gmail_thread_id": "thread-1",
    }

    def select_side_effect(table, params=None):
        if table == "tenants":
            return [{"id": "tenant-1"}]
        if table == "tickets" and params and params.get("gmail_thread_id"):
            return [existing_ticket]
        return []

    reply_ts = "2026-08-28T07:41:00+00:00"
    _, _, mock_update = _run(_message(gmail_thread_id="thread-1", received_at=reply_ts), select_side_effect)

    # STAGE 1.5's inbound-append update is the only one carrying this field -
    # a later STAGE 10 update also touches "messages" (appending the
    # outbound AI reply), so filter on the field under test, not "messages".
    append_calls = [c for c in mock_update.call_args_list if "last_customer_message_at" in c.args[2]]
    assert len(append_calls) == 1
    assert append_calls[0].args[2]["last_customer_message_at"] == reply_ts
    # The new message in the array carries the real timestamp too.
    appended_msg = append_calls[0].args[2]["messages"][-1]
    assert appended_msg["received_at"] == reply_ts
    assert appended_msg["direction"] == "inbound"


# ── 8-9. Gmail's own internalDate: preserved when available, never fabricated ─

def test_brand_gmail_service_extracts_gmail_internal_date_when_present():
    from src.services.brand_gmail_service import BrandGmailService

    fake_msg_get = MagicMock(return_value=MagicMock(execute=MagicMock(return_value={
        "threadId": "t1",
        "payload": {"headers": [{"name": "Subject", "value": "Hi"}, {"name": "From", "value": "c@example.com"}], "body": {}},
        "labelIds": [],
        "internalDate": "1798580580000",  # a fixed, known epoch-ms value
        "snippet": "hi",
    })))
    fake_svc = MagicMock()
    fake_svc.users.return_value.messages.return_value.list.return_value.execute.return_value = {"messages": [{"id": "m1"}]}
    fake_svc.users.return_value.messages.return_value.get = fake_msg_get
    fake_svc.users.return_value.messages.return_value.batchModify.return_value.execute.return_value = {}

    service = BrandGmailService.__new__(BrandGmailService)
    with patch.object(service, "_build_service", return_value=fake_svc), \
         patch.object(service, "_decode_body", return_value="hi"):
        emails = service._get_new_emails_sync({"id": "brand-1", "name": "Test"}, max_results=5)

    assert len(emails) == 1
    assert emails[0]["gmail_received_at"] is not None
    expected = datetime.fromtimestamp(1798580580000 / 1000, tz=timezone.utc).isoformat()
    assert emails[0]["gmail_received_at"] == expected


def test_brand_gmail_service_does_not_fabricate_timestamp_when_internal_date_missing():
    from src.services.brand_gmail_service import BrandGmailService

    fake_msg_get = MagicMock(return_value=MagicMock(execute=MagicMock(return_value={
        "threadId": "t1",
        "payload": {"headers": [{"name": "Subject", "value": "Hi"}, {"name": "From", "value": "c@example.com"}], "body": {}},
        "labelIds": [],
        # no internalDate key at all
        "snippet": "hi",
    })))
    fake_svc = MagicMock()
    fake_svc.users.return_value.messages.return_value.list.return_value.execute.return_value = {"messages": [{"id": "m1"}]}
    fake_svc.users.return_value.messages.return_value.get = fake_msg_get
    fake_svc.users.return_value.messages.return_value.batchModify.return_value.execute.return_value = {}

    service = BrandGmailService.__new__(BrandGmailService)
    with patch.object(service, "_build_service", return_value=fake_svc), \
         patch.object(service, "_decode_body", return_value="hi"):
        emails = service._get_new_emails_sync({"id": "brand-1", "name": "Test"}, max_results=5)

    assert emails[0]["gmail_received_at"] is None  # never guessed/fabricated
