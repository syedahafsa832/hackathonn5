"""
WISMO end-to-end: customer_success_agent.py now loops over every real
Shopify fulfillment (not just the first) for live Aftership tracking.
Reuses the existing harness pattern from
test_order_identity_verification_followup.py.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
import json  # noqa: E402
from src.agent.customer_success_agent import customer_success_agent  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


_FAKE_USAGE = {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}

TWO_SHIPMENT_ORDER = {
    "success": True, "order_number": "1013", "order_id": "1013",
    "status": "fulfilled", "financial_status": "paid", "cancelled_at": None,
    "tracking_number": "TRACK-A", "tracking_url": None, "tracking_company": "UPS",
    "shipment_status": "delivered", "shipped_at": "2026-01-02T00:00:00Z",
    "fulfillments": [
        {"tracking_number": "TRACK-A", "tracking_company": "UPS", "tracking_url": None,
         "shipment_status": "delivered", "created_at": "2026-01-02T00:00:00Z"},
        {"tracking_number": "TRACK-B", "tracking_company": "USPS", "tracking_url": None,
         "shipment_status": "in_transit", "created_at": "2026-01-03T00:00:00Z"},
    ],
    "fulfillment_count": 2, "total_amount": "80.00", "items": [], "created_at": "2026-01-01T00:00:00Z",
}


def _fake_ai_response():
    content = json.dumps({"intent": "order_status_inquiry", "reply_body": "ok", "risk_level": "low"})
    return MagicMock(choices=[MagicMock(message=MagicMock(content=content))])


def _run_wismo_query(brand_row, tracking_side_effect):
    captured = {}

    async def capturing_completion(*args, messages=None, **kwargs):
        captured["messages"] = messages
        return (_fake_ai_response(), "test_provider", "test_model", _FAKE_USAGE)

    def fake_select(table, params=None):
        if table == "brands":
            return [brand_row]
        if table == "tickets":
            return []
        return []

    tracking_mock = AsyncMock(side_effect=tracking_side_effect)

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=capturing_completion), \
         patch("src.agent.customer_success_agent.v3_tools.get_order_status", new=AsyncMock(return_value=TWO_SHIPMENT_ORDER)), \
         patch("src.agent.customer_success_agent.v3_tools.get_orders_by_email", new=AsyncMock(return_value={"success": False})), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value="")), \
         patch("src.services.tracking_service.get_tracking_status", new=tracking_mock), \
         patch("src.lib.supabase_client.supabase_select", side_effect=fake_select):
        result = run(customer_success_agent.process_customer_query(
            query="where is my order #1013?",
            customer_info={"name": "Syeda", "email": "customer10@example.com"},
            tenant_id="tenant-1", store_id="brand-1", ticket_id="ticket-1",
        ))

    prompt_text = "\n".join(m.get("content", "") for m in captured.get("messages", []))
    return result, prompt_text, tracking_mock


BRAND_WITH_AFTERSHIP = {"id": "brand-1", "name": "Test Store", "shopify_connected": False, "aftership_api_key": "test-key"}


def _tracking_result(status, message):
    return {
        "status": status, "status_text": status, "latest_location": "Austin",
        "latest_message": message, "latest_time": "2026-01-04T00:00:00Z",
        "expected_delivery": None, "carrier_slug": "ups", "recent_checkpoints": [],
        "carrier_status": status, "normalized_status": "DELIVERED" if status == "Delivered" else "IN_TRANSIT",
        "status_description": message, "latest_event": message, "latest_event_location": "Austin",
        "latest_event_timestamp": "2026-01-04T00:00:00Z", "events": [],
        "is_delivered": status == "Delivered", "is_out_for_delivery": False, "is_delayed": False,
        "is_exception": False, "provider": "aftership", "last_updated_at": "2026-01-04T00:00:00Z",
    }


def test_both_fulfillments_get_a_live_tracking_lookup_not_just_the_first():
    """The confirmed gap: only order.tracking_number (the first fulfillment)
    used to reach Aftership. Both TRACK-A and TRACK-B must now be queried."""
    def side_effect(tracking_number, carrier_slug, api_key):
        if tracking_number == "TRACK-A":
            return _tracking_result("Delivered", "Delivered to front door")
        return _tracking_result("InTransit", "In transit")

    result, prompt_text, tracking_mock = _run_wismo_query(BRAND_WITH_AFTERSHIP, side_effect)

    # Wiring, not re-proving string formatting - build_shipment_context's own
    # rendering (including the DELIVERED/IN_TRANSIT text) is already covered
    # exhaustively by test_wismo_tracking_normalization.py.
    called_numbers = {c.args[0] for c in tracking_mock.await_args_list}
    assert called_numbers == {"TRACK-A", "TRACK-B"}
    assert "2 separate packages" in prompt_text


def test_aftership_key_is_the_brands_own_scoped_key_never_customer_supplied():
    """Security: the tracking provider credential always comes from this
    conversation's own brand row - a customer's message can never influence
    which key or which tracking number gets queried (the loop only ever
    iterates the ORDER's own real Shopify fulfillments, never customer text)."""
    def side_effect(tracking_number, carrier_slug, api_key):
        assert api_key == "test-key"  # the brand's own key, not anything customer-derived
        return _tracking_result("Delivered", "Delivered")

    _, _, tracking_mock = _run_wismo_query(BRAND_WITH_AFTERSHIP, side_effect)
    assert tracking_mock.await_count == 2
    for c in tracking_mock.await_args_list:
        assert c.args[0] in ("TRACK-A", "TRACK-B")  # only real order tracking numbers, nothing else


def test_no_aftership_key_configured_skips_live_lookup_entirely():
    """No brand-configured key -> zero Aftership calls, falls back to
    Shopify-only data (never attempts a lookup with no credential)."""
    brand_no_key = {**BRAND_WITH_AFTERSHIP, "aftership_api_key": None}
    _, _, tracking_mock = _run_wismo_query(brand_no_key, lambda *a, **k: None)
    tracking_mock.assert_not_awaited()
