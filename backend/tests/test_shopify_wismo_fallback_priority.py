"""
WISMO fallback priority fix: Shopify-native shipment_status > live tracking
provider (AfterShip) > tracking-only > honest-unavailable.

Bug: build_tracking_context()/build_shipment_context() are called whenever a
tracking number exists and NEVER return an empty string - even the "no live
status available" branch returns real instruction text. _build_order_context's
old `elif tracking_context:` therefore won unconditionally every time a
tracking number existed, so a real, known Shopify fulfillment.shipment_status
(e.g. "delivered") sat right there unused while Luna was told live status was
unavailable - confirmed live.

Fix: _build_order_context now checks shipment_status against a fixed,
narrow set of known values (_SHOPIFY_NATIVE_SHIPMENT_STATUSES) BEFORE
tracking_context. Only those known values are ever treated as a real status;
anything else falls through to tracking_context (AfterShip, if it ran) or
the plain tracking-only block - unchanged from before this fix.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.agent.customer_success_agent import _build_order_context  # noqa: E402
from src.services.tracking_service import build_tracking_context  # noqa: E402


def _order(**overrides):
    order = {
        "success": True, "order_number": 1001, "status": "fulfilled",
        "financial_status": "paid", "cancelled_at": None, "total_amount": "50.00",
        "items": [{"title": "Hoodie", "quantity": 1, "price": "50.00", "variant_title": "M"}],
        "tracking_number": None, "tracking_url": None, "tracking_company": None,
        "shipment_status": None, "shipped_at": None, "fulfillments": [],
    }
    order.update(overrides)
    return order


# 1. Shopify shipment_status="delivered" + tracking number -> Shopify's
#    delivered status is used, not the generic "unavailable" text.
def test_shopify_delivered_status_takes_priority():
    order = _order(tracking_number="TN1", tracking_company="TCS", shipment_status="delivered")
    # Simulate the caller building tracking_context as it would when NO live
    # AfterShip data came back (the common real-world case for this bug).
    tracking_context = build_tracking_context(None, "TN1", None, "TCS")
    context = _build_order_context(order, tracking_context=tracking_context)

    assert "Shopify's own native shipment status" in context
    assert "delivered" in context
    assert "live status unavailable" not in context.lower()
    assert "carrier isn't returning a current tracking update" not in context


# 2. Shopify shipment_status="in_transit" + tracking number -> in-transit
#    status is used.
def test_shopify_in_transit_status_takes_priority():
    order = _order(tracking_number="TN2", tracking_company="DHL", shipment_status="in_transit")
    tracking_context = build_tracking_context(None, "TN2", None, "DHL")
    context = _build_order_context(order, tracking_context=tracking_context)

    assert "Shopify's own native shipment status" in context
    assert "in transit" in context


# 3. Tracking number + tracking URL + NO shipment_status -> tracking-only
#    context (no invented status).
def test_no_shipment_status_produces_tracking_only_context():
    order = _order(tracking_number="TN3", tracking_url="https://track.example/TN3",
                    tracking_company="FedEx", shipment_status=None)
    tracking_context = build_tracking_context(None, "TN3", "https://track.example/TN3", "FedEx")
    context = _build_order_context(order, tracking_context=tracking_context)

    assert "Shopify's own native shipment status" not in context
    assert "TN3" in context
    # "delivered"/"in transit"/etc. only ever appear inside the HARD RULES
    # negative instruction (do NOT say these) - never as a claimed status.
    assert "Current status: delivered" not in context
    assert "Do NOT say the shipment is delayed, in transit, out for delivery, or delivered" in context


# 4. Shopify shipment_status exists -> the generic "no live tracking status
#    available" branch must NOT override it (the exact bug, asserted directly).
def test_generic_unavailable_branch_never_overrides_a_known_shopify_status():
    order = _order(tracking_number="TN4", tracking_company="UPS", shipment_status="out_for_delivery")
    tracking_context = build_tracking_context(None, "TN4", None, "UPS")
    assert "No live carrier status available" in tracking_context  # sanity: this IS the old always-wins text

    context = _build_order_context(order, tracking_context=tracking_context)
    assert "No live carrier status available" not in context
    assert "out for delivery" in context


# 5. AfterShip still works when Shopify has NO native shipment_status -
#    existing live-tracking behavior is fully preserved.
def test_aftership_live_data_still_used_when_no_shopify_status():
    order = _order(tracking_number="TN5", tracking_company="DHL", shipment_status=None)
    live_info = {
        "status": "Delivered", "status_text": "Delivered", "latest_message": "Delivered to front door",
        "latest_location": "Karachi", "latest_time": "2026-09-15T10:00:00Z", "expected_delivery": None,
        "recent_checkpoints": [],
    }
    tracking_context = build_tracking_context(live_info, "TN5", None, "DHL")
    context = _build_order_context(order, tracking_context=tracking_context)

    assert "Shopify's own native shipment status" not in context  # Case 1 correctly skipped
    assert "LIVE FROM AFTERSHIP" in context
    assert "Your order was delivered" in context


# 6. No Shopify native status + no AfterShip result -> honest tracking-only /
#    unavailable behavior (unchanged from before this fix).
def test_no_shopify_status_and_no_aftership_is_honest_not_fabricated():
    order = _order(tracking_number="TN6", tracking_company="Speedex", shipment_status=None)
    tracking_context = build_tracking_context(None, "TN6", None, "Speedex")
    context = _build_order_context(order, tracking_context=tracking_context)

    assert "Shopify's own native shipment status" not in context
    assert "HARD RULES" in context
    assert "Do NOT say the shipment is delayed, in transit, out for delivery, or delivered" in context


# 7. Existing non-WISMO behavior (cancellation surfacing) is unaffected by
#    this change - regression guard, not a WISMO case.
def test_cancelled_order_context_unaffected():
    order = _order(status="unfulfilled", cancelled_at="2026-08-22T06:23:54Z", tracking_number=None)
    context = _build_order_context(order)
    assert "CANCELLED: Yes" in context
    assert "2026-08-22T06:23:54Z" in context


# ── Safety: unrecognized shipment_status values are never guessed at ──────

def test_unrecognized_shipment_status_falls_through_not_invented():
    order = _order(tracking_number="TN7", tracking_company="TCS", shipment_status="confirmed")
    tracking_context = build_tracking_context(None, "TN7", None, "TCS")
    context = _build_order_context(order, tracking_context=tracking_context)

    # "confirmed" is not in the known-status map - must fall through to the
    # tracking_context branch (honest "unavailable"), never surfaced as if
    # it were a real shipment status.
    assert "Shopify's own native shipment status" not in context
    assert "Current status: confirmed" not in context
