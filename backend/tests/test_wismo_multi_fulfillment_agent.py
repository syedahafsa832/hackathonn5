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
    """No brand-configured key AND no platform key -> zero Aftership calls,
    falls back to Shopify-only data (never attempts a lookup with no
    credential). Platform key (see resolve_aftership_api_key in
    tracking_service.py) is explicitly cleared here so this test doesn't
    depend on whether the real process environment happens to have
    AFTERSHIP_API_KEY set."""
    import src.services.tracking_service as tracking_mod
    original_platform_key = tracking_mod.PLATFORM_AFTERSHIP_API_KEY
    tracking_mod.PLATFORM_AFTERSHIP_API_KEY = None
    try:
        brand_no_key = {**BRAND_WITH_AFTERSHIP, "aftership_api_key": None}
        _, _, tracking_mock = _run_wismo_query(brand_no_key, lambda *a, **k: None)
    finally:
        tracking_mod.PLATFORM_AFTERSHIP_API_KEY = original_platform_key
    tracking_mock.assert_not_awaited()


# ── Core regression: the exact reported production bug ──────────────────────
#
# "where is my order #1008 track it" -> AfterShip returns nothing (this
# tracking number was never registered - a genuine TRACKING_NOT_FOUND, not
# a timeout/outage) -> Luna answered "Your order has shipped and is on its
# way to you... It should arrive in a couple of days" with ZERO tracking
# evidence behind that claim, and the activity timeline showed no failure
# event at all (only "Checking shipping status..."). Root cause:
# TRACKING_NOT_FOUND was excluded from the "provider failure" event/prompt
# path, so it silently fell through to a branch with no fabrication
# guardrail. These tests reproduce that exact scenario end-to-end and prove
# both symptoms are fixed.

ONE_SHIPMENT_ORDER = {
    "success": True, "order_number": "1008", "order_id": "1008",
    "status": "fulfilled", "financial_status": "paid", "cancelled_at": None,
    "tracking_number": "9205510200881234567890", "tracking_url": "https://tools.usps.com/go/TrackConfirmAction_input?qtc_tLabels1=9205510200881234567890",
    "tracking_company": "USPS", "shipment_status": "fulfilled", "shipped_at": "2026-09-01T16:52:29Z",
    "fulfillments": [
        {"tracking_number": "9205510200881234567890",
         "tracking_url": "https://tools.usps.com/go/TrackConfirmAction_input?qtc_tLabels1=9205510200881234567890",
         "tracking_company": "USPS", "shipment_status": "fulfilled", "created_at": "2026-09-01T16:52:29Z"},
    ],
    "fulfillment_count": 1, "total_amount": "25.00", "items": [], "created_at": "2026-08-26T11:20:15Z",
}


def _run_wismo_query_one_shipment(brand_row, tracking_side_effect):
    captured = {}
    events = []

    async def capturing_completion(*args, messages=None, **kwargs):
        captured["messages"] = messages
        return (_fake_ai_response(), "test_provider", "test_model", _FAKE_USAGE)

    async def on_progress(stage, label):
        events.append((stage, label))

    def fake_select(table, params=None):
        if table == "brands":
            return [brand_row]
        if table == "tickets":
            return []
        return []

    tracking_mock = AsyncMock(side_effect=tracking_side_effect)

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=capturing_completion), \
         patch("src.agent.customer_success_agent.v3_tools.get_order_status", new=AsyncMock(return_value=ONE_SHIPMENT_ORDER)), \
         patch("src.agent.customer_success_agent.v3_tools.get_orders_by_email", new=AsyncMock(return_value={"success": False})), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value="")), \
         patch("src.services.tracking_service.get_tracking_status", new=tracking_mock), \
         patch("src.lib.supabase_client.supabase_select", side_effect=fake_select):
        run(customer_success_agent.process_customer_query(
            query="where is my order #1008 track it",
            customer_info={"name": "Kamran", "email": "customer@example.com"},
            tenant_id="tenant-1", store_id="brand-1", ticket_id="ticket-1",
            on_progress=on_progress,
        ))

    prompt_text = "\n".join(m.get("content", "") for m in captured.get("messages", []))
    return prompt_text, events


async def _not_found_side_effect(tracking_number, carrier_slug, api_key):
    """Simulates the real production case: Aftership genuinely has no
    record for this tracking number (never registered / plan-restricted) -
    get_tracking_status returns None with get_last_failure_reason() ==
    TRACKING_NOT_FOUND, exactly like the real call in production."""
    from src.services import tracking_service
    tracking_service._last_failure_reason = tracking_service.TRACKING_NOT_FOUND
    return None


def test_tracking_not_found_emits_unavailable_event_not_silence():
    """THE reported bug's missing activity event: previously TRACKING_NOT_FOUND
    produced neither tracking_retrieved nor tracking_unavailable - the
    timeline just went quiet after "Checking shipping status...". Now it
    must emit the honest failure event."""
    prompt_text, events = _run_wismo_query_one_shipment(BRAND_WITH_AFTERSHIP, _not_found_side_effect)

    stages = [s for s, _ in events]
    assert "shipping_lookup" in stages
    assert "tracking_unavailable" in stages
    assert "tracking_retrieved" not in stages  # never a false success


def test_tracking_not_found_never_fabricates_shipped_status_or_eta_in_llm_prompt():
    """THE reported bug's fabricated claim: with zero tracking evidence, the
    prompt reaching the LLM must contain the hard no-fabrication guardrail
    and must never itself assert the order has shipped/is on its way with
    an invented ETA."""
    prompt_text, events = _run_wismo_query_one_shipment(BRAND_WITH_AFTERSHIP, _not_found_side_effect)

    assert "isn't returning a current tracking update right now" in prompt_text
    assert "Do NOT invent or estimate a delivery date/ETA" in prompt_text
    # The real Shopify tracking URL is still allowed through (task
    # requirement: don't remove the tracking URL, just don't claim it means
    # live-verified status).
    assert "9205510200881234567890" in prompt_text


# WISMO TEST MODE: Paths A-D (synthetic/mocked provider data only)

def _full_evidence_tracking_result():
    """PATH A: a realistic, complete provider response - every field the
    task requires present at once."""
    return {
        "status": "InTransit", "status_text": "In transit", "latest_location": "Austin, TX",
        "latest_message": "Arrived at Austin distribution center", "latest_time": "2026-09-01T10:00:00Z",
        "expected_delivery": "2026-09-05", "carrier_slug": "usps", "recent_checkpoints": [],
        "carrier_status": "InTransit", "normalized_status": "AT_DISTRIBUTION_CENTER",
        "status_description": "Arrived at Austin distribution center", "latest_event": "Arrived at Austin distribution center",
        "latest_event_location": "Austin, TX", "latest_event_timestamp": "2026-09-01T10:00:00Z", "events": [],
        "is_delivered": False, "is_out_for_delivery": False, "is_delayed": False,
        "is_exception": False, "provider": "aftership", "last_updated_at": "2026-09-01T10:00:00Z",
    }


def test_path_a_provider_success_reaches_luna_with_full_evidence_and_correct_event():
    """Shopify fulfillment -> tracking number -> provider lookup ->
    normalized tracking evidence -> Luna receives evidence -> activity says
    tracking retrieved -> response uses only verified tracking data."""
    async def side_effect(tracking_number, carrier_slug, api_key):
        return _full_evidence_tracking_result()

    prompt_text, events = _run_wismo_query_one_shipment(BRAND_WITH_AFTERSHIP, side_effect)

    stages = [s for s, _ in events]
    assert "tracking_retrieved" in stages
    assert "tracking_unavailable" not in stages
    # Single-shipment rendering (build_tracking_context) uses human-readable
    # status_text/message, not the raw normalized_status enum - that literal
    # string only appears in the multi-shipment renderer (see PATH D below).
    # The real evidence itself reaching the prompt is what matters here.
    assert "Arrived at Austin distribution center" in prompt_text
    assert "Sep 05" in prompt_text


def test_path_c_missing_tracking_number_never_invents_a_status():
    """Shopify fulfillment exists but carries no tracking number at all
    (order placed/fulfilled, label not yet created) -> no Aftership call,
    truthful "not available yet" message, never a fabricated status."""
    order_no_tracking = {
        "success": True, "order_number": "1008", "order_id": "1008",
        "status": "fulfilled", "financial_status": "paid", "cancelled_at": None,
        "tracking_number": None, "tracking_url": None, "tracking_company": None,
        "shipment_status": "fulfilled", "shipped_at": "2026-09-01T00:00:00Z",
        "fulfillments": [{"tracking_number": None, "tracking_url": None, "tracking_company": None,
                           "shipment_status": "fulfilled", "created_at": "2026-09-01T00:00:00Z"}],
        "fulfillment_count": 1, "total_amount": "25.00", "items": [], "created_at": "2026-08-26T00:00:00Z",
    }
    captured = {}
    events = []

    async def capturing_completion(*args, messages=None, **kwargs):
        captured["messages"] = messages
        return (_fake_ai_response(), "test_provider", "test_model", _FAKE_USAGE)

    async def on_progress(stage, label):
        events.append((stage, label))

    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND_WITH_AFTERSHIP]
        return []

    tracking_mock = AsyncMock()

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=capturing_completion), \
         patch("src.agent.customer_success_agent.v3_tools.get_order_status", new=AsyncMock(return_value=order_no_tracking)), \
         patch("src.agent.customer_success_agent.v3_tools.get_orders_by_email", new=AsyncMock(return_value={"success": False})), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value="")), \
         patch("src.services.tracking_service.get_tracking_status", new=tracking_mock), \
         patch("src.lib.supabase_client.supabase_select", side_effect=fake_select):
        run(customer_success_agent.process_customer_query(
            query="where is my order #1008?",
            customer_info={"name": "Kamran", "email": "customer@example.com"},
            tenant_id="tenant-1", store_id="brand-1", ticket_id="ticket-1",
            on_progress=on_progress,
        ))

    prompt_text = "\n".join(m.get("content", "") for m in captured.get("messages", []))
    tracking_mock.assert_not_awaited()
    assert "isn't available yet" in prompt_text
    # Checked against the actual order-evidence block only, not the whole
    # prompt - the static system-prompt boilerplate legitimately mentions
    # words like "in transit"/"delayed" as generic instructional examples
    # unrelated to this specific order; what must never happen is THIS
    # order's own evidence section claiming one of them with no data.
    order_block = prompt_text.split("=== REAL ORDER DATA")[1].split("=== END ORDER DATA")[0]
    for forbidden in ("in transit", "delivered", "out for delivery", "delayed", "couple of days", "arriving tomorrow"):
        assert forbidden not in order_block.lower()


def test_path_d_one_failed_shipment_never_corrupts_the_others_real_status():
    """Two fulfillments: TRACK-A gets full live evidence, TRACK-B's lookup
    fails (not found). TRACK-A's real status must reach the prompt intact,
    TRACK-B must show unavailable, and both activity events fire - one
    shipment's failure never overwrites or hides the other's real data."""
    async def side_effect(tracking_number, carrier_slug, api_key):
        if tracking_number == "TRACK-A":
            return _full_evidence_tracking_result()
        from src.services import tracking_service
        tracking_service._last_failure_reason = tracking_service.TRACKING_NOT_FOUND
        return None

    captured = {}
    events = []

    async def capturing_completion(*args, messages=None, **kwargs):
        captured["messages"] = messages
        return (_fake_ai_response(), "test_provider", "test_model", _FAKE_USAGE)

    async def on_progress(stage, label):
        events.append((stage, label))

    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND_WITH_AFTERSHIP]
        return []

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=capturing_completion), \
         patch("src.agent.customer_success_agent.v3_tools.get_order_status", new=AsyncMock(return_value=TWO_SHIPMENT_ORDER)), \
         patch("src.agent.customer_success_agent.v3_tools.get_orders_by_email", new=AsyncMock(return_value={"success": False})), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value="")), \
         patch("src.services.tracking_service.get_tracking_status", new=side_effect), \
         patch("src.lib.supabase_client.supabase_select", side_effect=fake_select):
        run(customer_success_agent.process_customer_query(
            query="where is my order #1013?",
            customer_info={"name": "Syeda", "email": "customer10@example.com"},
            tenant_id="tenant-1", store_id="brand-1", ticket_id="ticket-1",
            on_progress=on_progress,
        ))

    prompt_text = "\n".join(m.get("content", "") for m in captured.get("messages", []))
    stages = [s for s, _ in events]
    assert "tracking_retrieved" in stages
    assert "tracking_unavailable" in stages
    assert "AT_DISTRIBUTION_CENTER" in prompt_text
    assert "2 separate packages" in prompt_text
    assert "Do NOT invent a status for any package not listed above" in prompt_text


# UNKNOWN status can never be upgraded to a confident claim

def test_unknown_normalized_status_is_not_a_confident_claim_single_shipment():
    """build_tracking_context's single-shipment renderer only produces a
    specific confident claim (delivered / out for delivery / exception
    wording) for a small explicit set of recognized raw carrier tags -
    anything else, including a genuinely unrecognized one, falls through to
    the generic "on its way" phrasing, never a specific status claim it
    can't back up. This is what actually protects against an UNKNOWN
    status being dressed up as confident, since this renderer never emits
    the normalized_status enum literally at all (only the multi-shipment
    renderer does - see the mixed-status assertions in
    test_wismo_tracking_normalization.py, which do check the literal
    "UNKNOWN" string there)."""
    async def side_effect(tracking_number, carrier_slug, api_key):
        result = _full_evidence_tracking_result()
        result["status"] = "SomeNewUnrecognizedCarrierTag"  # raw tag genuinely unrecognized
        result["status_text"] = "Unrecognized carrier scan"  # must match - status_text is what's actually rendered
        result["normalized_status"] = "UNKNOWN"
        result["status_description"] = "Unrecognized carrier scan"
        result["latest_event"] = "Unrecognized carrier scan"
        result["expected_delivery"] = None
        return result

    prompt_text, events = _run_wismo_query_one_shipment(BRAND_WITH_AFTERSHIP, side_effect)

    order_block = prompt_text.split("=== REAL ORDER DATA")[1].split("=== END ORDER DATA")[0]
    for forbidden in ("in transit", "out for delivery", "your order was delivered", "delayed"):
        assert forbidden not in order_block.lower()
