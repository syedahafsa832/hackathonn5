"""
Gmail threading bug: a customer's Gmail "Reply" to Luna's auto-reply
created a brand-new ticket instead of continuing the existing one.

Root cause: brand_gmail_service.send_email() sent every outbound reply
with no threadId (Gmail assigns a fresh thread to every send() call
unless threadId is explicitly passed - a "Re:" subject alone doesn't do
this). So the customer's next Gmail "Reply" landed in a Gmail thread our
poller had never seen, and message_processor.py STAGE 1.5's
gmail_thread_id match always missed, creating a duplicate ticket.

Fix: _send_email_with_logging (message_processor.py) now looks up the
ticket's own gmail_thread_id and passes it through to send_email(), which
now accepts and forwards a threadId to the Gmail API.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.workers.message_processor import UnifiedMessageProcessor  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def test_auto_reply_send_passes_ticket_gmail_thread_id():
    proc = UnifiedMessageProcessor()
    mock_send = AsyncMock(return_value={"success": True, "id": "msg-2"})

    def fake_select(table, params=None):
        if table == "brands":
            return [{"id": "brand-1", "gmail_connected": True, "gmail_email": "b@brand.com"}]
        if table == "tickets":
            return [{"id": "ticket-1", "gmail_thread_id": "thread-abc"}]
        return []

    with patch("src.lib.supabase_client.supabase_select", side_effect=fake_select), \
         patch("src.workers.message_processor.supabase_update"), \
         patch("src.services.brand_gmail_service.brand_gmail_service.send_email", new=mock_send):
        run(proc._send_email_with_logging(
            "customer@example.com", "Where is my order?",
            {"reply_body": "Here's what I found.", "reply_subject": "Re: Where is my order?"},
            "ticket-1", store_id="brand-1",
        ))

    mock_send.assert_awaited_once()
    _, kwargs = mock_send.call_args
    assert kwargs.get("thread_id") == "thread-abc"


def test_send_email_forwards_thread_id_to_gmail_api():
    from unittest.mock import MagicMock
    from src.services.brand_gmail_service import BrandGmailService
    svc = BrandGmailService()
    mock_service = MagicMock()
    mock_service.users.return_value.messages.return_value.send.return_value.execute.return_value = {"id": "sent-1"}

    with patch.object(svc, "_build_service", return_value=mock_service):
        run(svc.send_email({"name": "Brand"}, "c@example.com", "Re: hi", "body text", thread_id="thread-abc"))

    _, kwargs = mock_service.users.return_value.messages.return_value.send.call_args
    assert kwargs["body"].get("threadId") == "thread-abc"


def test_send_email_omits_thread_id_when_not_given():
    """Backward-compat: a first-ever reply (no ticket/thread yet) must not
    send a broken/empty threadId."""
    from unittest.mock import MagicMock
    from src.services.brand_gmail_service import BrandGmailService
    svc = BrandGmailService()
    mock_service = MagicMock()
    mock_service.users.return_value.messages.return_value.send.return_value.execute.return_value = {"id": "sent-1"}

    with patch.object(svc, "_build_service", return_value=mock_service):
        run(svc.send_email({"name": "Brand"}, "c@example.com", "hi", "body text"))

    _, kwargs = mock_service.users.return_value.messages.return_value.send.call_args
    assert "threadId" not in kwargs["body"]
