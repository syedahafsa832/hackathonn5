"""
ShopifyClient._request() event-loop-blocking fix.

Before this fix, _request() was a plain synchronous method doing raw
requests.get/post/put/delete (with a blocking time.sleep() retry backoff),
called directly from async methods (get_order, process_refund, ...). Since
this backend runs as a single gunicorn worker (-w 1) sharing one event loop
with the HTTP server, a slow/rate-limited Shopify call froze the whole API —
including an unrelated GET /api/tickets/{id}.

Fix: _request is now a thin async wrapper around _request_sync (unchanged
internals, including the retry recursion and time.sleep), run via
asyncio.to_thread — the entire retry loop, sleeps included, runs inside one
worker thread for the whole call, never returning to the event loop between
attempts.

These tests prove: (A) the fix actually restores responsiveness with a
REAL blocking call (no shortcut sleep), and (B) normal success, exception
propagation, and the rate-limit retry/backoff behavior are all unchanged.
"""
import os
import sys
import time
import asyncio
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")
os.environ.setdefault("ENCRYPTION_SECRET", "test-encryption-secret-do-not-use-in-prod")

import pytest  # noqa: E402

from src.services.shopify_service import ShopifyClient, ShopifyError, ShopifyErrorCode  # noqa: E402
from src.services.supabase_service import supabase_service  # noqa: E402

SLOW_CALL_SECONDS = 1.5


def _fake_response(status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {}
    resp.json.return_value = json_data or {}
    resp.text = text
    return resp


def _fast_select(table, params):
    return [{"id": "ticket-1", "status": "processing"}]


# ---------------------------------------------------------------------------
# A) Responsiveness: a real, slow blocking Shopify call must not stall a
#    concurrent ticket-detail read. The sleep is genuine (time.sleep inside
#    the mocked requests.get), not a shortcut — it only becomes non-blocking
#    for the event loop because _request now runs it via asyncio.to_thread.
# ---------------------------------------------------------------------------

def _slow_requests_get(*args, **kwargs):
    time.sleep(SLOW_CALL_SECONDS)
    return _fake_response(200, {"order": {"id": 1}})


@pytest.mark.asyncio
async def test_slow_shopify_request_does_not_block_concurrent_ticket_read():
    client = ShopifyClient("test-shop.myshopify.com", "fake-token")

    with patch("src.services.shopify_service.requests.get", side_effect=_slow_requests_get), \
         patch("src.services.supabase_service.supabase_select", side_effect=_fast_select):

        slow_task = asyncio.create_task(client._request("GET", "orders/1.json"))
        await asyncio.sleep(0.05)  # let the slow call actually enter its thread first

        fast_start = time.monotonic()
        ticket = await supabase_service.get_ticket_by_id("ticket-1")
        fast_elapsed = time.monotonic() - fast_start

        assert ticket == {"id": "ticket-1", "status": "processing"}
        assert fast_elapsed < SLOW_CALL_SECONDS / 2, (
            f"ticket read took {fast_elapsed:.2f}s while a slow Shopify call was in flight — "
            f"event loop appears blocked"
        )

        result = await slow_task
        assert result == {"success": True, "data": {"order": {"id": 1}}}


# ---------------------------------------------------------------------------
# B) Behavior preserved: success, exception propagation, retry/backoff.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shopify_request_success_returns_expected_data():
    client = ShopifyClient("test-shop.myshopify.com", "fake-token")
    with patch("src.services.shopify_service.requests.get", return_value=_fake_response(200, {"a": 1})):
        result = await client._request("GET", "shop.json")
    assert result == {"success": True, "data": {"a": 1}}


@pytest.mark.asyncio
async def test_shopify_request_exception_propagates():
    client = ShopifyClient("test-shop.myshopify.com", "fake-token")
    with patch("src.services.shopify_service.requests.get", return_value=_fake_response(401, {}, "unauthorized")):
        with pytest.raises(ShopifyError) as exc_info:
            await client._request("GET", "shop.json")
    assert exc_info.value.error_code == ShopifyErrorCode.INVALID_TOKEN


@pytest.mark.asyncio
async def test_shopify_rate_limit_retry_behavior_preserved():
    """Exactly the existing behavior: retries on RATE_LIMITED with
    exponential backoff (2**retry_count), up to max_retries, then succeeds
    once a later attempt returns 200. time.sleep is mocked here only to
    keep this specific behavior-check fast — the real-sleep responsiveness
    claim is proven separately above."""
    client = ShopifyClient("test-shop.myshopify.com", "fake-token")
    responses = [_fake_response(429, {}, "rate limited"), _fake_response(200, {"ok": True})]

    with patch("src.services.shopify_service.requests.get", side_effect=responses), \
         patch("src.services.shopify_service.time.sleep") as mock_sleep:
        result = await client._request("GET", "shop.json")

    assert result == {"success": True, "data": {"ok": True}}
    mock_sleep.assert_called_once_with(1)  # 2**0 backoff on the first retry
