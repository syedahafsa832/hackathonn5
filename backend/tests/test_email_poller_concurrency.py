"""
Email poller concurrency fix.

_poll_brand_inbox used to await self.processor.process_message() fully for
each fetched email before even looking at the next one - so a slow AI/
provider/Gmail-send pipeline for email A delayed email B's own "create
ticket immediately" stage (in message_processor.py) by however long email
A's entire pipeline took. Emails are now dispatched independently, grouped
only by Gmail thread_id (same-thread messages still run strictly in order,
since message_processor.py's STAGE 1.5 does a "does a ticket exist for this
thread" check that two concurrent same-thread messages could otherwise both
pass before either commits, creating duplicate tickets for one thread).

All Gmail/Supabase/filter/guardian calls are mocked - no live services.
"""
import os
import sys
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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


def _email(msg_id, thread_id, sender="customer@example.com", body="Hi, where is my order?"):
    return {
        "id": msg_id, "thread_id": thread_id, "subject": "Order question",
        "sender_name": "Customer", "sender_email": sender, "body": body, "label_ids": [],
    }


ALLOW_RESULT = MagicMock(decision="allowed", reason=None, auto_reply_enabled=True,
                          email_category="support", sender_type="customer")
GUARDIAN_ALLOW = MagicMock(decision="allowed", reason=None, classification="support",
                            confidence=0.9, auto_reply_enabled=True)


async def _run_poll(emails, process_message):
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
    return poller


# ── 1-2. Independent (concurrent) dispatch across threads ──────────────────

@pytest.mark.asyncio
async def test_slow_email_does_not_block_a_different_threads_email():
    order = []

    async def process_message(_channel, payload):
        msg_id = payload["gmail_message_id"]
        if msg_id == "slow-msg":
            await asyncio.sleep(0.2)
        order.append(msg_id)
        return {"status": "ok", "ticket_id": f"ticket-{msg_id}"}

    emails = [_email("slow-msg", "thread-A"), _email("fast-msg", "thread-B")]
    await _run_poll(emails, process_message)

    # If processing were still sequential, slow-msg (listed first) would
    # always finish first. Concurrent dispatch lets the fast one finish
    # first despite being fetched second.
    assert order == ["fast-msg", "slow-msg"]


# ── 3-4. Failure isolation and correct counters ─────────────────────────────

@pytest.mark.asyncio
async def test_one_failed_email_does_not_prevent_others_and_counters_stay_correct(caplog):
    async def process_message(_channel, payload):
        msg_id = payload["gmail_message_id"]
        if msg_id == "bad-msg":
            raise RuntimeError("boom")
        return {"status": "ok", "ticket_id": f"ticket-{msg_id}"}

    emails = [_email("bad-msg", "thread-A"), _email("good-msg-1", "thread-B"), _email("good-msg-2", "thread-C")]
    import logging
    with caplog.at_level(logging.INFO, logger="src.channels.email_poller"):
        await _run_poll(emails, process_message)

    summary = next(r.message for r in caplog.records if "summary" in r.message)
    assert "processed=2" in summary
    assert "failures=1" in summary


# ── 5. Same-thread messages still process strictly in order ────────────────

@pytest.mark.asyncio
async def test_same_thread_messages_are_still_processed_in_order():
    order = []

    async def process_message(_channel, payload):
        msg_id = payload["gmail_message_id"]
        if msg_id == "thread-msg-1":
            await asyncio.sleep(0.1)  # would finish after msg-2 if run concurrently
        order.append(msg_id)
        return {"status": "ok", "ticket_id": f"ticket-{msg_id}"}

    same_thread = "thread-X"
    emails = [_email("thread-msg-1", same_thread), _email("thread-msg-2", same_thread)]
    await _run_poll(emails, process_message)

    assert order == ["thread-msg-1", "thread-msg-2"]


# ── 6. Gmail message dedup unchanged ────────────────────────────────────────

@pytest.mark.asyncio
async def test_duplicate_gmail_message_id_still_skipped():
    calls = []

    async def process_message(_channel, payload):
        calls.append(payload["gmail_message_id"])
        return {"status": "ok", "ticket_id": "t1"}

    def fake_select(table, params=None):
        if table == "tickets" and params.get("gmail_message_id") == "eq.dup-msg":
            return [{"id": "existing-ticket"}]
        return []

    emails = [_email("dup-msg", "thread-A"), _email("new-msg", "thread-B")]
    poller = EmailPoller(processor=MagicMock(process_message=process_message))
    with patch("src.services.brand_gmail_service.brand_gmail_service.get_new_emails",
               new=AsyncMock(return_value=emails)), \
         patch("src.channels.email_poller.email_filter_service.evaluate", return_value=ALLOW_RESULT), \
         patch("src.channels.email_poller.email_filter_service.log_decision"), \
         patch("src.channels.email_poller.email_guardian_service.evaluate", return_value=GUARDIAN_ALLOW), \
         patch("src.channels.email_poller.email_guardian_service.log_guardian_decision"), \
         patch("src.channels.email_poller.supabase_select", side_effect=fake_select), \
         patch("src.channels.email_poller.supabase_update", return_value={}):
        await poller._poll_brand_inbox(_brand())

    assert calls == ["new-msg"]
