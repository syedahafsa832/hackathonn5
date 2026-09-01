"""
WISMO — deterministic tracking-status normalization + multi-shipment context.

Reuses the existing, mature Aftership integration (tracking_service.py) —
no new provider, no new HTTP client. Additive only: normalize_status(),
get_last_failure_reason(), and the new structured fields on
get_tracking_status()'s success dict do not change its existing contract
(still returns None on any failure — see test_aftership_tracking.py, which
this file leaves untouched and still passes).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.tracking_service import (  # noqa: E402
    normalize_status, build_tracking_context, build_shipment_context,
    TRACKING_NOT_FOUND, TRACKING_PROVIDER_TIMEOUT,
)


# ── Deterministic normalization (never LLM-classified) ─────────────────────

def test_in_transit_tag_normalizes_to_in_transit():
    assert normalize_status("InTransit", "Moving Through Network") == "IN_TRANSIT"


def test_generic_transit_message_under_intransit_tag_normalizes_to_in_transit():
    assert normalize_status("InTransit", "Shipment on the way") == "IN_TRANSIT"


def test_distribution_center_message_overrides_to_at_distribution_center():
    """Aftership has no distinct tag for this - only the checkpoint's
    message text distinguishes it from a plain IN_TRANSIT."""
    assert normalize_status("InTransit", "Arrived at distribution center") == "AT_DISTRIBUTION_CENTER"


def test_out_for_delivery_tag_normalizes_correctly():
    assert normalize_status("OutForDelivery", "Out for delivery") == "OUT_FOR_DELIVERY"


def test_delay_in_message_normalizes_to_delayed_even_under_exception_tag():
    assert normalize_status("Exception", "Shipment delayed due to weather") == "DELAYED"


def test_delivered_tag_normalizes_correctly():
    assert normalize_status("Delivered", "Delivered to front door") == "DELIVERED"


def test_available_for_pickup_tag_normalizes_correctly():
    assert normalize_status("AvailableForPickup", None) == "AVAILABLE_FOR_PICKUP"


def test_unrecognized_tag_normalizes_to_unknown_never_guessed():
    assert normalize_status("SomeFutureAftershipTag", None) == "UNKNOWN"


def test_missing_tag_and_message_normalizes_to_unknown():
    assert normalize_status(None, None) == "UNKNOWN"


# ── Missing-data truthfulness (never fabricate) ─────────────────────────────

def test_no_tracking_number_produces_truthful_fallback_not_a_lookup():
    ctx = build_tracking_context(None, None, None, None)
    assert "isn't available yet" in ctx
    assert "TRACKING_PROVIDER" not in ctx


def test_missing_eta_is_never_invented():
    info = {
        "status": "InTransit", "status_text": "In transit", "latest_location": "Austin",
        "latest_message": "In transit", "latest_time": None, "expected_delivery": None,
        "recent_checkpoints": [],
    }
    ctx = build_tracking_context(info, "TN1", None, "USPS")
    assert "not confirmed yet" in ctx
    # No fabricated date - the literal string must not contain a made-up ETA.
    assert "expected_delivery" not in ctx


def test_provider_unavailable_is_never_reported_as_a_shipment_delay():
    """The core distinction the task requires: TRACKING_PROVIDER_* failure
    reasons must produce a provider-outage message, never a 'delayed'
    claim."""
    ctx = build_tracking_context(None, "TN1", "https://track.example/TN1", "USPS", failure_reason=TRACKING_PROVIDER_TIMEOUT)
    assert "isn't returning an update right now" in ctx
    assert "NOT a shipment delay" in ctx
    # Explicitly forbids the false claim, but must never assert it as fact.
    assert "your order is delayed" not in ctx.lower()
    assert "shipment is delayed" not in ctx.lower().replace("say the shipment is delayed", "")


def test_tracking_not_found_reason_does_not_trigger_the_provider_outage_message():
    """A genuine 404 (Aftership itself is healthy, this tracking number just
    isn't in its system yet) is not a provider outage - falls through to the
    normal "no live data" branch instead."""
    ctx = build_tracking_context(None, "TN1", None, "USPS", failure_reason=TRACKING_NOT_FOUND)
    assert "isn't returning an update right now" not in ctx


# ── Multiple shipments ──────────────────────────────────────────────────────

def test_single_shipment_list_matches_build_tracking_context_exactly():
    info = {"status": "Delivered", "status_text": "Delivered", "latest_location": "Austin",
            "latest_message": "Delivered", "latest_time": None, "expected_delivery": None,
            "recent_checkpoints": []}
    shipments = [{"tracking_number": "TN1", "tracking_url": None, "tracking_company": "USPS", "tracking_info": info, "failure_reason": None}]
    assert build_shipment_context(shipments) == build_tracking_context(info, "TN1", None, "USPS", None)


def test_two_shipments_with_mixed_statuses_are_both_represented():
    delivered = {"status": "Delivered", "normalized_status": "DELIVERED", "status_description": "Delivered", "expected_delivery": None}
    in_transit = {"status": "InTransit", "normalized_status": "IN_TRANSIT", "status_description": "In transit", "expected_delivery": None}
    shipments = [
        {"tracking_number": "TN1", "tracking_company": "UPS", "tracking_info": delivered, "failure_reason": None},
        {"tracking_number": "TN2", "tracking_company": "USPS", "tracking_info": in_transit, "failure_reason": None},
    ]
    ctx = build_shipment_context(shipments)
    assert "2 separate packages" in ctx
    assert "DELIVERED" in ctx
    assert "IN_TRANSIT" in ctx
    assert "UPS" in ctx and "USPS" in ctx
    assert "Do NOT describe only one package" in ctx


def test_shipment_with_no_tracking_number_is_reported_truthfully_not_skipped():
    shipments = [
        {"tracking_number": "TN1", "tracking_company": "UPS", "tracking_info": {"status": "Delivered", "normalized_status": "DELIVERED", "status_description": "Delivered", "expected_delivery": None}, "failure_reason": None},
        {"tracking_number": None, "tracking_company": None, "tracking_info": None, "failure_reason": None},
    ]
    ctx = build_shipment_context(shipments)
    assert "no tracking number yet" in ctx
