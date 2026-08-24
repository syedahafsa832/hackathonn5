"""
AI Draft edit-before-send: POST /api/v2/tickets/{id}/approve-ai previously
always sent ticket.ai_response/ai_draft verbatim - the frontend AI Draft
panel had no editable field at all, only "Approve & Send". A human could
never correct Luna's wording before it reached the customer.

Fix: the endpoint now accepts an optional request.body - the human's
edited text - and uses it as reply_body instead of the unedited draft when
given. Everything downstream (the actual Gmail send, and the message
appended for Conversation Replay via _promote_or_append_ai_message) already
used that same reply_body variable, so passing the edited text through it
is the only change needed: the original ai_draft/ai_response DB fields are
never overwritten (preserved for audit), and Conversation Replay shows
exactly what was sent because _promote_or_append_ai_message appends a new
outbound entry whenever the sent text doesn't exactly match the original
draft entry it's tracking.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.api.routes.v2_tickets import approve_ai_response, ApproveAiRequest  # noqa: E402
from src.api.middleware.auth_middleware import AuthenticatedContext, UserContext, UserRole  # noqa: E402

BRAND_ID = "brand-1"
TICKET_ID = "ticket-1"


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _context():
    return AuthenticatedContext(
        user=UserContext(user_id="user-1", supabase_auth_id="auth-1",
                          organization_id="org-1", email="agent@example.com",
                          role=UserRole.ADMIN, brands=[BRAND_ID]),
        organization=None, brand_ids=[BRAND_ID],
    )


def _ticket(**overrides):
    t = {
        "id": TICKET_ID, "brand_id": BRAND_ID, "store_id": BRAND_ID,
        "customer_email": "customer@example.com", "subject": "Where is my order?",
        "ai_draft": "hey, i can't pull it up right now.", "ai_response": None,
        "human_approved": False, "response_sent": False,
        "messages": [{"from": "AI Agent", "body": "hey, i can't pull it up right now.",
                       "direction": "draft", "role": "assistant"}],
    }
    t.update(overrides)
    return t


def _run(request_body, ticket_overrides=None):
    ticket = _ticket(**(ticket_overrides or {}))
    mock_send = AsyncMock(return_value={"success": True})
    captured_update = {}

    def fake_select(table, params=None):
        if table == "tickets":
            return [ticket]
        if table == "brands":
            return [{"id": BRAND_ID, "gmail_connected": True}]
        return []

    def fake_update(table, match, fields):
        captured_update.update(fields)
        return [{**ticket, **fields}]

    with patch("src.api.routes.v2_tickets.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_tickets.supabase_update", side_effect=fake_update), \
         patch("src.services.brand_gmail_service.brand_gmail_service.send_email", new=mock_send):
        result = run(approve_ai_response(TICKET_ID, ApproveAiRequest(body=request_body), _context()))

    return result, mock_send, captured_update, ticket


def test_edited_draft_is_the_exact_text_sent_to_the_customer():
    edited = "Hi! I found order #1013 - it was cancelled and the item was restocked."
    result, mock_send, captured_update, ticket = _run(edited)

    assert result["success"] is True
    sent_args = mock_send.call_args.args
    assert sent_args[3] == edited  # send_email(brand, to, subject, body)


def test_edited_draft_appears_correctly_in_conversation_replay():
    edited = "Hi! I found order #1013 - it was cancelled and the item was restocked."
    _, _, captured_update, ticket = _run(edited)

    messages = captured_update["messages"]
    sent_messages = [m for m in messages if m.get("direction") == "outbound"]
    assert len(sent_messages) == 1
    assert sent_messages[0]["body"] == edited
    # The original draft is not silently lost - it's a separate entry.
    assert any(m.get("body") == "hey, i can't pull it up right now." for m in messages)


def test_original_ai_draft_field_is_never_overwritten_by_the_edit():
    edited = "Hi! I found order #1013 - it was cancelled and the item was restocked."
    _, _, captured_update, ticket = _run(edited)

    assert "ai_draft" not in captured_update  # untouched - preserved for audit
    assert ticket["ai_draft"] == "hey, i can't pull it up right now."


def test_unedited_approve_still_sends_the_original_draft_unchanged():
    """Regression guard: existing approve-with-no-edit behavior is
    unchanged when no body is given."""
    result, mock_send, captured_update, ticket = _run(None)

    assert result["success"] is True
    sent_args = mock_send.call_args.args
    assert sent_args[3] == "hey, i can't pull it up right now."
    sent_messages = [m for m in captured_update["messages"] if m.get("direction") == "outbound"]
    assert len(sent_messages) == 1
    assert sent_messages[0]["body"] == "hey, i can't pull it up right now."


def test_blank_body_falls_back_to_original_draft_not_an_empty_send():
    result, mock_send, _, _ = _run("   ")
    sent_args = mock_send.call_args.args
    assert sent_args[3] == "hey, i can't pull it up right now."
