"""
Order Context caching (item 6 of the bug-fix pass).

TicketDetail's "Order Context" panel calls fetch_shopify_order on every
ticket-detail page load, which used to always make a live Shopify API call —
no caching at all. This adds a short-TTL cache and proves: (a) a second call
for the same brand+order within the TTL window does not hit Shopify again,
(b) invalidate_order_cache forces the next call to go live again, so a
cancel/refund is never followed by a reload showing stale order data.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.shopify_service import (  # noqa: E402
    fetch_shopify_order,
    invalidate_order_cache,
    _order_cache,
)

BRAND = {"id": "brand-1", "shopify_domain": "test.myshopify.com", "shopify_access_token": "encrypted-token"}


def _fake_order(financial_status="pending"):
    return {
        "success": True,
        "order": {
            "id": 123, "order_number": "1001", "name": "#1001",
            "financial_status": financial_status, "fulfillment_status": None,
            "total_price": "20.00", "currency": "USD", "created_at": "2026-01-01",
            "email": "c@example.com", "customer": {}, "line_items": [], "transactions": [],
        },
    }


@pytest.fixture(autouse=True)
def _clear_cache():
    _order_cache.clear()
    yield
    _order_cache.clear()


@pytest.mark.asyncio
async def test_second_fetch_within_ttl_does_not_call_shopify_again():
    with patch("src.services.shopify_service.decrypt_token", return_value="plain-token"), \
         patch("src.services.shopify_service.ShopifyClient.get_order", new=AsyncMock(return_value=_fake_order())) as mock_get:
        first = await fetch_shopify_order(BRAND, "1001")
        second = await fetch_shopify_order(BRAND, "1001")

    assert first["financial_status"] == "pending"
    assert second == first
    assert mock_get.call_count == 1  # second call served from cache


@pytest.mark.asyncio
async def test_invalidate_forces_a_fresh_fetch():
    with patch("src.services.shopify_service.decrypt_token", return_value="plain-token"), \
         patch("src.services.shopify_service.ShopifyClient.get_order", new=AsyncMock(side_effect=[
             _fake_order("pending"), _fake_order("refunded"),
         ])) as mock_get:
        before = await fetch_shopify_order(BRAND, "1001")
        invalidate_order_cache("brand-1", "1001")
        after = await fetch_shopify_order(BRAND, "1001")

    assert before["financial_status"] == "pending"
    assert after["financial_status"] == "refunded"
    assert mock_get.call_count == 2


@pytest.mark.asyncio
async def test_different_orders_do_not_share_a_cache_entry():
    with patch("src.services.shopify_service.decrypt_token", return_value="plain-token"), \
         patch("src.services.shopify_service.ShopifyClient.get_order", new=AsyncMock(return_value=_fake_order())) as mock_get:
        await fetch_shopify_order(BRAND, "1001")
        await fetch_shopify_order(BRAND, "1002")

    assert mock_get.call_count == 2
