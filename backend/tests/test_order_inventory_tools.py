"""
get_orders_by_email() and get_inventory_status() (src/services/tools.py) used
to query local Supabase 'orders'/'order_items'/'products'/'variants' tables
that nothing in the current architecture populates (confirmed: only a dead,
manually-triggered admin sync route writes to them) - so every real brand
got a false "no orders found" / "couldn't find that product" regardless of
what's actually in their Shopify store. Both now delegate to live Shopify
data via ShopifyClient. These tests cover the rewritten behavior: real data
only, no guessing on ambiguous matches, and honest failure when Shopify
can't be reached.
"""
import os
import sys
from unittest.mock import patch, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio
from src.services.tools import V3Tools  # noqa: E402
from src.services.shopify_service import ShopifyClient, ShopifyError  # noqa: E402


class _FakeResp:
    """Minimal stand-in for requests.Response, matching what get_order_status
    reads: .status_code, .json(), .text."""
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── ShopifyClient.find_products_by_title ────────────────────────────────

def _client():
    return ShopifyClient("test-shop.myshopify.com", "shpat_test")


def test_find_products_by_title_single_match():
    client = _client()
    products = [{"id": 1, "title": "Essential Hoodie", "status": "active", "variants": []}]
    with patch.object(client, "_request", return_value={"data": {"products": products}}):
        result = run(client.find_products_by_title("hoodie"))
    assert result["count"] == 1
    assert result["products"][0]["title"] == "Essential Hoodie"


def test_find_products_by_title_ambiguous_multiple_matches():
    client = _client()
    products = [
        {"id": 1, "title": "Essential Hoodie", "status": "active", "variants": []},
        {"id": 2, "title": "Zip-Up Hoodie", "status": "active", "variants": []},
    ]
    with patch.object(client, "_request", return_value={"data": {"products": products}}):
        result = run(client.find_products_by_title("hoodie"))
    assert result["count"] == 2


def test_find_products_by_title_excludes_inactive():
    client = _client()
    products = [{"id": 1, "title": "Old Hoodie", "status": "draft", "variants": []}]
    with patch.object(client, "_request", return_value={"data": {"products": products}}):
        result = run(client.find_products_by_title("hoodie"))
    assert result["count"] == 0


def test_find_products_by_title_excludes_archived():
    """Archived is a distinct Shopify status from draft — both must be treated
    as unavailable, not just draft."""
    client = _client()
    products = [{"id": 1, "title": "Discontinued Hoodie", "status": "archived", "variants": []}]
    with patch.object(client, "_request", return_value={"data": {"products": products}}):
        result = run(client.find_products_by_title("hoodie"))
    assert result["count"] == 0


# ── tools.get_orders_by_email ────────────────────────────────────────────

def test_get_orders_by_email_without_credentials_errors_not_guesses():
    tools = V3Tools()
    result = run(tools.get_orders_by_email("a@b.com"))
    assert "error" in result


def test_get_orders_by_email_multiple_orders_returned_uncollapsed():
    tools = V3Tools()
    fake_result = {"orders": [
        {"order_number": 1001, "fulfillment_status": "fulfilled", "financial_status": "paid", "total_price": "50.00"},
        {"order_number": 1002, "fulfillment_status": None, "financial_status": "paid", "total_price": "20.00"},
    ]}
    with patch.object(ShopifyClient, "find_orders_by_email", new=AsyncMock(return_value=fake_result)):
        result = run(tools.get_orders_by_email("a@b.com", shop_domain="test.myshopify.com", access_token="tok"))
    assert result["success"] is True
    assert result["count"] == 2
    assert result["orders"][1]["status"] == "unfulfilled"


def test_get_orders_by_email_shopify_failure_is_reported_as_error():
    tools = V3Tools()
    with patch.object(ShopifyClient, "find_orders_by_email", new=AsyncMock(side_effect=ShopifyError("down"))):
        result = run(tools.get_orders_by_email("a@b.com", shop_domain="test.myshopify.com", access_token="tok"))
    assert "error" in result


# ── tools.get_inventory_status ───────────────────────────────────────────

def test_get_inventory_status_without_credentials_escalates_not_guesses():
    tools = V3Tools()
    result = run(tools.get_inventory_status("hoodie"))
    assert result["success"] is False


def test_get_inventory_status_in_stock():
    tools = V3Tools()
    fake = {"products": [{"title": "Essential Hoodie", "variants": [
        {"title": "M", "sku": "EH-M", "price": "50.00", "inventory_quantity": 5, "inventory_management": "shopify"},
    ]}]}
    with patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(return_value=fake)):
        result = run(tools.get_inventory_status("hoodie", shop_domain="test.myshopify.com", access_token="tok"))
    assert result["success"] is True
    assert result["variants"][0]["in_stock"] is True


def test_get_inventory_status_out_of_stock_all_variants():
    tools = V3Tools()
    fake = {"products": [{"title": "Essential Hoodie", "variants": [
        {"title": "M", "sku": "EH-M", "price": "50.00", "inventory_quantity": 0, "inventory_management": "shopify"},
        {"title": "L", "sku": "EH-L", "price": "50.00", "inventory_quantity": 0, "inventory_management": "shopify"},
    ]}]}
    with patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(return_value=fake)):
        result = run(tools.get_inventory_status("hoodie", shop_domain="test.myshopify.com", access_token="tok"))
    assert "out of stock" in result["message"].lower()


def test_get_inventory_status_ambiguous_product_asks_not_guesses():
    tools = V3Tools()
    fake = {"products": [
        {"title": "Essential Hoodie", "variants": []},
        {"title": "Zip-Up Hoodie", "variants": []},
    ]}
    with patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(return_value=fake)):
        result = run(tools.get_inventory_status("hoodie", shop_domain="test.myshopify.com", access_token="tok"))
    assert result.get("ambiguous") is True
    assert len(result["matches"]) == 2


def test_get_inventory_status_not_found_is_honest_not_guessed():
    tools = V3Tools()
    with patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(return_value={"products": []})):
        result = run(tools.get_inventory_status("nonexistent-thing", shop_domain="test.myshopify.com", access_token="tok"))
    assert result["success"] is False
    assert "couldn't find" in result["message"].lower()


def test_get_inventory_status_mixed_variants_reports_each_accurately():
    """Product exists but the specific variant a customer wants may not be
    the one in stock — each variant's in_stock flag must be independently
    accurate, not collapsed into one product-level yes/no."""
    tools = V3Tools()
    fake = {"products": [{"title": "Essential Hoodie", "variants": [
        {"title": "S", "sku": "EH-S", "price": "50.00", "inventory_quantity": 0, "inventory_management": "shopify"},
        {"title": "M", "sku": "EH-M", "price": "50.00", "inventory_quantity": 3, "inventory_management": "shopify"},
    ]}]}
    with patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(return_value=fake)):
        result = run(tools.get_inventory_status("hoodie", shop_domain="test.myshopify.com", access_token="tok"))
    assert result["success"] is True
    variants_by_size = {v["size"]: v["in_stock"] for v in result["variants"]}
    assert variants_by_size == {"S": False, "M": True}
    assert "in stock" in result["message"].lower()


def test_get_inventory_status_untracked_inventory_not_reported_as_out_of_stock():
    """Shopify variants with inventory_management=None aren't stock-tracked
    (the product keeps selling regardless of quantity) — treating a stale/zero
    quantity as authoritative here would be inventing an "out of stock" claim
    that isn't true."""
    tools = V3Tools()
    fake = {"products": [{"title": "Digital Gift Card", "variants": [
        {"title": "Default Title", "sku": "GIFTCARD", "price": "25.00",
         "inventory_quantity": 0, "inventory_management": None},
    ]}]}
    with patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(return_value=fake)):
        result = run(tools.get_inventory_status("gift card", shop_domain="test.myshopify.com", access_token="tok"))
    assert result["success"] is True
    assert result["variants"][0]["in_stock"] is True
    assert "out of stock" not in result["message"].lower()


def test_get_inventory_status_shopify_failure_escalates():
    tools = V3Tools()
    with patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(side_effect=ShopifyError("down"))):
        result = run(tools.get_inventory_status("hoodie", shop_domain="test.myshopify.com", access_token="tok"))
    assert result["success"] is False


# ── tools.get_order_status — tracking requirements from the original task ──
#
# Coverage map for the 5 required tracking scenarios:
#   1. valid shipped order with real tracking -> test_get_order_status_shipped_order_with_real_tracking
#   2. unshipped order                        -> test_get_order_status_unshipped_order_has_no_tracking
#   3. shipped order, missing tracking number -> test_get_order_status_shipped_order_missing_tracking_number
#   4. multiple/ambiguous possible orders     -> test_get_orders_by_email_multiple_orders_returned_uncollapsed (above)
#   5. Shopify API failure                    -> test_get_order_status_shopify_api_failure_returns_error_not_crash
#                                                 (and test_get_orders_by_email_shopify_failure_is_reported_as_error above)

def _order_payload(**overrides):
    base = {
        "order_number": 1001,
        "fulfillment_status": None,
        "financial_status": "paid",
        "cancelled_at": None,
        "total_price": "50.00",
        "line_items": [],
        "created_at": "2026-01-01T00:00:00Z",
        "fulfillments": [],
    }
    base.update(overrides)
    return {"orders": [base]}


def test_get_order_status_shipped_order_with_real_tracking():
    tools = V3Tools()
    payload = _order_payload(fulfillment_status="fulfilled", fulfillments=[{
        "tracking_number": "1Z999AA10123456784",
        "tracking_url": "https://track.example/1Z999AA10123456784",
        "tracking_company": "UPS",
        "shipment_status": "in_transit",
        "created_at": "2026-01-02T00:00:00Z",
    }])
    with patch("src.services.tools.requests.get", return_value=_FakeResp(200, payload)):
        result = run(tools.get_order_status("1001", shop_domain="test.myshopify.com", access_token="tok"))
    assert result["success"] is True
    assert result["status"] == "fulfilled"
    assert result["tracking_number"] == "1Z999AA10123456784"
    assert result["tracking_company"] == "UPS"


def test_get_order_status_unshipped_order_has_no_tracking():
    tools = V3Tools()
    payload = _order_payload(fulfillment_status=None, fulfillments=[])
    with patch("src.services.tools.requests.get", return_value=_FakeResp(200, payload)):
        result = run(tools.get_order_status("1002", shop_domain="test.myshopify.com", access_token="tok"))
    assert result["success"] is True
    assert result["status"] == "unfulfilled"
    assert result["tracking_number"] is None


def test_get_order_status_shipped_order_missing_tracking_number():
    tools = V3Tools()
    payload = _order_payload(fulfillment_status="fulfilled", fulfillments=[{
        "tracking_number": None,
        "tracking_url": None,
        "tracking_company": None,
        "shipment_status": None,
        "created_at": "2026-01-02T00:00:00Z",
    }])
    with patch("src.services.tools.requests.get", return_value=_FakeResp(200, payload)):
        result = run(tools.get_order_status("1003", shop_domain="test.myshopify.com", access_token="tok"))
    assert result["success"] is True
    assert result["status"] == "fulfilled"
    assert result["tracking_number"] is None  # never invented when Shopify has none


def test_get_order_status_shopify_api_failure_returns_error_not_crash():
    tools = V3Tools()
    with patch("src.services.tools.requests.get", side_effect=Exception("connection refused")):
        result = run(tools.get_order_status("1004", shop_domain="test.myshopify.com", access_token="tok"))
    assert "error" in result
    assert "success" not in result


# ── tools.get_order_status — additional cases from the order-tracking hardening task ──
#
# Coverage map:
#   order does not exist          -> test_get_order_status_nonexistent_order_returns_error_not_invented
#   cancelled order                -> test_get_order_status_cancelled_order_reports_cancelled
#   partially fulfilled order      -> test_get_order_status_partially_fulfilled_order
#   multiple shipments/fulfillments-> test_get_order_status_multiple_fulfillments_preserves_every_shipment
#   ownership: matching email      -> test_get_order_status_customer_email_matches_owner_returns_data
#   ownership: wrong customer      -> test_get_order_status_customer_email_mismatch_blocks_access
#   ownership: no email (anon)     -> test_get_order_status_no_customer_email_blocks_access
#   ownership: check opt-out       -> test_get_order_status_customer_email_none_preserves_backward_compat

def test_get_order_status_nonexistent_order_returns_error_not_invented():
    tools = V3Tools()
    empty = {"orders": []}
    with patch("src.services.tools.requests.get", return_value=_FakeResp(200, empty)):
        result = run(tools.get_order_status("999999", shop_domain="test.myshopify.com", access_token="tok"))
    assert "error" in result
    assert "success" not in result


def test_get_order_status_cancelled_order_reports_cancelled():
    tools = V3Tools()
    payload = _order_payload(fulfillment_status=None, cancelled_at="2026-01-03T00:00:00Z")
    with patch("src.services.tools.requests.get", return_value=_FakeResp(200, payload)):
        result = run(tools.get_order_status("1005", shop_domain="test.myshopify.com", access_token="tok"))
    assert result["success"] is True
    assert result["cancelled_at"] == "2026-01-03T00:00:00Z"


def test_get_order_status_partially_fulfilled_order():
    tools = V3Tools()
    payload = _order_payload(fulfillment_status="partial", fulfillments=[{
        "tracking_number": "TRACK-PARTIAL-1", "tracking_url": None,
        "tracking_company": "USPS", "shipment_status": "in_transit",
        "created_at": "2026-01-02T00:00:00Z",
    }])
    with patch("src.services.tools.requests.get", return_value=_FakeResp(200, payload)):
        result = run(tools.get_order_status("1006", shop_domain="test.myshopify.com", access_token="tok"))
    assert result["success"] is True
    assert result["status"] == "partial"


def test_get_order_status_multiple_fulfillments_preserves_every_shipment():
    """Corrected behavior: a multi-shipment order must not lose real shipment
    data. The top-level tracking_* fields still mirror the first fulfillment
    for backward compatibility with single-shipment callers, but the full,
    distinct list of shipments must also be present — never collapsed."""
    tools = V3Tools()
    payload = _order_payload(fulfillment_status="fulfilled", fulfillments=[
        {"tracking_number": "FIRST-SHIPMENT", "tracking_url": None,
         "tracking_company": "UPS", "shipment_status": "delivered", "created_at": "2026-01-02T00:00:00Z"},
        {"tracking_number": "SECOND-SHIPMENT", "tracking_url": None,
         "tracking_company": "FedEx", "shipment_status": "in_transit", "created_at": "2026-01-03T00:00:00Z"},
    ])
    with patch("src.services.tools.requests.get", return_value=_FakeResp(200, payload)):
        result = run(tools.get_order_status("1007", shop_domain="test.myshopify.com", access_token="tok"))
    assert result["success"] is True
    # Backward-compatible single-shipment fields mirror the first fulfillment.
    assert result["tracking_number"] == "FIRST-SHIPMENT"
    assert result["tracking_company"] == "UPS"
    # Nothing is discarded: both real shipments are preserved distinctly.
    assert result["fulfillment_count"] == 2
    assert [f["tracking_number"] for f in result["fulfillments"]] == ["FIRST-SHIPMENT", "SECOND-SHIPMENT"]
    assert [f["tracking_company"] for f in result["fulfillments"]] == ["UPS", "FedEx"]
    assert [f["shipment_status"] for f in result["fulfillments"]] == ["delivered", "in_transit"]


def test_get_order_status_single_fulfillment_reports_count_one():
    tools = V3Tools()
    payload = _order_payload(fulfillment_status="fulfilled", fulfillments=[{
        "tracking_number": "ONLY-SHIPMENT", "tracking_url": None,
        "tracking_company": "UPS", "shipment_status": "in_transit", "created_at": "2026-01-02T00:00:00Z",
    }])
    with patch("src.services.tools.requests.get", return_value=_FakeResp(200, payload)):
        result = run(tools.get_order_status("1009", shop_domain="test.myshopify.com", access_token="tok"))
    assert result["fulfillment_count"] == 1
    assert result["fulfillments"][0]["tracking_number"] == "ONLY-SHIPMENT"


def test_get_order_status_no_fulfillments_reports_count_zero():
    tools = V3Tools()
    payload = _order_payload(fulfillment_status=None, fulfillments=[])
    with patch("src.services.tools.requests.get", return_value=_FakeResp(200, payload)):
        result = run(tools.get_order_status("1010", shop_domain="test.myshopify.com", access_token="tok"))
    assert result["fulfillment_count"] == 0
    assert result["fulfillments"] == []


def test_get_order_status_customer_email_matches_owner_returns_data():
    tools = V3Tools()
    payload = _order_payload(fulfillment_status="fulfilled", email="owner@example.com")
    with patch("src.services.tools.requests.get", return_value=_FakeResp(200, payload)):
        result = run(tools.get_order_status(
            "1008", shop_domain="test.myshopify.com", access_token="tok",
            customer_email="Owner@Example.com",  # case-insensitive match
        ))
    assert result["success"] is True
    assert result["order_number"] == 1001


def test_get_order_status_customer_email_mismatch_blocks_access():
    """Security requirement: a valid order number alone must never return
    another customer's order data."""
    tools = V3Tools()
    payload = _order_payload(fulfillment_status="fulfilled", email="owner@example.com")
    with patch("src.services.tools.requests.get", return_value=_FakeResp(200, payload)):
        result = run(tools.get_order_status(
            "1008", shop_domain="test.myshopify.com", access_token="tok",
            customer_email="attacker@example.com",
        ))
    assert "error" in result
    assert "success" not in result


def test_get_order_status_no_customer_email_blocks_access():
    """An unverified/anonymous requester (e.g. chat widget visitor who hasn't
    given an email) passes customer_email="" — must not get order data back
    just by guessing a valid order number."""
    tools = V3Tools()
    payload = _order_payload(fulfillment_status="fulfilled", email="owner@example.com")
    with patch("src.services.tools.requests.get", return_value=_FakeResp(200, payload)):
        result = run(tools.get_order_status(
            "1008", shop_domain="test.myshopify.com", access_token="tok",
            customer_email="",
        ))
    assert "error" in result
    assert "success" not in result


def test_get_order_status_no_email_yet_is_flagged_distinctly_from_a_mismatched_one():
    """Root cause of the 'I can't pull up your order' production bug:
    ownership_mismatch used to be bool(provided_email), so a chat visitor
    who's given an order number but no email at all yet (the single most
    common case) got ownership_mismatch=False - indistinguishable from a
    genuine Shopify lookup failure to callers. It must now be True either
    way (the order WAS found), with email_provided distinguishing "no email
    given yet" from "a mismatched email was given" so callers can phrase
    each honestly."""
    tools = V3Tools()
    payload = _order_payload(fulfillment_status="fulfilled", email="owner@example.com")
    with patch("src.services.tools.requests.get", return_value=_FakeResp(200, payload)):
        no_email_result = run(tools.get_order_status(
            "1008", shop_domain="test.myshopify.com", access_token="tok", customer_email="",
        ))
    assert no_email_result["ownership_mismatch"] is True
    assert no_email_result["email_provided"] is False

    with patch("src.services.tools.requests.get", return_value=_FakeResp(200, payload)):
        mismatch_result = run(tools.get_order_status(
            "1008", shop_domain="test.myshopify.com", access_token="tok", customer_email="wrong@example.com",
        ))
    assert mismatch_result["ownership_mismatch"] is True
    assert mismatch_result["email_provided"] is True


def test_get_order_status_cancelled_order_with_matching_email_returns_full_data():
    """Shopify Order Context bug: connected Shopify + valid order number +
    cancelled order + a verified/matching identity must return the real
    cancellation data - not be blocked by the ownership check, which only
    guards a MISMATCHED or unverified identity."""
    tools = V3Tools()
    payload = _order_payload(fulfillment_status="unfulfilled", cancelled_at="2026-08-22T06:23:54Z", email="owner@example.com")
    with patch("src.services.tools.requests.get", return_value=_FakeResp(200, payload)):
        result = run(tools.get_order_status(
            "1013", shop_domain="test.myshopify.com", access_token="tok",
            customer_email="owner@example.com",
        ))
    assert result["success"] is True
    assert result["cancelled_at"] == "2026-08-22T06:23:54Z"
    assert "ownership_mismatch" not in result


def test_get_order_status_ownership_mismatch_is_flagged_distinctly_from_not_found():
    """The order genuinely exists (found in Shopify) but the requester's
    email doesn't match it - callers need to tell these two cases apart to
    give an honest answer without ever disclosing the real order's data."""
    tools = V3Tools()
    payload = _order_payload(fulfillment_status="fulfilled", email="owner@example.com")
    with patch("src.services.tools.requests.get", return_value=_FakeResp(200, payload)):
        mismatch_result = run(tools.get_order_status(
            "1008", shop_domain="test.myshopify.com", access_token="tok",
            customer_email="someone-else@example.com",
        ))
    assert mismatch_result["ownership_mismatch"] is True
    assert "success" not in mismatch_result

    with patch("src.services.tools.requests.get", return_value=_FakeResp(200, {"orders": []})):
        not_found_result = run(tools.get_order_status(
            "9999", shop_domain="test.myshopify.com", access_token="tok",
            customer_email="someone-else@example.com",
        ))
    assert "ownership_mismatch" not in not_found_result


def test_get_order_status_customer_email_none_preserves_backward_compat():
    """customer_email=None (the default) means the caller isn't verifying
    ownership at all — used by callers that don't have a customer identity
    to check against. Documents the opt-in nature of the check."""
    tools = V3Tools()
    payload = _order_payload(fulfillment_status="fulfilled", email="owner@example.com")
    with patch("src.services.tools.requests.get", return_value=_FakeResp(200, payload)):
        result = run(tools.get_order_status("1008", shop_domain="test.myshopify.com", access_token="tok"))
    assert result["success"] is True
