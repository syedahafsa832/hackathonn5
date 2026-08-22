"""
Refund Policy Eligibility Tests
================================
Covers: default (unconfigured) policy behaves exactly like the old hardcoded
constants, a custom return window, excluded products, excluded collections,
digital-product exclusion, merchant-customized final-sale tags, and the two
staging-time safety checks (already refunded, already cancelled).

All Shopify/brand/DB calls are mocked — no live services required.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.actions_manager import actions_manager  # noqa: E402


def _order(days_old=10, fulfillment_status="fulfilled", tags="", total_price="100.00",
           refunds=None, cancelled_at=None, line_items=None):
    created_at = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    return {
        "id": 555,
        "order_number": "#1001",
        "email": "customer@example.com",
        "created_at": created_at,
        "fulfillment_status": fulfillment_status,
        "tags": tags,
        "total_price": total_price,
        "currency": "USD",
        "refunds": refunds or [],
        "cancelled_at": cancelled_at,
        "line_items": line_items or [
            {"id": 1, "product_id": 111, "title": "Basic Tee", "quantity": 1,
             "price": "100.00", "sku": "TEE-1", "requires_shipping": True},
        ],
    }


async def _run(order, brand=None, excluded_products=None, excluded_collections=None,
                product_collections=None, sender_email="customer@example.com"):
    """Runs check_return_eligibility with every external dependency mocked."""
    async def fake_get_order(order_id, email, tenant_id=None):
        return order

    def fake_select(table, params=None):
        if table == "refund_policy_excluded_products":
            return excluded_products or []
        if table == "refund_policy_excluded_collections":
            return excluded_collections or []
        return []

    async def fake_get_brand(brand_id):
        return brand

    async def fake_get_collections(tenant_id, product_id):
        return (product_collections or {}).get(product_id, [])

    with patch.object(actions_manager, "_get_order_from_shopify", side_effect=fake_get_order), \
         patch("src.services.actions_manager.supabase_select", side_effect=fake_select), \
         patch("src.services.brand_manager.brand_manager.get_brand", side_effect=fake_get_brand), \
         patch.object(actions_manager, "_get_product_collection_ids", side_effect=fake_get_collections), \
         patch("src.services.brand_knowledge_service.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value="")):
        return await actions_manager.check_return_eligibility(
            "1001", sender_email,
            tenant_id="t1", brand_id="b1" if brand is not None else None,
        )


# ─── 1. Default policy (no brand configured) ────────────────────────────────

@pytest.mark.asyncio
async def test_default_policy_matches_legacy_hardcoded_behavior():
    """No brand_id / brand not found -> falls back to the exact constants
    this function used before Phase 3. A default-configured brand must
    behave identically to today."""
    order = _order(days_old=10)
    result = await _run(order, brand=None)
    assert result["eligible"] is True
    assert result["policy_snapshot"]["window_days"] == actions_manager.RETURN_WINDOW_DAYS
    assert result["policy_snapshot"]["final_sale_tags"] == actions_manager.NON_RETURNABLE_TAGS


@pytest.mark.asyncio
async def test_default_policy_rejects_order_older_than_30_days():
    order = _order(days_old=45)
    result = await _run(order, brand={"id": "b1"})
    assert result["eligible"] is False
    assert "30 days" in result["reason"]


# ─── 2. Custom return window ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_custom_return_window_allows_order_default_would_reject():
    order = _order(days_old=45)
    brand = {"id": "b1", "return_policy_days": 60}
    result = await _run(order, brand=brand)
    assert result["eligible"] is True
    assert result["policy_snapshot"]["window_days"] == 60


@pytest.mark.asyncio
async def test_custom_return_window_still_rejects_beyond_custom_window():
    order = _order(days_old=70)
    brand = {"id": "b1", "return_policy_days": 60}
    result = await _run(order, brand=brand)
    assert result["eligible"] is False
    assert "60 days" in result["reason"]


# ─── 3. Excluded products ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_excluded_product_blocks_eligibility():
    order = _order(line_items=[
        {"id": 1, "product_id": 999, "title": "Limited Drop Sneaker", "quantity": 1,
         "price": "100.00", "requires_shipping": True},
    ])
    brand = {"id": "b1"}
    result = await _run(order, brand=brand, excluded_products=[{"shopify_product_id": 999}])
    assert result["eligible"] is False
    assert "Limited Drop Sneaker" in result["reason"]


@pytest.mark.asyncio
async def test_non_excluded_product_is_unaffected():
    order = _order(line_items=[
        {"id": 1, "product_id": 111, "title": "Basic Tee", "quantity": 1,
         "price": "100.00", "requires_shipping": True},
    ])
    brand = {"id": "b1"}
    result = await _run(order, brand=brand, excluded_products=[{"shopify_product_id": 999}])
    assert result["eligible"] is True


# ─── 4. Excluded collections ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_excluded_collection_blocks_eligibility():
    order = _order(line_items=[
        {"id": 1, "product_id": 222, "title": "Clearance Hoodie", "quantity": 1,
         "price": "80.00", "requires_shipping": True},
    ])
    brand = {"id": "b1"}
    result = await _run(
        order, brand=brand,
        excluded_collections=[{"shopify_collection_id": 77}],
        product_collections={222: [77, 88]},
    )
    assert result["eligible"] is False
    assert "Clearance Hoodie" in result["reason"]


@pytest.mark.asyncio
async def test_product_outside_excluded_collection_is_unaffected():
    order = _order(line_items=[
        {"id": 1, "product_id": 222, "title": "Regular Hoodie", "quantity": 1,
         "price": "80.00", "requires_shipping": True},
    ])
    brand = {"id": "b1"}
    result = await _run(
        order, brand=brand,
        excluded_collections=[{"shopify_collection_id": 77}],
        product_collections={222: [99]},
    )
    assert result["eligible"] is True


# ─── 5. Digital-product exclusion ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_digital_product_excluded_when_policy_enabled():
    order = _order(line_items=[
        {"id": 1, "product_id": 333, "title": "eBook Download", "quantity": 1,
         "price": "20.00", "requires_shipping": False},
    ])
    brand = {"id": "b1", "exclude_digital_products": True}
    result = await _run(order, brand=brand)
    assert result["eligible"] is False
    assert "eBook Download" in result["reason"]


@pytest.mark.asyncio
async def test_digital_product_allowed_when_policy_disabled():
    order = _order(line_items=[
        {"id": 1, "product_id": 333, "title": "eBook Download", "quantity": 1,
         "price": "20.00", "requires_shipping": False},
    ])
    brand = {"id": "b1", "exclude_digital_products": False}
    result = await _run(order, brand=brand)
    assert result["eligible"] is True


# ─── 6. Merchant-customized final-sale tags ──────────────────────────────────

@pytest.mark.asyncio
async def test_custom_final_sale_tag_blocks_eligibility():
    """A tag that is NOT in the default list, but IS in this brand's
    custom final_sale_tags, must still block — proves the policy value is
    actually being read, not just falling through to the old defaults."""
    order = _order(tags="clearance-bin")
    brand = {"id": "b1", "final_sale_tags": ["clearance-bin"]}
    result = await _run(order, brand=brand)
    assert result["eligible"] is False
    assert "Final Sale" in result["reason"]


@pytest.mark.asyncio
async def test_default_final_sale_tag_still_works_with_custom_brand():
    """Sanity check the other direction too: a brand with ITS OWN custom
    list no longer matches the hardcoded default tag."""
    order = _order(tags="final sale")
    brand = {"id": "b1", "final_sale_tags": ["clearance-bin"]}
    result = await _run(order, brand=brand)
    assert result["eligible"] is True  # "final sale" isn't in this brand's custom list


# ─── 7. Already refunded ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_already_refunded_order_blocked_at_staging():
    order = _order(total_price="100.00", refunds=[
        {"transactions": [{"amount": "100.00"}]},
    ])
    result = await _run(order, brand={"id": "b1"})
    assert result["eligible"] is False
    assert "already been refunded" in result["reason"]


@pytest.mark.asyncio
async def test_partially_refunded_order_still_eligible():
    order = _order(total_price="100.00", refunds=[
        {"transactions": [{"amount": "40.00"}]},
    ])
    result = await _run(order, brand={"id": "b1"})
    assert result["eligible"] is True


# ─── 8. Already cancelled ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_already_cancelled_order_blocked_at_staging():
    order = _order(cancelled_at=datetime.now(timezone.utc).isoformat())
    result = await _run(order, brand={"id": "b1"})
    assert result["eligible"] is False
    assert "already been cancelled" in result["reason"]


@pytest.mark.asyncio
async def test_already_cancelled_checked_before_fulfillment_status():
    """Cancelled + unfulfilled order should report as cancelled, not as
    'hasn't been delivered yet' — the safety check must run first."""
    order = _order(cancelled_at=datetime.now(timezone.utc).isoformat(), fulfillment_status="unfulfilled")
    result = await _run(order, brand={"id": "b1"})
    assert result["eligible"] is False
    assert "already been cancelled" in result["reason"]


# ─── 9. Sender email vs order email identity check (item 10) ────────────────

@pytest.mark.asyncio
async def test_mismatched_sender_email_is_not_silently_eligible():
    """The order's email is customer@example.com (see _order()'s default);
    someone emailing from a different address requesting a cancel/refund on
    this order must not be treated as eligible."""
    order = _order(days_old=10)  # would otherwise pass every other check
    result = await _run(order, brand={"id": "b1"}, sender_email="attacker@example.com")
    assert result["eligible"] is False
    assert result["reason"] == "sender email does not match order email on file"


@pytest.mark.asyncio
async def test_mismatched_sender_email_still_stages_for_manual_review():
    """Must not vanish silently — detect_and_create() only skips staging
    entirely when neither staging_required nor requires_manual_review is
    set, so both must be true here for the request to reach a human."""
    order = _order(days_old=10)
    result = await _run(order, brand={"id": "b1"}, sender_email="attacker@example.com")
    assert result["staging_required"] is True
    assert result["requires_manual_review"] is True


@pytest.mark.asyncio
async def test_matching_sender_email_is_unaffected():
    """Sanity check the other direction: identical email still proceeds
    through the normal eligibility checks exactly as before."""
    order = _order(days_old=10)
    result = await _run(order, brand={"id": "b1"}, sender_email="customer@example.com")
    assert result["eligible"] is True


@pytest.mark.asyncio
async def test_order_with_no_email_on_file_is_lenient():
    """No email on the order at all means nothing to compare against —
    stays lenient rather than blocking every such order."""
    order = _order(days_old=10)
    order["email"] = ""
    result = await _run(order, brand={"id": "b1"}, sender_email="customer@example.com")
    assert result["eligible"] is True
