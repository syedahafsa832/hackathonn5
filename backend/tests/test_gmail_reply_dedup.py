"""
P0 fix regression tests: Gmail thread-continuation replies were never
recorded as processed anywhere email_poller.py's dedup check could see.

Root cause: the dedup check only matched tickets.gmail_message_id, which is
set ONCE at ticket creation (message_processor.py's STAGE 1.8) to the very
first message's id. A same-thread reply appended later (STAGE 1.5) never
got its own gmail_message_id recorded against the ticket, so on every
subsequent poll cycle within the same day (Gmail's `after:` search is
date-granular, not incremental) the exact same reply was re-fetched, passed
the dedup check again, and was reprocessed from scratch.

Fix: track every processed inbound gmail_message_id per ticket via the new
`processed_gmail_message_ids` column (migration 060) - the ticket-creating
message is seeded into it at creation, and STAGE 1.5 appends every reply's
own id. email_poller.py's dedup check now looks there first.

All Gmail/Supabase/filter/guardian calls are mocked - no live services.
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.channels.email_poller import EmailPoller  # noqa: E402
from src.workers.message_processor import UnifiedMessageProcessor  # noqa: E402
from src.services.supabase_service import SupabaseService  # noqa: E402


def _brand():
    return {
        "id": "brand-1", "name": "Test Brand", "agent_name": "Luna",
        "gmail_email": "brand@example.com", "support_email": "support@example.com",
    }


def _email(msg_id, thread_id, sender="customer@example.com", body="Follow-up message"):
    return {
        "id": msg_id, "thread_id": thread_id, "subject": "Re: Order question",
        "sender_name": "Customer", "sender_email": sender, "body": body, "label_ids": [],
    }


ALLOW_RESULT = MagicMock(decision="allowed", reason=None, auto_reply_enabled=True,
                          email_category="support", sender_type="customer")
GUARDIAN_ALLOW = MagicMock(decision="allowed", reason=None, classification="support",
                            confidence=0.9, auto_reply_enabled=True)


# ── 1. A reply already folded into processed_gmail_message_ids is skipped ──

@pytest.mark.asyncio
async def test_thread_reply_already_processed_is_skipped_on_next_poll():
    """Simulates the SECOND poll cycle after a thread-continuation reply was
    already processed: an existing ticket has the reply's gmail_message_id
    in processed_gmail_message_ids (but NOT as the ticket's own top-level
    gmail_message_id, which is fixed to the original first message)."""
    calls = []

    async def process_message(_channel, payload):
        calls.append(payload["gmail_message_id"])
        return {"status": "ok", "ticket_id": "t1"}

    existing_ticket = {
        "id": "ticket-1",
        "gmail_message_id": "original-msg",  # the ticket-creating message only
        "processed_gmail_message_ids": ["original-msg", "reply-msg"],
    }

    def fake_select(table, params=None):
        if table == "tickets" and params.get("processed_gmail_message_ids") == "cs.{reply-msg}":
            return [existing_ticket]
        return []

    emails = [_email("reply-msg", "thread-A"), _email("new-msg", "thread-B")]
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

    assert calls == ["new-msg"], (
        "GAP: a thread-continuation reply already recorded in "
        "processed_gmail_message_ids was reprocessed instead of skipped."
    )


# ── 2. Failed processing must remain retryable ──────────────────────────────

@pytest.mark.asyncio
async def test_message_that_failed_processing_is_retried_next_poll():
    """A message process_message() raised on is never recorded as processed
    anywhere (no ticket write happens), so the very next poll must call
    process_message() for it again rather than silently dropping it."""
    attempts = []

    async def flaky_process_message(_channel, payload):
        attempts.append(payload["gmail_message_id"])
        raise RuntimeError("transient Supabase error")

    email = _email("flaky-msg", "thread-A")

    async def _poll_once():
        poller = EmailPoller(processor=MagicMock(process_message=flaky_process_message))
        with patch("src.services.brand_gmail_service.brand_gmail_service.get_new_emails",
                   new=AsyncMock(return_value=[email])), \
             patch("src.channels.email_poller.email_filter_service.evaluate", return_value=ALLOW_RESULT), \
             patch("src.channels.email_poller.email_filter_service.log_decision"), \
             patch("src.channels.email_poller.email_guardian_service.evaluate", return_value=GUARDIAN_ALLOW), \
             patch("src.channels.email_poller.email_guardian_service.log_guardian_decision"), \
             patch("src.channels.email_poller.supabase_select", return_value=[]), \
             patch("src.channels.email_poller.supabase_update", return_value={}):
            await poller._poll_brand_inbox(_brand())

    await _poll_once()
    await _poll_once()  # same message, next poll cycle - no ticket was ever created

    assert attempts == ["flaky-msg", "flaky-msg"], (
        "A message that failed processing must remain retryable on the next poll."
    )


# ── 3. STAGE 1.5 records the reply's own gmail_message_id ──────────────────

def test_thread_continuation_appends_reply_id_to_processed_ids():
    """message_processor.py's STAGE 1.5 must append the NEW reply's
    gmail_message_id into the existing ticket's processed_gmail_message_ids,
    not just its messages[] array."""
    processor = UnifiedMessageProcessor()
    captured_updates = {}

    existing_ticket = {
        "id": "ticket-1",
        "gmail_thread_id": "thread-123",
        "store_id": "brand-1",
        "messages": [{"from": "cust@example.com", "body": "first", "received_at": "t0", "direction": "inbound"}],
        "processed_gmail_message_ids": ["original-msg"],
        "status": "open",
    }

    def fake_select(table, params=None):
        if table == "tickets":
            return [existing_ticket]
        return []

    def fake_update(table, match, data):
        if table == "tickets":
            captured_updates.update(data)
        return {**existing_ticket, **data}

    with patch("src.workers.message_processor.supabase_select", side_effect=fake_select), \
         patch("src.workers.message_processor.supabase_update", side_effect=fake_update), \
         patch("src.workers.message_processor.supabase_service") as mock_service:
        mock_service.get_system_settings.side_effect = Exception("stop early - only STAGE 1.5 under test")
        try:
            asyncio.run(processor.process_message("email_incoming", {
                "channel": "email",
                "customer_email": "cust@example.com",
                "customer_name": "Customer",
                "content": "follow-up reply",
                "subject": "Re: Order question",
                "gmail_thread_id": "thread-123",
                "gmail_message_id": "reply-msg",
                "store_id": "brand-1",
            }))
        except Exception:
            pass

    assert captured_updates.get("processed_gmail_message_ids") == ["original-msg", "reply-msg"], (
        f"STAGE 1.5 did not record the reply's own gmail_message_id: {captured_updates}"
    )


# ── 4. STAGE 1.8 (new ticket) seeds processed_gmail_message_ids ────────────

@pytest.mark.asyncio
async def test_new_ticket_seeds_processed_gmail_message_ids():
    """SupabaseService.create_ticket must seed the new column with the
    ticket-creating message's own gmail_message_id, so the very first
    message in a thread is dedup-safe from the moment the ticket exists."""
    captured = {}

    def fake_insert(table, data):
        if table == "tickets":
            captured.update(data)
        return {**data, "id": "ticket-new"}

    with patch("src.services.supabase_service.supabase_insert", side_effect=fake_insert):
        await SupabaseService().create_ticket({
            "store_id": "brand-1",
            "customer_email": "cust@example.com",
            "customer_name": "Customer",
            "subject": "Order question",
            "message": "Hi",
            "messages": [{"from": "cust@example.com", "body": "Hi", "received_at": "t0", "direction": "inbound"}],
            "channel": "email",
            "gmail_message_id": "original-msg",
        })

    assert captured.get("processed_gmail_message_ids") == ["original-msg"], (
        f"New ticket did not seed processed_gmail_message_ids: {captured}"
    )


@pytest.mark.asyncio
async def test_new_ticket_without_gmail_message_id_seeds_empty_list():
    """A non-email (e.g. web_form) ticket has no gmail_message_id - must
    seed an empty list, never None (a None here would break the poller's
    `cs.` containment query for this ticket if it were ever email-based)."""
    captured = {}

    def fake_insert(table, data):
        if table == "tickets":
            captured.update(data)
        return {**data, "id": "ticket-new"}

    with patch("src.services.supabase_service.supabase_insert", side_effect=fake_insert):
        await SupabaseService().create_ticket({
            "store_id": "brand-1",
            "customer_email": "cust@example.com",
            "customer_name": "Customer",
            "subject": "Web question",
            "message": "Hi",
            "channel": "web_form",
        })

    assert captured.get("processed_gmail_message_ids") == []
