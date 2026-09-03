"""
Regression tests for the "conversation missing when merchant reopens the
dashboard after being away" production bug.

Root cause: supabase_service.create_ticket() rebuilds the row to insert from
a fixed field whitelist (`formatted_ticket`) that never included
last_customer_message_at, even though message_processor.py's STAGE 1.8
("create ticket immediately" — the write that happens the instant a new
Gmail message is ingested, independent of whether anyone has the dashboard
open) explicitly passes it in. Every brand-new ticket (the first message in
a thread) was therefore inserted with last_customer_message_at = NULL.

GET /api/tickets (tickets.py, the endpoint the Conversations page and the
Dashboard's Recent Conversations widget both call) orders by
"last_customer_message_at.desc.nullslast, updated_at.desc" — nullslast
means a NULL sorts behind every row that has a real value, no matter how
recent. Tickets.jsx re-sorts client-side with a `|| updated_at` fallback so
it happened to mask the bug, but Dashboard.jsx's Recent Conversations widget
trusts the server order as-is and just takes the first 3
(`conversations?.slice(0, 3)`) — so a conversation Luna fully processed and
replied to while the merchant was away could be sorted off the visible
list forever, even though it was the most recent real activity.

Confirmed against the live production database (Customer_support project):
tickets created via the STAGE 1.8 path (single inbound message, no thread
continuation) consistently have last_customer_message_at = NULL, while
tickets that went through STAGE 1.5's thread-continuation update (a raw
supabase_update, not create_ticket()) have it correctly set.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from unittest.mock import AsyncMock  # noqa: E402

from src.services.supabase_service import supabase_service  # noqa: E402
from src.api.routes.tickets import router as tickets_router, _tickets_cache  # noqa: E402
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext  # noqa: E402


def setup_function():
    _tickets_cache.clear()


# ─── 1. create_ticket() must forward last_customer_message_at to the insert ──

def test_create_ticket_persists_last_customer_message_at():
    """A brand-new ticket (STAGE 1.8's exact payload shape) must be inserted
    with last_customer_message_at set from the real inbound message
    timestamp — not silently dropped by create_ticket()'s field whitelist."""
    captured = {}

    def fake_insert(table, data):
        if table == "tickets":
            captured["data"] = data
        return {"id": "new-ticket-1", **data}

    with patch("src.services.supabase_service.supabase_insert", side_effect=fake_insert):
        asyncio.run(supabase_service.create_ticket({
            "store_id": "brand-1",
            "customer_email": "bushra@example.com",
            "customer_name": "Bushra Zohaib",
            "subject": "Refund for order #1009",
            "message": "I'd like a refund for order #1009.",
            "messages": [{"from": "bushra@example.com", "body": "...", "direction": "inbound"}],
            "channel": "email",
            "status": "processing",
            "gmail_thread_id": "thread-abc",
            "gmail_message_id": "msg-abc",
            "last_customer_message_at": "2026-09-02T00:21:00+00:00",
        }))

    assert captured["data"].get("last_customer_message_at") == "2026-09-02T00:21:00+00:00", (
        "create_ticket() dropped last_customer_message_at — the ticket would be "
        "inserted with this column NULL and get sorted to the very bottom of "
        "every last_customer_message_at.desc.nullslast query, regardless of how "
        "recent the customer's message actually was"
    )


# ─── 2. End-to-end: a brand-new Gmail message, dashboard never opened ────────

def test_new_gmail_message_is_persisted_with_customer_activity_timestamp_while_dashboard_closed():
    """Exercises the real STAGE 1.8 code path in message_processor.py (not a
    mocked create_ticket) all the way down to the literal dict sent to
    Supabase — simulating a customer email arriving and being fully
    processed with no dashboard/browser/frontend involved at all."""
    from src.workers.message_processor import UnifiedMessageProcessor

    processor = UnifiedMessageProcessor()
    captured = {}

    def fake_insert(table, data):
        if table == "tickets":
            captured["data"] = data
            return {"id": "offline-ticket-1", **data}
        return {"id": "row-1"}

    with patch("src.workers.message_processor.supabase_select", return_value=[]), \
         patch("src.services.supabase_service.supabase_insert", side_effect=fake_insert), \
         patch("src.services.plan_service.check_ai_entitlement", side_effect=Exception("stop after ticket creation")):
        try:
            asyncio.run(processor.process_message("email_incoming", {
                "channel": "email",
                "content": "Hi Luna, I'd like a refund for order #1009.",
                "customer_email": "bushrazohaib84@gmail.com",
                "customer_name": "Bushra Zohaib",
                "subject": "Refund request",
                "store_id": "brand-1",
                "gmail_thread_id": "thread-1009",
                "gmail_message_id": "msg-1009",
                "received_at": "2026-09-02T00:21:00+00:00",
            }))
        except Exception:
            pass

    assert "data" in captured, "STAGE 1.8 never created the ticket"
    assert captured["data"].get("last_customer_message_at") == "2026-09-02T00:21:00+00:00"
    assert captured["data"].get("status") == "processing"
    assert captured["data"].get("gmail_thread_id") == "thread-1009"


# ─── 3. The Recent Conversations widget's raw server order must surface it ──

def test_conversation_processed_while_away_sorts_ahead_of_older_untouched_tickets():
    """Dashboard.jsx's Recent Conversations widget takes GET /api/tickets's
    result as-is (conversations.slice(0, 3), no client-side re-sort) —
    unlike Tickets.jsx, which has its own fallback sort that happened to
    mask this bug. So the brand-new conversation must already be first in
    the *server's* own ordering, exactly reproducing the "merchant was away
    for 10 days" scenario: an old conversation nobody has touched sits in
    the list, then a new customer message arrives and is fully processed
    with the dashboard closed."""
    old_conversation = {
        "id": "old", "channel": "email",
        "last_customer_message_at": "2026-08-20T00:00:00+00:00",
        "updated_at": "2026-08-20T00:05:00+00:00",
        "messages": [],
    }
    processed_while_merchant_was_away = {
        "id": "offline-ticket-1", "channel": "email",
        # This is exactly what create_ticket() now persists (see test 1/2
        # above) instead of leaving it NULL.
        "last_customer_message_at": "2026-09-02T00:21:00+00:00",
        "updated_at": "2026-09-02T00:22:00+00:00",
        "messages": [],
    }

    app = FastAPI()
    app.include_router(tickets_router, prefix="/api")
    app.dependency_overrides[get_current_tenant] = lambda: TenantContext(tenant_id="tenant-1", email="owner@example.com")

    with patch("src.api.routes.tickets._get_tenant_brand_ids", new=AsyncMock(return_value=["brand-1"])), \
         patch("src.api.routes.tickets.supabase_service.get_tickets",
               new=AsyncMock(return_value=[old_conversation, processed_while_merchant_was_away])), \
         patch("src.api.routes.v2_tickets._normalize_ticket_messages", return_value=[]):
        client = TestClient(app)
        resp = client.get("/api/tickets")

    ids = [t["id"] for t in resp.json()]
    assert ids[0] == "offline-ticket-1", (
        "conversation processed while the merchant was away must be first in "
        "the server's own order — Dashboard.jsx's Recent Conversations widget "
        "takes the raw server order's first 3 with no client-side re-sort"
    )
