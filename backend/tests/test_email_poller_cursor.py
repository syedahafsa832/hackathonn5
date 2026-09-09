"""
Gmail polling cursor fix.

last_polled_at used to be stamped to wall-clock now() after every poll
cycle, regardless of what was actually fetched. If a cycle queried Gmail
before its search index had caught up on a just-sent message, the cursor
still advanced past "now" - already later than that message's real
timestamp - so every later cycle's since_dt filter excluded it forever,
with zero trace anywhere (confirmed live for a customer's "yo" reply: not
in tickets.messages, not in processed_gmail_message_ids, not in
email_quarantine).

Fix: advance the cursor only to the newest gmail_received_at actually
fetched this cycle, never past "now", never backward, and never at all
when zero emails were fetched.
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


def _brand():
    return {
        "id": "brand-1", "name": "Test Brand", "agent_name": "Luna",
        "gmail_email": "brand@example.com", "support_email": "support@example.com",
    }


def _email(msg_id, thread_id, gmail_received_at, sender="customer@example.com", body="hi"):
    return {
        "id": msg_id, "thread_id": thread_id, "subject": "Order question",
        "sender_name": "Customer", "sender_email": sender, "body": body, "label_ids": [],
        "gmail_received_at": gmail_received_at,
    }


ALLOW_RESULT = MagicMock(decision="allowed", reason=None, auto_reply_enabled=True,
                          email_category="support", sender_type="customer")
GUARDIAN_ALLOW = MagicMock(decision="allowed", reason=None, classification="support",
                            confidence=0.9, auto_reply_enabled=True)


async def _run_poll(emails):
    poller = EmailPoller(processor=MagicMock(process_message=AsyncMock(return_value={"success": True})))
    update_calls = []

    def _capture_update(table, match, data):
        update_calls.append((table, match, data))
        return {}

    with patch("src.services.brand_gmail_service.brand_gmail_service.get_new_emails",
               new=AsyncMock(return_value=emails)), \
         patch("src.channels.email_poller.email_filter_service.evaluate", return_value=ALLOW_RESULT), \
         patch("src.channels.email_poller.email_filter_service.log_decision"), \
         patch("src.channels.email_poller.email_guardian_service.evaluate", return_value=GUARDIAN_ALLOW), \
         patch("src.channels.email_poller.email_guardian_service.log_guardian_decision"), \
         patch("src.channels.email_poller.supabase_select", return_value=[]), \
         patch("src.channels.email_poller.supabase_update", side_effect=_capture_update):
        await poller._poll_brand_inbox(_brand())
    return update_calls


@pytest.mark.asyncio
async def test_newest_gmail_timestamp_becomes_the_cursor_not_wall_clock():
    """A late-indexed message's own timestamp - not now() - becomes the
    cursor, so a subsequent cycle's since_dt can never already be past it."""
    ts = datetime(2026, 9, 9, 16, 20, 50, tzinfo=timezone.utc)
    emails = [_email("m1", "t1", ts)]
    calls = await _run_poll(emails)
    brand_updates = [c for c in calls if c[0] == "brands" and "last_polled_at" in c[2]]
    assert len(brand_updates) == 1
    new_cursor = datetime.fromisoformat(brand_updates[0][2]["last_polled_at"])
    assert new_cursor == ts
    assert new_cursor < datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_empty_poll_does_not_advance_cursor():
    calls = await _run_poll([])
    brand_updates = [c for c in calls if c[0] == "brands" and "last_polled_at" in c[2]]
    assert brand_updates == []
