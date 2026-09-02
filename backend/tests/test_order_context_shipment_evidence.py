"""
Merchant-facing WISMO evidence (fetch_shopify_order's new `shipments` field).

Gap closed: Luna's live Aftership lookup only ever existed transiently
inside process_customer_query()'s tool_results - never returned to the
frontend, so a merchant looking at the same ticket saw only the raw
Shopify tracking_number/carrier, never the normalized status/latest event/
ETA Luna actually used. fetch_shopify_order (already the one live,
30s-cached call TicketDetail's Order Context panel makes on every view -
see test_order_context_caching.py) now also returns a `shipments` list,
one entry per real fulfillment, enriched with the same live Aftership
lookup tracking_service.py already provides - reusing it, not duplicating
it. No new persistence, no new provider, no new cache layer.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.shopify_service import fetch_shopify_order, _order_cache  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_cache():
    _order_cache.clear()
    yield
    _order_cache.clear()


def _order_with_fulfillments(*fulfillments):
    return {
        "success": True,
        "order": {
            "id": 123, "order_number": "1001", "name": "#1001",
            "financial_status": "paid", "fulfillment_status": "fulfilled",
            "total_price": "20.00", "currency": "USD", "created_at": "2026-01-01",
            "email": "c@example.com", "customer": {}, "line_items": [], "transactions": [],
            "fulfillments": list(fulfillments),
        },
    }


def _tracking_result(normalized, event, location, eta):
    return {
        "normalized_status": normalized, "status_description": event,
        "latest_event": event, "latest_event_location": location,
        "latest_event_timestamp": "2026-09-01T00:00:00Z", "expected_delivery": eta,
    }


BRAND_NO_AFTERSHIP = {"id": "brand-1", "shopify_domain": "test.myshopify.com", "shopify_access_token": "enc"}
BRAND_WITH_AFTERSHIP = {**BRAND_NO_AFTERSHIP, "aftership_api_key": "test-key"}


@pytest.mark.asyncio
async def test_no_fulfillments_yields_empty_shipments_list_no_fabrication():
    order_resp = {"success": True, "order": {
        "id": 123, "order_number": "1001", "name": "#1001", "financial_status": "paid",
        "fulfillment_status": None, "total_price": "20.00", "currency": "USD",
        "created_at": "2026-01-01", "email": "c@example.com", "customer": {},
        "line_items": [], "transactions": [],
    }}
    with patch("src.services.shopify_service.decrypt_token", return_value="tok"), \
         patch("src.services.shopify_service.ShopifyClient.get_order", new=AsyncMock(return_value=order_resp)):
        result = await fetch_shopify_order(BRAND_WITH_AFTERSHIP, "1001")

    assert result["shipments"] == []
    assert result["tracking_number"] is None


@pytest.mark.asyncio
async def test_no_aftership_key_returns_shopify_fields_only_no_live_call(monkeypatch):
    """Must exercise the true "no credential resolves at all" case - not the
    developer's real local .env AFTERSHIP_API_KEY. resolve_aftership_api_key()
    falls back to the module-level PLATFORM_AFTERSHIP_API_KEY (set once, at
    import time, from the real environment) whenever a brand has no key of
    its own, so this brand-scoped assertion is only meaningful with that
    platform key forced off for the duration of this test. monkeypatch
    reverts it automatically even on failure, and never touches os.environ,
    so no other test (or the real key already imported into tracking_mod)
    is affected."""
    import src.services.tracking_service as tracking_mod
    monkeypatch.setattr(tracking_mod, "PLATFORM_AFTERSHIP_API_KEY", None)

    fulfillment = {"tracking_number": "TN1", "tracking_url": "https://x/TN1", "tracking_company": "USPS"}
    with patch("src.services.shopify_service.decrypt_token", return_value="tok"), \
         patch("src.services.shopify_service.ShopifyClient.get_order", new=AsyncMock(return_value=_order_with_fulfillments(fulfillment))), \
         patch("src.services.tracking_service.get_tracking_status", new=AsyncMock()) as mock_tracking:
        result = await fetch_shopify_order(BRAND_NO_AFTERSHIP, "1001")

    mock_tracking.assert_not_awaited()
    assert len(result["shipments"]) == 1
    s = result["shipments"][0]
    assert s["tracking_number"] == "TN1"
    assert s["carrier"] == "USPS"
    assert s["normalized_status"] is None  # never fabricated


@pytest.mark.asyncio
async def test_with_aftership_key_enriches_shipment_with_live_status():
    fulfillment = {"tracking_number": "TN1", "tracking_url": "https://x/TN1", "tracking_company": "USPS"}
    live = _tracking_result("IN_TRANSIT", "Arrived at Austin facility", "Austin, TX", "2026-09-02")
    with patch("src.services.shopify_service.decrypt_token", return_value="tok"), \
         patch("src.services.shopify_service.ShopifyClient.get_order", new=AsyncMock(return_value=_order_with_fulfillments(fulfillment))), \
         patch("src.services.tracking_service.get_tracking_status", new=AsyncMock(return_value=live)):
        result = await fetch_shopify_order(BRAND_WITH_AFTERSHIP, "1001")

    s = result["shipments"][0]
    assert s["normalized_status"] == "IN_TRANSIT"
    assert s["latest_event"] == "Arrived at Austin facility"
    assert s["latest_event_location"] == "Austin, TX"
    assert s["estimated_delivery"] == "2026-09-02"


@pytest.mark.asyncio
async def test_aftership_failure_leaves_shipment_fields_null_never_fabricated():
    fulfillment = {"tracking_number": "TN1", "tracking_url": None, "tracking_company": "USPS"}
    with patch("src.services.shopify_service.decrypt_token", return_value="tok"), \
         patch("src.services.shopify_service.ShopifyClient.get_order", new=AsyncMock(return_value=_order_with_fulfillments(fulfillment))), \
         patch("src.services.tracking_service.get_tracking_status", new=AsyncMock(return_value=None)):
        result = await fetch_shopify_order(BRAND_WITH_AFTERSHIP, "1001")

    s = result["shipments"][0]
    assert s["tracking_number"] == "TN1"  # real Shopify fact, still shown
    assert s["normalized_status"] is None  # no fake status when the provider gave nothing
    assert s["latest_event"] is None


@pytest.mark.asyncio
async def test_multiple_fulfillments_each_get_their_own_shipment_entry():
    f1 = {"tracking_number": "TN-A", "tracking_url": None, "tracking_company": "UPS"}
    f2 = {"tracking_number": "TN-B", "tracking_url": None, "tracking_company": "USPS"}
    delivered = _tracking_result("DELIVERED", "Delivered", "Front door", None)
    in_transit = _tracking_result("IN_TRANSIT", "In transit", None, None)

    async def fake_tracking(tracking_number, carrier_slug, api_key):
        return delivered if tracking_number == "TN-A" else in_transit

    with patch("src.services.shopify_service.decrypt_token", return_value="tok"), \
         patch("src.services.shopify_service.ShopifyClient.get_order", new=AsyncMock(return_value=_order_with_fulfillments(f1, f2))), \
         patch("src.services.tracking_service.get_tracking_status", new=fake_tracking):
        result = await fetch_shopify_order(BRAND_WITH_AFTERSHIP, "1001")

    assert len(result["shipments"]) == 2
    assert result["shipments"][0]["normalized_status"] == "DELIVERED"
    assert result["shipments"][1]["normalized_status"] == "IN_TRANSIT"


@pytest.mark.asyncio
async def test_unmapped_carrier_skips_live_lookup_gracefully():
    fulfillment = {"tracking_number": "TN1", "tracking_url": None, "tracking_company": "Some Obscure Regional Courier"}
    with patch("src.services.shopify_service.decrypt_token", return_value="tok"), \
         patch("src.services.shopify_service.ShopifyClient.get_order", new=AsyncMock(return_value=_order_with_fulfillments(fulfillment))), \
         patch("src.services.tracking_service.get_tracking_status", new=AsyncMock()) as mock_tracking:
        result = await fetch_shopify_order(BRAND_WITH_AFTERSHIP, "1001")

    mock_tracking.assert_not_awaited()
    assert result["shipments"][0]["normalized_status"] is None
