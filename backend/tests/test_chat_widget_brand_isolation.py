"""
Chat widget session brand-isolation (P0 tenant-isolation audit).

_get_or_create_session() looked up an existing chat ticket by session_id
ALONE (gmail_thread_id + channel="chat"), with no brand_id check. session_id
is generated client-side (widget/components/chat-widget/ChatWidget.tsx's
makeSessionId(): `Math.random().toString(36) + Date.now().toString(36)`) —
not a server-issued or cryptographically random token — so any caller who
supplied (or happened to collide on) another brand's session_id with a
DIFFERENT brand_id would still be handed that other brand's existing
conversation: their message gets appended to it, and the full prior
history/customer_email travels back in the response. Fixed by requiring
brand_id in the same lookup query.

get_chat_history and update_session_email had the identical bug (no
brand_id parameter at all) with zero live callers anywhere in the
frontend, so they were removed rather than patched — see the comment left
in their place in v2_chat_widget.py.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.api.routes.v2_chat_widget import _get_or_create_session  # noqa: E402

SESSION_ID = "cs_shared0abc123"  # a session_id an attacker guessed/observed from brand B


def test_a_brands_own_session_ticket_is_not_returned_to_a_different_brand():
    """The core regression: brand A's request, carrying brand B's
    session_id, must NOT be handed brand B's existing conversation."""
    brand_b_ticket = {
        "id": "ticket-b-1", "brand_id": "brand-B", "store_id": "brand-B",
        "gmail_thread_id": SESSION_ID, "channel": "chat",
        "customer_email": "victim@brand-b-customer.com",
        "messages": [{"direction": "inbound", "body": "my card number is ...", "role": "user"}],
    }

    def fake_select(table, params=None):
        params = params or {}
        if table != "tickets":
            return []
        # Simulate a real PostgREST AND-filtered query: every param must match.
        if params.get("gmail_thread_id") != f"eq.{SESSION_ID}":
            return []
        if params.get("channel") != "eq.chat":
            return []
        wanted_brand = params.get("brand_id", "").removeprefix("eq.")
        if wanted_brand and brand_b_ticket["brand_id"] != wanted_brand:
            return []  # the fix: brand filter excludes brand B's ticket for anyone else
        return [brand_b_ticket]

    created = {}

    def fake_insert(table, data):
        row = {**data, "id": "ticket-a-new"}
        created["row"] = row
        return row

    with patch("src.api.routes.v2_chat_widget.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_chat_widget.supabase_insert", side_effect=fake_insert):
        result = _get_or_create_session(SESSION_ID, brand_id="brand-A")

    # Must NOT be brand B's ticket (would leak their conversation + email).
    assert result["id"] != "ticket-b-1"
    assert result.get("customer_email") != "victim@brand-b-customer.com"
    # A fresh ticket was created, correctly scoped to brand A.
    assert created["row"]["brand_id"] == "brand-A"
    assert created["row"]["store_id"] == "brand-A"


def test_b_brand_can_still_resume_its_own_session():
    """Positive control: the fix must not break a brand resuming its own
    genuine session — proves the isolation above is real, not a broken
    lookup that always creates a new ticket."""
    own_ticket = {
        "id": "ticket-a-1", "brand_id": "brand-A", "store_id": "brand-A",
        "gmail_thread_id": SESSION_ID, "channel": "chat",
        "customer_email": "real-customer@example.com",
        "messages": [{"direction": "inbound", "body": "hi", "role": "user"}],
    }

    def fake_select(table, params=None):
        params = params or {}
        if table != "tickets":
            return []
        if params.get("gmail_thread_id") != f"eq.{SESSION_ID}":
            return []
        if params.get("brand_id") != "eq.brand-A":
            return []
        return [own_ticket]

    with patch("src.api.routes.v2_chat_widget.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_chat_widget.supabase_insert") as mock_insert:
        result = _get_or_create_session(SESSION_ID, brand_id="brand-A")

    assert result["id"] == "ticket-a-1"
    assert result["customer_email"] == "real-customer@example.com"
    mock_insert.assert_not_called()  # reused the existing session, didn't fork a duplicate
