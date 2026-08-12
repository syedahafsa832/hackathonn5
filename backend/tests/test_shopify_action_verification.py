"""
Shopify refund/cancel execution must verify Shopify's response, not just its
HTTP status, before reporting success (workflow verification: Refunds,
Cancellations).

process_refund() and cancel_order() in shopify_service.py previously treated
any 200/201 response as full success. Two real gaps this closes:

1. Shopify's POST /refunds.json can return 201 (the refund RECORD was
   created) while the nested payment-gateway transaction inside it reports
   status "failure"/"error" (e.g. an expired card) - money never actually
   moved. The old code never inspected the transactions array, so a gateway
   failure would still be reported to the customer as "Successfully
   refunded $X.XX".
2. Same class of gap for cancel_order(): a response without a populated
   cancelled_at was still treated as a successful cancellation.

Both are fixed by raising ShopifyError when Shopify's own response doesn't
corroborate success - both actions_service.py's approve_action and
v2_tickets.py's execute_refund/execute_cancel_order already treat any
ShopifyError as a failure (existing, unmodified code), so this fix alone
protects both call sites without touching either of them.

All Shopify HTTP calls are mocked via ShopifyClient._request - no live
service required.
"""
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services.shopify_service import ShopifyClient, ShopifyError  # noqa: E402


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _client():
    return ShopifyClient("test-shop.myshopify.com", "shpat_test")


def _order_response(**overrides):
    order = {
        "id": 123456789, "name": "#1001", "total_price": "50.00",
        "cancelled_at": None, "refunds": [], "fulfillment_status": None,
        "gateway": "manual",
    }
    order.update(overrides)
    return {"data": {"orders": [order]}}


# ── process_refund: gateway transaction verification ────────────────────────

def test_refund_with_successful_transaction_reports_success():
    client = _client()
    order_resp = _order_response()
    txn_resp = {"data": {"transactions": [{"id": 1, "kind": "sale", "status": "success"}]}}
    refund_resp = {"data": {"refund": {
        "id": 999, "transactions": [{"id": 2, "kind": "refund", "status": "success"}],
    }}}

    with patch.object(client, "_request", side_effect=[order_resp, txn_resp, refund_resp]):
        result = run(client.process_refund("1001"))

    assert result["success"] is True
    assert result["refund_id"] == 999


def test_refund_with_gateway_failure_transaction_is_not_reported_as_success():
    """The core case this fix exists for: Shopify creates the refund record
    (would be a 201 at the HTTP layer) but the actual money movement failed
    at the payment gateway."""
    client = _client()
    order_resp = _order_response()
    txn_resp = {"data": {"transactions": [{"id": 1, "kind": "sale", "status": "success"}]}}
    refund_resp = {"data": {"refund": {
        "id": 999, "transactions": [{"id": 2, "kind": "refund", "status": "failure"}],
    }}}

    with patch.object(client, "_request", side_effect=[order_resp, txn_resp, refund_resp]):
        with pytest.raises(ShopifyError):
            run(client.process_refund("1001"))


def test_refund_with_no_transactions_in_response_is_not_reported_as_success():
    """A parent transaction was found (so a real money refund was attempted)
    but Shopify's response came back with no transactions at all - never
    assume success from an empty/ambiguous response."""
    client = _client()
    order_resp = _order_response()
    txn_resp = {"data": {"transactions": [{"id": 1, "kind": "sale", "status": "success"}]}}
    refund_resp = {"data": {"refund": {"id": 999, "transactions": []}}}

    with patch.object(client, "_request", side_effect=[order_resp, txn_resp, refund_resp]):
        with pytest.raises(ShopifyError):
            run(client.process_refund("1001"))


def test_refund_already_cancelled_order_fails_before_any_refund_call():
    client = _client()
    order_resp = _order_response(cancelled_at="2026-01-01T00:00:00Z")

    with patch.object(client, "_request", return_value=order_resp):
        with pytest.raises(ShopifyError):
            run(client.process_refund("1001"))


# ── cancel_order: response corroboration ─────────────────────────────────────

def test_cancel_order_with_populated_cancelled_at_reports_success():
    client = _client()
    order_resp = _order_response()
    cancel_resp = {"data": {"order": {"id": 123456789, "cancelled_at": "2026-01-02T00:00:00Z"}}}

    with patch.object(client, "_request", side_effect=[order_resp, cancel_resp]):
        result = run(client.cancel_order("1001"))

    assert result["success"] is True
    assert result["cancelled_at"] == "2026-01-02T00:00:00Z"


def test_cancel_order_with_no_cancelled_at_in_response_is_not_reported_as_success():
    client = _client()
    order_resp = _order_response()
    # Shopify accepted the request but the response doesn't corroborate a
    # real cancellation - must not be reported as one.
    cancel_resp = {"data": {"order": {"id": 123456789, "cancelled_at": None}}}

    with patch.object(client, "_request", side_effect=[order_resp, cancel_resp]):
        with pytest.raises(ShopifyError):
            run(client.cancel_order("1001"))


def test_cancel_order_already_fulfilled_order_fails_before_any_cancel_call():
    client = _client()
    order_resp = _order_response(fulfillment_status="fulfilled")

    with patch.object(client, "_request", return_value=order_resp):
        with pytest.raises(ShopifyError):
            run(client.cancel_order("1001"))
