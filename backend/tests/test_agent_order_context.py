"""
_build_order_context (customer_success_agent.py) turns a get_order_status()
result into the prompt block the LLM is told to use verbatim. The original
implementation only ever looked at the first Shopify fulfillment - an order
that shipped in two boxes with two different tracking numbers would silently
lose the second one. tools.py now returns the full `fulfillments` list
(see test_get_order_status_multiple_fulfillments_preserves_every_shipment in
test_order_inventory_tools.py); these tests cover that _build_order_context
actually surfaces every real shipment distinctly instead of collapsing them.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.agent.customer_success_agent import _build_order_context  # noqa: E402


def _base_order(**overrides):
    order = {
        "success": True,
        "order_number": 1001,
        "status": "fulfilled",
        "financial_status": "paid",
        "cancelled_at": None,
        "total_amount": "50.00",
        "items": [{"title": "Hoodie", "quantity": 1, "price": "50.00", "variant_title": "M"}],
        "tracking_number": None,
        "tracking_url": None,
        "tracking_company": None,
        "shipment_status": None,
        "shipped_at": None,
        "fulfillments": [],
    }
    order.update(overrides)
    return order


def test_single_shipment_uses_the_plain_shipping_info_block():
    order = _base_order(
        tracking_number="TRACK1", tracking_company="UPS", shipment_status="in_transit",
        fulfillments=[{"tracking_number": "TRACK1", "tracking_company": "UPS",
                       "shipment_status": "in_transit", "tracking_url": None, "shipped_at": None}],
    )
    context = _build_order_context(order)
    assert "SHIPPING INFO:" in context
    assert "SEPARATE SHIPMENTS" not in context
    assert "TRACK1" in context


def test_multiple_shipments_are_listed_distinctly_not_merged():
    order = _base_order(fulfillments=[
        {"tracking_number": "TRACK-A", "tracking_company": "UPS",
         "shipment_status": "delivered", "tracking_url": None, "shipped_at": "2026-01-02T00:00:00Z"},
        {"tracking_number": "TRACK-B", "tracking_company": "FedEx",
         "shipment_status": "in_transit", "tracking_url": None, "shipped_at": "2026-01-03T00:00:00Z"},
    ])
    context = _build_order_context(order)
    assert "2 SEPARATE SHIPMENTS" in context
    assert "TRACK-A" in context and "TRACK-B" in context
    assert "UPS" in context and "FedEx" in context
    assert "Shipment 1:" in context and "Shipment 2:" in context
    assert "Do NOT combine them into a single tracking number" in context


def test_multiple_shipments_missing_tracking_is_not_invented():
    order = _base_order(fulfillments=[
        {"tracking_number": "TRACK-A", "tracking_company": "UPS",
         "shipment_status": "delivered", "tracking_url": None, "shipped_at": None},
        {"tracking_number": None, "tracking_company": None,
         "shipment_status": None, "tracking_url": None, "shipped_at": None},
    ])
    context = _build_order_context(order)
    assert "2 SEPARATE SHIPMENTS" in context
    assert "TRACK-A" in context
    assert "not yet available from the carrier" in context


def test_no_fulfillments_falls_back_to_unfulfilled_message():
    order = _base_order(status="unfulfilled", fulfillments=[])
    context = _build_order_context(order)
    assert "SEPARATE SHIPMENTS" not in context
    assert "hasn't shipped" in context
