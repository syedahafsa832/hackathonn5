"""
BrandGmailService send-path event-loop-blocking fix.

send_email / send_html_reply_in_thread / send_reply_in_thread called the
Google API client's synchronous .execute() directly from async methods.
Google's client has no async variant, so — same root cause as the Shopify
fix — a slow send held the single shared event loop, freezing
GET /api/tickets/{id} for the whole request.

Fix mirrors the pre-existing get_new_emails / _get_new_emails_sync pattern:
each send method's real work moved into a *_sync twin (unchanged), and the
public async method now does `await asyncio.to_thread(self._..._sync, ...)`.

Tests: (A) responsiveness with a REAL blocking .execute() (no shortcut
sleep), and (B) normal success / exception propagation preserved for all
three send methods.
"""
import os
import sys
import time
import asyncio
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import pytest  # noqa: E402

from src.services.brand_gmail_service import BrandGmailService  # noqa: E402
from src.services.supabase_service import supabase_service  # noqa: E402

SLOW_CALL_SECONDS = 1.5


def _fast_select(table, params):
    return [{"id": "ticket-1", "status": "processing"}]


def _mock_gmail_service_with_slow_execute():
    """A fake googleapiclient service whose send(...).execute() genuinely
    blocks for SLOW_CALL_SECONDS before returning — same shape as the real
    thing (svc.users().messages().send(...).execute())."""
    def _slow_execute():
        time.sleep(SLOW_CALL_SECONDS)
        return {"id": "msg-1"}
    svc = MagicMock()
    svc.users.return_value.messages.return_value.send.return_value.execute.side_effect = _slow_execute
    return svc


def _mock_gmail_service_with_fast_execute(result=None):
    svc = MagicMock()
    svc.users.return_value.messages.return_value.send.return_value.execute.return_value = result or {"id": "msg-1"}
    return svc


def _mock_gmail_service_raising(exc):
    svc = MagicMock()
    svc.users.return_value.messages.return_value.send.return_value.execute.side_effect = exc
    return svc


# ---------------------------------------------------------------------------
# A) Responsiveness
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_slow_gmail_send_does_not_block_concurrent_ticket_read():
    svc = BrandGmailService()
    with patch.object(svc, "_build_service", return_value=_mock_gmail_service_with_slow_execute()), \
         patch("src.services.supabase_service.supabase_select", side_effect=_fast_select):

        slow_task = asyncio.create_task(
            svc.send_email({"name": "Brand"}, "c@example.com", "Re: hi", "body text")
        )
        await asyncio.sleep(0.05)

        fast_start = time.monotonic()
        ticket = await supabase_service.get_ticket_by_id("ticket-1")
        fast_elapsed = time.monotonic() - fast_start

        assert ticket == {"id": "ticket-1", "status": "processing"}
        assert fast_elapsed < SLOW_CALL_SECONDS / 2, (
            f"ticket read took {fast_elapsed:.2f}s while a slow Gmail send was in flight — "
            f"event loop appears blocked"
        )

        result = await slow_task
        assert result == {"success": True, "id": "msg-1"}


# ---------------------------------------------------------------------------
# B) Behavior preserved: success + exception propagation, all three methods.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_email_success_returns_expected_result():
    svc = BrandGmailService()
    with patch.object(svc, "_build_service", return_value=_mock_gmail_service_with_fast_execute({"id": "sent-1"})):
        result = await svc.send_email({"name": "Brand"}, "c@example.com", "hi", "body")
    assert result == {"success": True, "id": "sent-1"}


@pytest.mark.asyncio
async def test_send_email_exception_is_caught_and_reported_not_raised():
    """Existing behavior: send_email never lets a Google API exception
    escape — it's caught and turned into {"success": False, "error": ...}."""
    svc = BrandGmailService()
    with patch.object(svc, "_build_service", return_value=_mock_gmail_service_raising(RuntimeError("boom"))):
        result = await svc.send_email({"name": "Brand"}, "c@example.com", "hi", "body")
    assert result == {"success": False, "error": "boom"}


@pytest.mark.asyncio
async def test_send_html_reply_in_thread_success_returns_expected_result():
    svc = BrandGmailService()
    with patch.object(svc, "_build_service", return_value=_mock_gmail_service_with_fast_execute({"id": "sent-2"})):
        result = await svc.send_html_reply_in_thread(
            {"name": "Brand"}, "c@example.com", "Rate us", "<p>html</p>", "plain", "thread-1"
        )
    assert result == {"success": True, "id": "sent-2"}


@pytest.mark.asyncio
async def test_send_reply_in_thread_success_returns_expected_result():
    svc = BrandGmailService()
    with patch.object(svc, "_build_service", return_value=_mock_gmail_service_with_fast_execute({"id": "sent-3"})):
        result = await svc.send_reply_in_thread(
            {"name": "Brand"}, "c@example.com", "hi", "body", "thread-1"
        )
    assert result == {"success": True, "id": "sent-3"}


@pytest.mark.asyncio
async def test_send_email_no_gmail_connected_returns_error_without_calling_google_api():
    """_build_service returning None (no stored token) must still be
    reported the same way — never a crash, never a call to a null service."""
    svc = BrandGmailService()
    with patch.object(svc, "_build_service", return_value=None):
        result = await svc.send_email({"name": "Brand"}, "c@example.com", "hi", "body")
    assert result == {"success": False, "error": "Gmail not connected for this brand"}
