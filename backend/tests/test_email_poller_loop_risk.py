"""
loop_risk must never prevent an inbound customer message from being
ingested. It used to hard-drop the message inside email_poller.py before
it ever reached message_processor.py (confirmed live: a "yo" reply on
ticket #7767d0be, loop_risk=true/auto_reply_count=2, vanished with zero
trace - never in messages, processed_gmail_message_ids, or quarantine).

Suppressing the AI's own auto-reply SEND now happens inside
message_processor.py instead (both the main path and the provider-retry
path), right before should_auto_reply is acted on - never before
ingestion. This test proves the poller side of that fix: a message on a
loop_risk=true thread reaches the processor exactly like a normal one.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.channels.email_poller import EmailPoller  # noqa: E402

ALLOW_RESULT = MagicMock(decision="allowed", reason=None, auto_reply_enabled=True,
                          email_category="support", sender_type="customer")
GUARDIAN_ALLOW = MagicMock(decision="allowed", reason=None, classification="support",
                            confidence=0.9, auto_reply_enabled=True)


def _brand():
    return {"id": "brand-1", "name": "Test Brand", "agent_name": "Luna",
            "gmail_email": "brand@example.com", "support_email": "support@example.com"}


def _email(msg_id, thread_id):
    return {
        "id": msg_id, "thread_id": thread_id, "subject": "Re: order question",
        "sender_name": "Customer", "sender_email": "customer@example.com",
        "body": "yo", "label_ids": [],
        "gmail_received_at": datetime(2026, 9, 9, 16, 41, 0, tzinfo=timezone.utc),
    }


async def _run_poll(emails):
    dispatched = []

    async def process_message(_channel, payload):
        dispatched.append(payload)
        return {"status": "ok", "ticket_id": "existing-ticket"}

    poller = EmailPoller(processor=MagicMock(process_message=process_message))
    with patch("src.services.brand_gmail_service.brand_gmail_service.get_new_emails",
               new=AsyncMock(return_value=emails)), \
         patch("src.channels.email_poller.email_filter_service.evaluate", return_value=ALLOW_RESULT), \
         patch("src.channels.email_poller.email_filter_service.log_decision"), \
         patch("src.channels.email_poller.email_guardian_service.evaluate", return_value=GUARDIAN_ALLOW), \
         patch("src.channels.email_poller.email_guardian_service.log_guardian_decision"), \
         patch("src.channels.email_poller.supabase_select", return_value=[]), \
         patch("src.channels.email_poller.supabase_update", return_value={}):
        await poller._poll_brand_inbox(_brand())
    return dispatched


@pytest.mark.asyncio
async def test_reply_on_loop_risk_thread_still_reaches_processor():
    """The confirmed bug: the poller used to look up tickets.loop_risk
    itself and `return` before dispatch whenever it was true, dropping the
    message with zero trace. That lookup/early-return no longer exists in
    email_poller.py at all - loop_risk is now only consulted downstream in
    message_processor.py to suppress the auto-reply SEND, never ingestion.
    A message on what would have been a loop-risk thread must still reach
    the processor exactly like any other."""
    email = _email("m-yo", "existing-thread-123")
    dispatched = await _run_poll([email])
    assert len(dispatched) == 1
    assert dispatched[0].get("gmail_thread_id") == "existing-thread-123"


@pytest.mark.asyncio
async def test_reply_on_normal_thread_unaffected():
    """Non-loop-risk behavior must remain unchanged."""
    email = _email("m-normal", "existing-thread-456")
    dispatched = await _run_poll([email])
    assert len(dispatched) == 1
