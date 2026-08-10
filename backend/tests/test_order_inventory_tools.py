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
        {"title": "M", "sku": "EH-M", "price": "50.00", "inventory_quantity": 5},
    ]}]}
    with patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(return_value=fake)):
        result = run(tools.get_inventory_status("hoodie", shop_domain="test.myshopify.com", access_token="tok"))
    assert result["success"] is True
    assert result["variants"][0]["in_stock"] is True


def test_get_inventory_status_out_of_stock_all_variants():
    tools = V3Tools()
    fake = {"products": [{"title": "Essential Hoodie", "variants": [
        {"title": "M", "sku": "EH-M", "price": "50.00", "inventory_quantity": 0},
        {"title": "L", "sku": "EH-L", "price": "50.00", "inventory_quantity": 0},
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
