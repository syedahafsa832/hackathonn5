"""
Email Loop Prevention Tests
=============================
Covers the independent loop guards in email_poller.py's _poll_brand_inbox:
brand-owned sender address, deep Re: chains, and Gmail's own SENT label.

Root cause this file now guards against: the SENT-label guard replaced an
earlier version that searched the entire email body for our own reply
signature text (e.g. "- Luna") as a loop-prevention signal. That broke on
a real, confirmed-live production case: Gmail appends the previous message
(including our own signature) as quoted history below every reply, so a
genuine new customer reply to a thread Luna had already answered contained
"- Luna" in its quoted portion and was silently discarded before ever
reaching the filter/guardian pipeline - the customer's follow-up simply
vanished, with no ticket update and no error.

Signature text is never a reliable identity signal (a customer can just as
easily quote, paste, or forward our own wording). Gmail's SENT label is
authoritative account-level metadata - set if and only if the connected
Gmail account itself sent the message - and is already captured per
message by brand_gmail_service.py's label_ids field (already relied on by
email_filter_service.py for category filtering), so this reuses existing
data rather than adding a new signal.

All Gmail/Supabase/filter calls are mocked - no live services required.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.channels.email_poller import EmailPoller  # noqa: E402


def _brand(agent_name="Luna"):
    return {
        "id": "brand-1", "name": "Test Brand", "agent_name": agent_name,
        "gmail_email": "brand@example.com", "support_email": "support@example.com",
    }


async def _run_poll(brand, emails):
    poller = EmailPoller()
    fake_filter_result = MagicMock(decision="blocked", reason="test-boundary", auto_reply_enabled=False)

    with patch("src.services.brand_gmail_service.brand_gmail_service.get_new_emails",
               new=AsyncMock(return_value=emails)), \
         patch("src.channels.email_poller.email_filter_service.evaluate", return_value=fake_filter_result) as mock_evaluate, \
         patch("src.channels.email_poller.email_filter_service.log_decision"), \
         patch("src.channels.email_poller.supabase_select", return_value=[]):
        await poller._poll_brand_inbox(brand)

    # Every email that reached filter evaluation survived all loop guards;
    # anything skipped by a guard never gets here.
    return [call.args[0]["sender_email"] for call in mock_evaluate.call_args_list]


# ── Case A / E: genuine outbound mail (or an echo carrying the SENT label) ─

@pytest.mark.asyncio
async def test_message_with_sent_label_is_skipped_regardless_of_sender():
    """The strongest reliable signal: Gmail's own SENT label. Covers the
    narrow case the old signature-body check was actually meant for - a
    third-party tool that echoes our own outbound reply back into the
    inbox without preserving our sender address (so the earlier
    brand-owned-address guard wouldn't catch it either)."""
    echoed_own_reply = {
        "id": "msg1", "thread_id": "t1", "subject": "Fwd: your inquiry",
        "sender_name": "Third-Party Forwarder", "sender_email": "forwarder@thirdparty.com",
        "body": "Forwarded message below:\n\nHi! Thanks for reaching out.\n\n- Luna\nTest Brand",
        "label_ids": ["SENT"],
    }
    normal_email = {
        "id": "msg2", "thread_id": "t2", "subject": "Where is my order?",
        "sender_name": "Real Customer", "sender_email": "customer@example.com",
        "body": "Hey, where's my order? It's been a week.",
        "label_ids": ["INBOX", "UNREAD"],
    }

    reached_filter = await _run_poll(_brand(), [echoed_own_reply, normal_email])

    assert "customer@example.com" in reached_filter
    assert "forwarder@thirdparty.com" not in reached_filter


# ── Case B/C/D: a real customer reply must never be dropped for containing
# our own signature/content, quoted or otherwise ─────────────────────────

@pytest.mark.asyncio
async def test_customer_reply_quoting_our_signature_is_processed():
    """The exact regression: Gmail appends the prior message (including our
    signature) as quoted history below every reply. A genuine new customer
    reply must still be ingested."""
    reply_with_quoted_history = {
        "id": "msg3", "thread_id": "t3", "subject": "Re: Order #1009",
        "sender_name": "Bushra Zohaib", "sender_email": "bushrazohaib84@gmail.com",
        "body": (
            "Usman Tariq\nfrom United States\n\n"
            "On Aug 31, 2026, Syedahafsa1983's Store wrote:\n"
            "> Hey Bushra,\n>\n> I found order #1009, but the email you're contacting us from "
            "doesn't match the one on that order.\n> To update the shipping address, I need to "
            "confirm your identity.\n> Could you please provide your full name and country?\n>\n"
            "> - Luna\n> Syedahafsa1983's Store"
        ),
        "label_ids": ["INBOX", "UNREAD"],
    }

    reached_filter = await _run_poll(_brand(), [reply_with_quoted_history])

    assert reached_filter == ["bushrazohaib84@gmail.com"]


@pytest.mark.asyncio
async def test_customer_copying_our_entire_reply_verbatim_is_still_processed():
    """Case C: the customer pastes/forwards our own previous reply back to
    us in a NEW email (no Gmail quote formatting at all). The sender is
    genuinely the customer, so it must still be processed - this is exactly
    what the old whole-body signature scan would have wrongly rejected."""
    pasted_email = {
        "id": "msg4", "thread_id": "t4", "subject": "Re: inquiry",
        "sender_name": "Real Customer", "sender_email": "customer2@example.com",
        "body": "Hey Bushra,\n\nI found order #1009...\n\n- Luna\nTest Brand",
        "label_ids": ["INBOX", "UNREAD"],
    }

    reached_filter = await _run_poll(_brand(), [pasted_email])

    assert reached_filter == ["customer2@example.com"]


@pytest.mark.asyncio
async def test_normal_customer_email_mentioning_agent_name_in_passing_is_not_falsely_skipped():
    email = {
        "id": "msg5", "thread_id": "t5", "subject": "Question",
        "sender_name": "Real Customer", "sender_email": "customer3@example.com",
        "body": "Hi, I spoke with Luna yesterday and she said my order shipped. Can you confirm?",
        "label_ids": ["INBOX", "UNREAD"],
    }

    reached_filter = await _run_poll(_brand(), [email])

    assert "customer3@example.com" in reached_filter


# ── Failure case (spec section 15): a short reply must still be ingested ──

@pytest.mark.asyncio
async def test_short_low_content_reply_is_still_processed():
    """A brief 'Thanks' reply (no SENT label, no brand-owned sender) must
    still reach the pipeline - ingestion is not gated on content length or
    substance; that judgment belongs to the AI, not the loop guard."""
    short_reply = {
        "id": "msg6", "thread_id": "t6", "subject": "Re: Order #1009",
        "sender_name": "Real Customer", "sender_email": "customer4@example.com",
        "body": "Thanks",
        "label_ids": ["INBOX", "UNREAD"],
    }

    reached_filter = await _run_poll(_brand(), [short_reply])

    assert reached_filter == ["customer4@example.com"]


# ── The other two independent guards stay intact ───────────────────────────

@pytest.mark.asyncio
async def test_brand_owned_sender_address_is_still_skipped():
    own_reply_correct_sender = {
        "id": "msg7", "thread_id": "t7", "subject": "Re: Order #1009",
        "sender_name": "Test Brand", "sender_email": "brand@example.com",
        "body": "Hey Bushra,\n\nI found order #1009...\n\n- Luna\nTest Brand",
        "label_ids": ["SENT"],
    }

    poller = EmailPoller()
    fake_filter_result = MagicMock(decision="blocked", reason="test-boundary", auto_reply_enabled=False)
    with patch("src.services.brand_gmail_service.brand_gmail_service.get_new_emails",
               new=AsyncMock(return_value=[own_reply_correct_sender])), \
         patch("src.channels.email_poller.email_filter_service.evaluate", return_value=fake_filter_result) as mock_evaluate, \
         patch("src.channels.email_poller.email_filter_service.log_decision"), \
         patch("src.channels.email_poller.supabase_select", return_value=[]):
        await poller._poll_brand_inbox(_brand(), all_brand_emails=frozenset({"brand@example.com"}))

    assert mock_evaluate.call_args_list == []


@pytest.mark.asyncio
async def test_deep_reply_chain_is_still_skipped():
    deep_chain_email = {
        "id": "msg8", "thread_id": "t8", "subject": "Re: Re: Re: inquiry",
        "sender_name": "Someone", "sender_email": "customer5@example.com",
        "body": "Still going...",
        "label_ids": ["INBOX", "UNREAD"],
    }

    reached_filter = await _run_poll(_brand(), [deep_chain_email])

    assert reached_filter == []
