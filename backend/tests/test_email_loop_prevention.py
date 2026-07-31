"""
Email Loop Prevention Tests (H4)
===================================
Covers the three independent loop guards in email_poller.py's
_poll_brand_inbox: brand-owned sender address, deep Re: chains, and the
new signature-marker guard added in this pass — skip an inbound email if
its body contains our own reply signature ("— {agent_name}"), catching
loops the other two guards miss (e.g. a third-party tool echoes our reply
back without preserving the original sender or bumping the Re: count).

All Gmail/Supabase/filter calls are mocked — no live services required.
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


@pytest.mark.asyncio
async def test_email_containing_our_own_signature_is_skipped():
    signature_email = {
        "id": "msg1", "thread_id": "t1", "subject": "Fwd: your inquiry",
        "sender_name": "Third-Party Forwarder", "sender_email": "forwarder@thirdparty.com",
        "body": "Forwarded message below:\n\nHi! Thanks for reaching out.\n\n— Luna\nTest Brand",
        "label_ids": [],
    }
    normal_email = {
        "id": "msg2", "thread_id": "t2", "subject": "Where is my order?",
        "sender_name": "Real Customer", "sender_email": "customer@example.com",
        "body": "Hey, where's my order? It's been a week.",
        "label_ids": [],
    }

    reached_filter = await _run_poll(_brand(), [signature_email, normal_email])

    assert "customer@example.com" in reached_filter
    assert "forwarder@thirdparty.com" not in reached_filter


@pytest.mark.asyncio
async def test_signature_guard_is_per_brand_agent_name():
    """The marker is built from this brand's own configured agent_name, not
    a hardcoded 'Luna' — a brand with a custom agent name must still be
    protected."""
    custom_signature_email = {
        "id": "msg3", "thread_id": "t3", "subject": "Re: inquiry",
        "sender_name": "Forwarder", "sender_email": "forwarder@thirdparty.com",
        "body": "Echoed reply:\n\nHappy to help!\n\n— Zara\nCustom Brand",
        "label_ids": [],
    }

    reached_filter = await _run_poll(_brand(agent_name="Zara"), [custom_signature_email])

    assert reached_filter == []


@pytest.mark.asyncio
async def test_normal_customer_email_mentioning_agent_name_in_passing_is_not_falsely_skipped():
    """A customer merely mentioning 'Luna' (no em-dash signature format)
    must not be caught — the guard matches the signature format, not just
    the name."""
    email = {
        "id": "msg4", "thread_id": "t4", "subject": "Question",
        "sender_name": "Real Customer", "sender_email": "customer2@example.com",
        "body": "Hi, I spoke with Luna yesterday and she said my order shipped. Can you confirm?",
        "label_ids": [],
    }

    reached_filter = await _run_poll(_brand(), [email])

    assert "customer2@example.com" in reached_filter


@pytest.mark.asyncio
async def test_signature_guard_is_independent_of_re_depth_counting():
    """A single-Re: (or no-Re:) subject must still be caught by the
    signature guard even though it would sail through the Re:-depth guard."""
    email = {
        "id": "msg5", "thread_id": "t5", "subject": "Your support ticket",  # no "Re:" at all
        "sender_name": "Auto-forwarder", "sender_email": "bot@ticketing-tool.com",
        "body": "Ticket echoed:\n\n— Luna\nTest Brand",
        "label_ids": [],
    }

    reached_filter = await _run_poll(_brand(), [email])

    assert reached_filter == []
