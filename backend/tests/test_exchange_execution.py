"""
Exchange execution layer — direct unit tests for the three pieces that sit
below return_actions_integration.py's staging logic (covered separately in
test_exchange_workflow.py):

1. actions_manager.find_exchange_target() — every LIVE-Shopify-grounded
   resolution branch (not found / out of stock / variant missing / ambiguous
   / found).
2. shopify_service.ShopifyClient.create_exchange_draft_order() — the real
   Shopify mutation (draft order + complete, or draft order + invoice).
3. actions_service.approve_action()'s EXCHANGE branch — idempotency-key
   replay and financial audit recording, same guarantees refund/cancel
   already have.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services.actions_manager import ActionsManager  # noqa: E402
from src.services.shopify_service import ShopifyClient, ShopifyError  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ══════════════════════════════════════════════════════════════════════════
# 1. find_exchange_target()
# ══════════════════════════════════════════════════════════════════════════

def _hoodie_product(**overrides):
    product = {
        "id": 555, "title": "Essential Hoodie", "handle": "essential-hoodie",
        "options": [{"name": "Size"}],
        "variants": [
            {"id": 9001, "title": "M", "option1": "M", "price": "45.00", "inventory_management": "shopify", "inventory_quantity": 5},
            {"id": 9002, "title": "L", "option1": "L", "price": "45.00", "inventory_management": "shopify", "inventory_quantity": 3},
            {"id": 9003, "title": "XL", "option1": "XL", "price": "45.00", "inventory_management": "shopify", "inventory_quantity": 0},
        ],
    }
    product.update(overrides)
    return product


def _mgr_with_client(get_product_result=None, find_products_result=None):
    mgr = ActionsManager()
    client = MagicMock()
    client.shop_domain = "test-shop.myshopify.com"
    client.get_product_by_id = AsyncMock(return_value=get_product_result)
    client.find_products_by_title = AsyncMock(return_value=find_products_result or {"products": []})
    return mgr, client


def _run_find(mgr, client, original_item, target):
    with patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock(return_value=client)):
        return run(mgr.find_exchange_target("tenant-1", original_item, target))


def test_no_tenant_id_returns_no_shopify_connection():
    mgr = ActionsManager()
    result = run(mgr.find_exchange_target(None, {"product_id": 555}, "L"))
    assert result == {"found": False, "reason": "no_shopify_connection"}


def test_blank_target_returns_target_not_specified():
    mgr, client = _mgr_with_client()
    result = _run_find(mgr, client, {"product_id": 555}, "   ")
    assert result["reason"] == "target_not_specified"


def test_product_no_longer_available_returns_product_unavailable():
    mgr, client = _mgr_with_client(get_product_result=None)
    result = _run_find(mgr, client, {"product_id": 555, "variant_title": "M"}, "size L")
    assert result["reason"] == "product_unavailable"


def test_requested_size_not_offered_returns_variant_not_found_with_options():
    mgr, client = _mgr_with_client(get_product_result=_hoodie_product())
    result = _run_find(mgr, client, {"product_id": 555, "variant_title": "M"}, "size XXL")
    assert result["reason"] == "variant_not_found"
    assert set(result["available_options"]) == {"M", "L", "XL"}


def test_requested_size_out_of_stock_returns_out_of_stock():
    mgr, client = _mgr_with_client(get_product_result=_hoodie_product())
    result = _run_find(mgr, client, {"product_id": 555, "variant_title": "M"}, "size XL")
    assert result["reason"] == "out_of_stock"
    assert result["variant_title"] == "XL"


def test_requested_size_in_stock_is_found():
    mgr, client = _mgr_with_client(get_product_result=_hoodie_product())
    result = _run_find(mgr, client, {"product_id": 555, "variant_title": "M"}, "size L")
    assert result == {
        "found": True, "same_product": True, "product_id": 555, "product_title": "Essential Hoodie",
        "variant_id": 9002, "variant_title": "L", "price": 45.0,
        "product_url": "https://test-shop.myshopify.com/products/essential-hoodie",
    }


def test_color_request_keeps_original_size_via_untouched_attribute_match():
    product = _hoodie_product(
        options=[{"name": "Size"}, {"name": "Color"}],
        variants=[
            {"id": 1, "title": "M / Black", "option1": "M", "option2": "Black", "price": "45.00", "inventory_management": "shopify", "inventory_quantity": 5},
            {"id": 2, "title": "M / White", "option1": "M", "option2": "White", "price": "45.00", "inventory_management": "shopify", "inventory_quantity": 5},
            {"id": 3, "title": "L / White", "option1": "L", "option2": "White", "price": "45.00", "inventory_management": "shopify", "inventory_quantity": 5},
        ],
    )
    mgr, client = _mgr_with_client(get_product_result=product)
    original = {"product_id": 555, "variant_title": "M / Black", "variant_id": 1}
    result = _run_find(mgr, client, original, "black please")
    assert result["found"] is True
    assert result["variant_id"] == 1  # matches the same size (M) it already had


def test_different_product_single_match_is_found():
    product = {"id": 700, "title": "Canvas Tote Bag", "handle": "canvas-tote", "options": [{"name": "Title"}],
               "variants": [{"id": 7001, "title": "Default Title", "price": "15.00", "inventory_management": "shopify", "inventory_quantity": 10}]}
    mgr, client = _mgr_with_client(find_products_result={"products": [product]})
    result = _run_find(mgr, client, {"product_id": 555, "variant_title": "M"}, "canvas tote")
    assert result["found"] is True
    assert result["same_product"] is False
    assert result["product_title"] == "Canvas Tote Bag"
    assert result["variant_id"] == 7001


def test_different_product_ambiguous_matches_asks_not_guesses():
    products = [{"id": 1, "title": "Essential Hoodie V2"}, {"id": 2, "title": "Premium Hoodie"}]
    mgr, client = _mgr_with_client(find_products_result={"products": products})
    result = _run_find(mgr, client, {"product_id": 555, "variant_title": "M"}, "hoodie")
    assert result["reason"] == "ambiguous"
    assert result["matches"] == ["Essential Hoodie V2", "Premium Hoodie"]


def test_different_product_not_found_at_all():
    mgr, client = _mgr_with_client(find_products_result={"products": []})
    result = _run_find(mgr, client, {"product_id": 555, "variant_title": "M"}, "leather jacket")
    assert result["reason"] == "target_not_found"
    assert result["query"] == "leather jacket"


def test_different_product_with_unnamed_variant_asks_rather_than_guesses():
    product = {"id": 800, "title": "Premium Hoodie", "handle": "premium-hoodie", "options": [{"name": "Size"}],
               "variants": [
                   {"id": 1, "title": "M", "option1": "M", "price": "65.00", "inventory_management": "shopify", "inventory_quantity": 5},
                   {"id": 2, "title": "L", "option1": "L", "price": "65.00", "inventory_management": "shopify", "inventory_quantity": 5},
               ]}
    mgr, client = _mgr_with_client(find_products_result={"products": [product]})
    result = _run_find(mgr, client, {"product_id": 555, "variant_title": "M"}, "premium hoodie")
    assert result["reason"] == "variant_not_found"


# ══════════════════════════════════════════════════════════════════════════
# 2. ShopifyClient.create_exchange_draft_order()
# ══════════════════════════════════════════════════════════════════════════

def _shopify_client():
    return ShopifyClient("test-shop.myshopify.com", "shpat_test")


def test_negative_price_difference_never_reaches_shopify():
    client = _shopify_client()
    with patch.object(client, "_request") as mock_request:
        with pytest.raises(ShopifyError):
            run(client.create_exchange_draft_order("c@example.com", 9002, 1, -10.0, "#1001"))
    mock_request.assert_not_called()


def test_zero_difference_applies_full_discount_and_completes():
    client = _shopify_client()
    draft_resp = {"data": {"draft_order": {"id": 555, "name": "#D1001"}}}
    complete_resp = {"data": {"draft_order": {"status": "completed", "order_id": 999}}}
    with patch.object(client, "_request", side_effect=[draft_resp, complete_resp]) as mock_request:
        result = run(client.create_exchange_draft_order("c@example.com", 9002, 1, 0.0, "#1001"))

    assert result["success"] is True
    assert result["completed"] is True
    assert result["replacement_order_id"] == 999
    assert result["balance_due"] == 0.0
    draft_call = mock_request.call_args_list[0]
    assert draft_call.args[1] == "draft_orders.json"
    assert draft_call.args[2]["draft_order"]["applied_discount"]["value"] == "100.0"


def test_missing_draft_order_id_raises():
    client = _shopify_client()
    with patch.object(client, "_request", return_value={"data": {"draft_order": {}}}):
        with pytest.raises(ShopifyError):
            run(client.create_exchange_draft_order("c@example.com", 9002, 1, 0.0, "#1001"))


def test_completion_not_confirmed_raises_never_reported_as_success():
    client = _shopify_client()
    draft_resp = {"data": {"draft_order": {"id": 555, "name": "#D1001"}}}
    complete_resp = {"data": {"draft_order": {"status": "open", "order_id": None}}}
    with patch.object(client, "_request", side_effect=[draft_resp, complete_resp]):
        with pytest.raises(ShopifyError):
            run(client.create_exchange_draft_order("c@example.com", 9002, 1, 0.0, "#1001"))


def test_positive_difference_creates_draft_and_sends_real_invoice():
    client = _shopify_client()
    draft_resp = {"data": {"draft_order": {"id": 555, "name": "#D1001", "invoice_url": "https://checkout/pay"}}}
    invoice_resp = {"data": {}}
    with patch.object(client, "_request", side_effect=[draft_resp, invoice_resp]) as mock_request:
        result = run(client.create_exchange_draft_order("c@example.com", 9002, 1, 20.0, "#1001"))

    assert result["success"] is True
    assert result["completed"] is False
    assert result["invoice_sent"] is True
    assert result["balance_due"] == 20.0
    draft_call = mock_request.call_args_list[0]
    assert "applied_discount" not in draft_call.args[2]["draft_order"]


def test_positive_difference_invoice_send_failure_does_not_fail_the_whole_operation():
    client = _shopify_client()
    draft_resp = {"data": {"draft_order": {"id": 555, "name": "#D1001"}}}
    with patch.object(client, "_request", side_effect=[draft_resp, ShopifyError("mail down")]):
        result = run(client.create_exchange_draft_order("c@example.com", 9002, 1, 20.0, "#1001"))

    assert result["success"] is True
    assert result["invoice_sent"] is False
    assert result["draft_order_id"] == 555


# ══════════════════════════════════════════════════════════════════════════
# 3. actions_service.approve_action()'s EXCHANGE branch
# ══════════════════════════════════════════════════════════════════════════

from src.services.actions_service import actions_service  # noqa: E402


def _exchange_action(price_difference=0.0, **overrides):
    action = {
        "id": "action-1", "tenant_id": "tenant-1", "brand_id": "brand-1",
        "ticket_id": "ticket-1", "status": "pending", "action_type": "exchange",
        "order_id": "1001", "customer_email": "c@example.com", "customer_name": "Jane",
        "extracted_data": {
            "target": {"variant_id": 9002, "variant_title": "L", "product_title": "Essential Hoodie"},
            "original_item": {"quantity": 1},
            "price_difference": price_difference,
        },
    }
    action.update(overrides)
    return action


@pytest.mark.asyncio
async def test_approve_exchange_zero_difference_creates_and_completes_draft_order():
    action = _exchange_action(price_difference=0.0)
    with patch("src.services.actions_service.supabase_select", return_value=[action]), \
         patch("src.services.actions_service.supabase_update", return_value=[action]), \
         patch("src.services.financial_audit.supabase_select", return_value=[]), \
         patch("src.services.financial_audit.supabase_insert", return_value={}), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock()) as mock_getter:
        mock_client = MagicMock()
        mock_client.create_exchange_draft_order = AsyncMock(return_value={
            "success": True, "completed": True, "invoice_sent": False,
            "draft_order_id": 555, "balance_due": 0.0, "message": "done",
        })
        mock_getter.return_value = mock_client

        result = await actions_service.approve_action(
            tenant_id="tenant-1", action_id="action-1", approved_by="staff@example.com",
        )

    assert result["success"] is True
    mock_client.create_exchange_draft_order.assert_awaited_once()
    _, kwargs = mock_client.create_exchange_draft_order.call_args
    assert kwargs["variant_id"] == 9002
    assert kwargs["price_difference"] == 0.0


@pytest.mark.asyncio
async def test_approve_exchange_negative_difference_never_calls_shopify_and_flags_manual_review():
    action = _exchange_action(price_difference=-10.0)
    with patch("src.services.actions_service.supabase_select", return_value=[action]), \
         patch("src.services.actions_service.supabase_update", return_value=[action]), \
         patch("src.services.financial_audit.supabase_select", return_value=[]), \
         patch("src.services.financial_audit.supabase_insert", return_value={}), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock()) as mock_getter:
        mock_client = MagicMock()
        mock_client.create_exchange_draft_order = AsyncMock()
        mock_getter.return_value = mock_client

        result = await actions_service.approve_action(
            tenant_id="tenant-1", action_id="action-1", approved_by="staff@example.com",
        )

    mock_client.create_exchange_draft_order.assert_not_called()
    assert result["success"] is True
    assert result["execution_result"].get("manual_action_required") is True


@pytest.mark.asyncio
async def test_approve_exchange_with_no_resolved_target_fails_safely():
    action = _exchange_action(price_difference=0.0)
    action["extracted_data"]["target"] = {}
    with patch("src.services.actions_service.supabase_select", return_value=[action]), \
         patch("src.services.actions_service.supabase_update", return_value=[action]), \
         patch("src.services.financial_audit.supabase_select", return_value=[]), \
         patch("src.services.financial_audit.supabase_insert", return_value={}), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock()) as mock_getter:
        mock_client = MagicMock()
        mock_client.create_exchange_draft_order = AsyncMock()
        mock_getter.return_value = mock_client

        result = await actions_service.approve_action(
            tenant_id="tenant-1", action_id="action-1", approved_by="staff@example.com",
        )

    mock_client.create_exchange_draft_order.assert_not_called()
    assert result["success"] is False


@pytest.mark.asyncio
async def test_approve_exchange_idempotency_key_replay_does_not_create_a_second_draft_order():
    action = _exchange_action(price_difference=0.0)
    audit_rows = []
    with patch("src.services.actions_service.supabase_select", return_value=[action]), \
         patch("src.services.actions_service.supabase_update", return_value=[action]), \
         patch("src.services.financial_audit.supabase_select", side_effect=lambda t, p=None: audit_rows), \
         patch("src.services.financial_audit.supabase_insert", side_effect=lambda t, data: (audit_rows.append(data), data)[1]), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock()) as mock_getter:
        mock_client = MagicMock()
        mock_client.create_exchange_draft_order = AsyncMock(return_value={
            "success": True, "completed": True, "invoice_sent": False,
            "draft_order_id": 555, "balance_due": 0.0, "message": "done",
        })
        mock_getter.return_value = mock_client

        first = await actions_service.approve_action(
            tenant_id="tenant-1", action_id="action-1", approved_by="staff@example.com",
            idempotency_key="retry-key-exchange",
        )
        second = await actions_service.approve_action(
            tenant_id="tenant-1", action_id="action-1", approved_by="staff@example.com",
            idempotency_key="retry-key-exchange",
        )

    assert second == first
    assert mock_client.create_exchange_draft_order.call_count == 1


@pytest.mark.asyncio
async def test_approve_exchange_failure_is_recorded_in_financial_audit_log():
    action = _exchange_action(price_difference=20.0)
    audit_rows = []

    def fake_audit_insert(table, data):
        audit_rows.append(data)
        return data

    with patch("src.services.actions_service.supabase_select", return_value=[action]), \
         patch("src.services.actions_service.supabase_update", return_value=[action]), \
         patch("src.services.financial_audit.supabase_select", return_value=[]), \
         patch("src.services.financial_audit.supabase_insert", side_effect=fake_audit_insert), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock()) as mock_getter:
        mock_client = MagicMock()
        mock_client.create_exchange_draft_order = AsyncMock(side_effect=ShopifyError("Shopify unavailable"))
        mock_getter.return_value = mock_client

        result = await actions_service.approve_action(
            tenant_id="tenant-1", action_id="action-1", approved_by="staff@example.com",
        )

    assert result["success"] is False
    assert len(audit_rows) == 1
    assert audit_rows[0]["status"] == "failed"
    assert audit_rows[0]["action_type"] == "exchange"
