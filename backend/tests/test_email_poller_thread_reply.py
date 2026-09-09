"""
P0: customer reply inside an EXISTING Gmail thread must reach the processor
and advance the cursor past its own timestamp - not be silently dropped.

Root cause (same class as test_email_poller_cursor.py, reproduced live for
ticket #7767d0be's "yo" reply): the poll cursor (brands.last_polled_at) used
to be stamped to wall-clock now() every cycle. A reply landing in the tight
window between one cycle's Gmail fetch and the next cycle's cursor-write
would still get a cursor value AFTER its own timestamp, so the following
cycle's since_dt filter excluded it forever - with zero trace anywhere.
Fixed in email_poller.py by advancing the cursor only to the newest
gmail_received_at actually fetched. This test proves the fix on a reply
email specifically (matching an existing thread_id), not just a fresh one.
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
    return {
        "id": "brand-1", "name": "Test Brand", "agent_name": "Luna",
        "gmail_email": "brand@example.com", "support_email": "support@example.com",
    }


def _reply_email(msg_id, thread_id, gmail_received_at):
    return {
        "id": msg_id, "thread_id": thread_id, "subject": "Re: order question",
        "sender_name": "Customer", "sender_email": "customer@example.com",
        "body": "yo", "label_ids": [], "gmail_received_at": gmail_received_at,
    }


@pytest.mark.asyncio
async def test_reply_on_existing_thread_reaches_processor_and_advances_cursor():
    reply_ts = datetime(2026, 9, 9, 16, 41, 0, tzinfo=timezone.utc)
    email = _reply_email("m-yo", "existing-thread-123", reply_ts)

    dispatched = []

    async def process_message(_channel, payload):
        dispatched.append(payload)
        return {"status": "ok", "ticket_id": "existing-ticket"}

    update_calls = []

    def _capture_update(table, match, data):
        update_calls.append((table, match, data))
        return {}

    poller = EmailPoller(processor=MagicMock(process_message=process_message))
    with patch("src.services.brand_gmail_service.brand_gmail_service.get_new_emails",
               new=AsyncMock(return_value=[email])), \
         patch("src.channels.email_poller.email_filter_service.evaluate", return_value=ALLOW_RESULT), \
         patch("src.channels.email_poller.email_filter_service.log_decision"), \
         patch("src.channels.email_poller.email_guardian_service.evaluate", return_value=GUARDIAN_ALLOW), \
         patch("src.channels.email_poller.email_guardian_service.log_guardian_decision"), \
         patch("src.channels.email_poller.supabase_select", return_value=[]), \
         patch("src.channels.email_poller.supabase_update", side_effect=_capture_update):
        await poller._poll_brand_inbox(_brand())

    # 1. The reply reached the processor at all (thread-matching handoff),
    #    keyed by its own thread_id so message_processor.py appends it to
    #    the existing ticket rather than dropping it.
    assert len(dispatched) == 1
    assert dispatched[0].get("gmail_thread_id") == "existing-thread-123"

    # 2. The cursor advanced to the reply's OWN timestamp - not wall-clock
    #    now() - so a later cycle's since_dt can never already be past it.
    brand_updates = [c for c in update_calls if c[0] == "brands" and "last_polled_at" in c[2]]
    assert len(brand_updates) == 1
    new_cursor = datetime.fromisoformat(brand_updates[0][2]["last_polled_at"])
    assert new_cursor == reply_ts
