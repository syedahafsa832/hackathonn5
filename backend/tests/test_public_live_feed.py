"""
Product-claim safety: the homepage "Watch Luna Work" widget (LiveFeed.jsx)
displays tickets from GET /api/public/live-feed as "Resolved". Refunds,
cancellations, and address changes are only ever *staged* for merchant
approval (return_actions_integration.py) - a ticket with one of those intents
can reach status=auto_resolved/resolved just because the acknowledgment reply
was sent, not because the actual sensitive action was completed. Showing that
as "Resolved" on the public homepage would overclaim automation this product
doesn't do. public_feed.py now filters those intents out of the public feed;
these tests cover that filter directly.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.api.routes.public_feed import public_live_feed  # noqa: E402


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _ticket(intent, **overrides):
    base = {
        "detected_order_id": "1001",
        "subject": "Where is my order",
        "confidence_score": 90,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:01:00Z",
        "intent": intent,
    }
    base.update(overrides)
    return base


def test_order_status_ticket_appears_in_public_feed():
    tickets = [_ticket("order_status_inquiry")]
    with patch("src.api.routes.public_feed.supabase_select", return_value=tickets):
        result = run(public_live_feed())
    assert len(result["feed"]) == 1


def test_refund_ticket_is_excluded_from_public_feed():
    tickets = [_ticket("refund_request")]
    with patch("src.api.routes.public_feed.supabase_select", return_value=tickets):
        result = run(public_live_feed())
    assert result["feed"] == []


def test_cancellation_ticket_is_excluded_from_public_feed():
    tickets = [_ticket("cancellation_request")]
    with patch("src.api.routes.public_feed.supabase_select", return_value=tickets):
        result = run(public_live_feed())
    assert result["feed"] == []


def test_address_change_ticket_is_excluded_from_public_feed():
    tickets = [_ticket("address_change")]
    with patch("src.api.routes.public_feed.supabase_select", return_value=tickets):
        result = run(public_live_feed())
    assert result["feed"] == []


def test_mixed_tickets_only_non_staged_actions_shown():
    tickets = [
        _ticket("order_status_inquiry", detected_order_id="1001"),
        _ticket("refund_request", detected_order_id="1002"),
        _ticket("shipping_inquiry", detected_order_id="1003"),
        _ticket("address_change", detected_order_id="1004"),
    ]
    with patch("src.api.routes.public_feed.supabase_select", return_value=tickets):
        result = run(public_live_feed())
    order_refs = [row["order_ref"] for row in result["feed"]]
    assert order_refs == ["#1001", "#1003"]
