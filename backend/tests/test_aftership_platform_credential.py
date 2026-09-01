"""
Platform-level AfterShip credential (H1 architecture fix).

Before: every brand needed its own aftership_api_key row before Luna could
give live tracking updates - most brands never configured one, so WISMO
silently degraded to raw-link sharing for the overwhelming majority of
merchants.

After: ONE AFTERSHIP_API_KEY (platform env var) drives tracking_service.py
for every connected brand. resolve_aftership_api_key() is the single choke
point every caller now goes through (customer_success_agent.py,
shopify_service.py's fetch_shopify_order) instead of reading
brand.aftership_api_key directly. The legacy per-brand column/endpoint
still works as a fallback/override - nothing already relying on it breaks -
but a merchant no longer needs to configure anything.

Security invariant this file exists to prove: a SHARED credential must
never mean SHARED shipment access. Every lookup is still gated on that
specific brand's own, ownership-verified Shopify fulfillment data - the
credential change doesn't touch that chain at all.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import src.services.tracking_service as tracking_mod  # noqa: E402
from src.services.tracking_service import resolve_aftership_api_key  # noqa: E402
from src.services.shopify_service import fetch_shopify_order, _order_cache  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_platform_key_and_cache():
    """PLATFORM_AFTERSHIP_API_KEY is a module-level constant evaluated once
    at import time from os.environ - patching os.environ afterward wouldn't
    change it, so tests control it directly via monkeypatch on the module
    attribute instead, independent of whatever the real process env/.env
    happens to contain."""
    original = tracking_mod.PLATFORM_AFTERSHIP_API_KEY
    _order_cache.clear()
    yield
    tracking_mod.PLATFORM_AFTERSHIP_API_KEY = original
    _order_cache.clear()


# ── resolve_aftership_api_key ───────────────────────────────────────────────

def test_platform_key_wins_over_a_brands_own_legacy_key():
    tracking_mod.PLATFORM_AFTERSHIP_API_KEY = "platform-shared-key"
    assert resolve_aftership_api_key({"id": "brand-1", "aftership_api_key": "brand-own-key"}) == "platform-shared-key"


def test_falls_back_to_brand_key_when_no_platform_key_configured():
    tracking_mod.PLATFORM_AFTERSHIP_API_KEY = None
    assert resolve_aftership_api_key({"id": "brand-1", "aftership_api_key": "brand-own-key"}) == "brand-own-key"


def test_returns_none_when_neither_platform_nor_brand_key_exists():
    tracking_mod.PLATFORM_AFTERSHIP_API_KEY = None
    assert resolve_aftership_api_key({"id": "brand-1"}) is None
    assert resolve_aftership_api_key(None) is None


def test_multiple_brands_share_the_identical_platform_credential():
    """The core architecture requirement: one platform key, many brands -
    not one key minted per brand."""
    tracking_mod.PLATFORM_AFTERSHIP_API_KEY = "platform-shared-key"
    brand_a = {"id": "brand-a"}  # no per-brand key configured - not required
    brand_b = {"id": "brand-b"}
    brand_c = {"id": "brand-c", "aftership_api_key": "old-legacy-key-c"}  # even one with a stale legacy key
    assert resolve_aftership_api_key(brand_a) == "platform-shared-key"
    assert resolve_aftership_api_key(brand_b) == "platform-shared-key"
    assert resolve_aftership_api_key(brand_c) == "platform-shared-key"  # platform still wins


# ── Security: shared credential must never mean shared shipment access ─────

def _order_with_tracking(order_number, tracking_number, carrier="USPS"):
    return {
        "success": True,
        "order": {
            "id": 1, "order_number": order_number, "name": f"#{order_number}",
            "financial_status": "paid", "fulfillment_status": "fulfilled",
            "total_price": "20.00", "currency": "USD", "created_at": "2026-01-01",
            "email": "c@example.com", "customer": {}, "line_items": [], "transactions": [],
            "fulfillments": [{"tracking_number": tracking_number, "tracking_url": None, "tracking_company": carrier}],
        },
    }


@pytest.mark.asyncio
async def test_brand_a_lookup_never_uses_brand_bs_tracking_number():
    """Same shared platform key for both brands - the tracking NUMBER
    queried must still come only from that specific brand's own,
    independently-fetched Shopify order. Proves the shared credential
    doesn't blur which shipment gets looked up."""
    tracking_mod.PLATFORM_AFTERSHIP_API_KEY = "platform-shared-key"
    brand_a = {"id": "brand-a", "shopify_domain": "a.myshopify.com", "shopify_access_token": "enc-a"}
    brand_b = {"id": "brand-b", "shopify_domain": "b.myshopify.com", "shopify_access_token": "enc-b"}

    queried_numbers = []

    async def fake_tracking(tracking_number, carrier_slug, api_key):
        queried_numbers.append(tracking_number)
        assert api_key == "platform-shared-key"  # same shared key either way
        return None

    with patch("src.services.shopify_service.decrypt_token", return_value="tok"), \
         patch("src.services.shopify_service.ShopifyClient.get_order", new=AsyncMock(
             side_effect=[_order_with_tracking("1001", "TRACK-A-ONLY"), _order_with_tracking("2001", "TRACK-B-ONLY")]
         )), \
         patch("src.services.tracking_service.get_tracking_status", new=fake_tracking):
        result_a = await fetch_shopify_order(brand_a, "1001")
        result_b = await fetch_shopify_order(brand_b, "2001")

    assert queried_numbers == ["TRACK-A-ONLY", "TRACK-B-ONLY"]
    assert result_a["shipments"][0]["tracking_number"] == "TRACK-A-ONLY"
    assert result_b["shipments"][0]["tracking_number"] == "TRACK-B-ONLY"


@pytest.mark.asyncio
async def test_arbitrary_customer_supplied_tracking_number_is_never_queried():
    """Only tracking numbers extracted from the order's own real Shopify
    fulfillments are ever passed to Aftership - a customer message can
    never inject an arbitrary tracking number into the lookup, regardless
    of which key (platform or legacy) is active."""
    tracking_mod.PLATFORM_AFTERSHIP_API_KEY = "platform-shared-key"
    brand = {"id": "brand-1", "shopify_domain": "x.myshopify.com", "shopify_access_token": "enc"}

    async def fake_tracking(tracking_number, carrier_slug, api_key):
        assert tracking_number == "REAL-TRACK-1"  # never a customer-supplied string
        return None

    # Simulates a customer message like "track XYZ-CUSTOMER-SUPPLIED-999" -
    # fetch_shopify_order has no code path that reads message text at all;
    # it only ever iterates the order's own fulfillments.
    with patch("src.services.shopify_service.decrypt_token", return_value="tok"), \
         patch("src.services.shopify_service.ShopifyClient.get_order", new=AsyncMock(
             return_value=_order_with_tracking("1001", "REAL-TRACK-1")
         )), \
         patch("src.services.tracking_service.get_tracking_status", new=fake_tracking):
        result = await fetch_shopify_order(brand, "1001")

    assert result["shipments"][0]["tracking_number"] == "REAL-TRACK-1"


# ── Credential never reaches the frontend or the LLM ────────────────────────

def test_aftership_status_endpoint_never_returns_the_raw_platform_key():
    """The settings endpoint reports availability, never the credential
    itself - platform_managed=True is enough for the UI, no key value."""
    import asyncio
    from src.api.routes.saas_settings import get_aftership_status

    tracking_mod.PLATFORM_AFTERSHIP_API_KEY = "super-secret-platform-key"

    class _FakeTenant:
        tenant_id = "tenant-1"

    with patch("src.api.routes.saas_settings._get_tenant_brand_async", new=AsyncMock(return_value={"id": "brand-1"})):
        result = asyncio.get_event_loop().run_until_complete(get_aftership_status(tenant=_FakeTenant()))

    assert result["connected"] is True
    assert result["platform_managed"] is True
    body_str = str(result)
    assert "super-secret-platform-key" not in body_str


def test_tracking_context_sent_to_the_llm_never_contains_the_api_key():
    """build_tracking_context/build_shipment_context (the only text that
    reaches the LLM prompt) must never leak the credential, regardless of
    which key resolved it."""
    from src.services.tracking_service import build_tracking_context
    info = {
        "status": "InTransit", "status_text": "In transit", "latest_location": "Austin",
        "latest_message": "In transit", "latest_time": None, "expected_delivery": None,
        "recent_checkpoints": [],
    }
    ctx = build_tracking_context(info, "TN1", None, "USPS")
    assert "super-secret-platform-key" not in ctx
    assert "asat_" not in ctx  # AfterShip key prefix never appears in any form
