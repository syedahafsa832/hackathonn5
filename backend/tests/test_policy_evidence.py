"""
Policy Evidence & Deterministic Decision Layer.

RAG (get_custom_policy_text) answers "what does the merchant's policy say?"
src/services/policy_evidence.py answers "does this customer's real order
satisfy that policy?" - using ONLY the already-fetched, authoritative
Shopify order timestamp, never the customer's wording. The LLM never
decides this; return_actions_integration.py / actions_manager.py only ask
it to explain an already-computed result.

Covers the exact bug reported: a free-text policy like "cancel within 2
hours" was previously never verified at all - ANY custom policy text
(regardless of content) forced a blind escalation, so a same-day order
could get a wishy-washy "might still be within the window" reply instead of
a deterministic answer.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services.policy_evidence import verify_time_window, _extract_window_hours  # noqa: E402
from src.services.actions_manager import actions_manager  # noqa: E402
from src.services.return_actions_integration import ReturnActionsIntegration  # noqa: E402
from src.services.intent_detector import IntentResult  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ══════════════════════════════════════════════════════════════════════════
# Unit tests — _extract_window_hours / verify_time_window in isolation
# ══════════════════════════════════════════════════════════════════════════

def test_extract_window_hours_recognizes_common_phrasings():
    assert _extract_window_hours("Orders can be cancelled within 2 hours of placing the order.", ["cancel"]) == 2.0
    assert _extract_window_hours("Cancellations must be requested within 2 hours.", ["cancel"]) == 2.0
    assert _extract_window_hours("You have 2 hours to cancel your order after purchase.", ["cancel"]) == 2.0
    assert _extract_window_hours("Refunds are available within 30 days of delivery.", ["refund"]) == 30.0 * 24
    assert _extract_window_hours("Returns must be initiated within 14 days.", ["return"]) == 14.0 * 24


def test_extract_window_hours_does_not_cross_unrelated_sentences():
    """An unrelated 'ships within 5 days' clause in the same document must
    never be mistaken for a cancellation window."""
    text = "We ship worldwide within 5 days of purchase. Cancellations are not accepted once shipped."
    assert _extract_window_hours(text, ["cancel"]) is None


def test_extract_window_hours_returns_none_for_unparseable_policy():
    """'damaged products require photographic evidence' (or any policy this
    module doesn't recognize) must never be guessed at."""
    assert _extract_window_hours("Damaged products require photographic evidence before a refund is issued.", ["refund"]) is None
    assert _extract_window_hours("Orders cannot be refunded after 24 hours.", ["refund"]) is None  # "after", not "within" — outside this module's narrow scope


# 1. Expired cancellation window
def test_expired_cancellation_window_is_ineligible():
    order_created_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    result = verify_time_window("Orders can be cancelled within 2 hours of placing the order.", order_created_at, ["cancel"])
    assert result["status"] == "INELIGIBLE"
    assert result["evidence"]["policy_window_hours"] == 2.0


# 2. Valid cancellation window
def test_valid_cancellation_window_is_eligible():
    order_created_at = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    result = verify_time_window("Orders can be cancelled within 2 hours of placing the order.", order_created_at, ["cancel"])
    assert result["status"] == "ELIGIBLE"


# 4. Missing evidence -> UNKNOWN, never auto-eligible
def test_missing_order_timestamp_is_unknown_never_eligible():
    result = verify_time_window("Orders can be cancelled within 2 hours.", None, ["cancel"])
    assert result["status"] == "UNKNOWN"
    assert result["status"] != "ELIGIBLE"


def test_unparseable_policy_is_unknown_never_eligible():
    order_created_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    result = verify_time_window("Damaged products require photographic evidence.", order_created_at, ["cancel"])
    assert result["status"] == "UNKNOWN"


def test_invalid_timestamp_is_unknown_never_raises():
    result = verify_time_window("Cancel within 2 hours.", "not-a-real-timestamp", ["cancel"])
    assert result["status"] == "UNKNOWN"


# 10. Timezone correctness / exact boundary
def test_boundary_one_second_before_deadline_is_eligible():
    now = datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc)
    order_created_at = (now - timedelta(hours=1, minutes=59, seconds=59)).isoformat()
    result = verify_time_window("Cancel within 2 hours.", order_created_at, ["cancel"], now=now)
    assert result["status"] == "ELIGIBLE"


def test_boundary_exact_deadline_is_ineligible():
    now = datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc)
    order_created_at = (now - timedelta(hours=2)).isoformat()
    result = verify_time_window("Cancel within 2 hours.", order_created_at, ["cancel"], now=now)
    assert result["status"] == "INELIGIBLE"


def test_timezone_aware_across_offsets():
    """A non-UTC offset timestamp (Shopify sometimes returns store-local
    offsets, e.g. +05:00) must be handled correctly, not naively compared
    against a UTC 'now' as if both were the same wall-clock time."""
    order_created_at = "2026-08-24T14:30:00+05:00"  # = 09:30:00 UTC
    now = datetime(2026, 8, 24, 10, 30, 0, tzinfo=timezone.utc)  # 1h after the UTC-equivalent instant
    result = verify_time_window("Cancel within 2 hours.", order_created_at, ["cancel"], now=now)
    assert result["status"] == "ELIGIBLE"
    assert abs(result["evidence"]["elapsed_hours"] - 1.0) < 0.01


# ══════════════════════════════════════════════════════════════════════════
# Integration — return_actions_integration.handle_return_intent (cancellation)
# ══════════════════════════════════════════════════════════════════════════

def _run_cancel(order_created_at, policy_text, query="can I cancel my order?", order_id="1013"):
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="cancel", order_id=order_id, raw_address=None, confidence=0.9)
    eligibility = {
        "eligible": False, "reason": "order not yet fulfilled",
        "order": {"fulfillment_status": "unfulfilled", "created_at": order_created_at},
        "items": [], "order_total": "45.00",
    }
    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=eligibility)), \
         patch.object(integration.actions, "get_custom_policy_text", new=AsyncMock(return_value=policy_text)), \
         patch.object(integration, "_find_active_action", new=AsyncMock(return_value=None)), \
         patch.object(integration, "_maybe_autopilot_cancel", new=AsyncMock(return_value=None)) as mock_autopilot, \
         patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True, "action_id": "a1"})) as mock_create:
        result = run(integration.handle_return_intent(
            query=query, customer_info={"name": "Jane", "email": "jane@example.com"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1", intent_result=intent,
        ))
    return result, mock_create, mock_autopilot


# 9. Example from the spec: order placed yesterday, 2h cancellation window
def test_cancellation_request_outside_window_is_declined_not_escalated_not_staged():
    result, mock_create, mock_autopilot = _run_cancel(
        order_created_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        policy_text="Orders can be cancelled within 2 hours of placing the order.",
    )
    mock_create.assert_not_called()
    mock_autopilot.assert_not_called()
    assert "NOT ELIGIBLE" in result["action_context"]
    assert "MANUAL REVIEW" not in result["action_context"]
    # action_context is the instruction fed to the LLM, not the customer-
    # facing text itself — it must explicitly forbid the reported bug's
    # hedge ("your order might still be within the window") rather than
    # leaving the model free to guess.
    assert "factually" in result["action_context"].lower()
    assert "never say it 'might' still qualify" in result["action_context"]


# 10. Example from the spec: order placed 45 minutes ago, 2h window -> continues through existing flow
def test_cancellation_request_inside_window_continues_through_existing_staging_flow():
    result, mock_create, mock_autopilot = _run_cancel(
        order_created_at=(datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat(),
        policy_text="Orders can be cancelled within 2 hours of placing the order.",
    )
    mock_create.assert_awaited_once()
    _, kwargs = mock_create.call_args
    assert kwargs["action_type"] == "cancel_order"
    mock_autopilot.assert_awaited_once()  # existing Autopilot safety gate still consulted, never bypassed


# 3. Customer wording cannot override Shopify
def test_customer_claimed_order_age_never_overrides_shopify_timestamp():
    """Customer says '20 minutes ago', Shopify says yesterday — Shopify wins."""
    result, mock_create, mock_autopilot = _run_cancel(
        order_created_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        policy_text="Orders can be cancelled within 2 hours of placing the order.",
        query="Can I cancel? I ordered this 20 minutes ago.",
    )
    mock_create.assert_not_called()
    assert "NOT ELIGIBLE" in result["action_context"]


# Unparseable/other free-text policy still falls back to today's existing
# escalation behavior, completely unchanged.
def test_unparseable_custom_policy_still_escalates_as_before():
    result, mock_create, mock_autopilot = _run_cancel(
        order_created_at=(datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
        policy_text="Cancellations are handled on a case-by-case basis by our support team.",
    )
    mock_create.assert_awaited_once()
    mock_autopilot.assert_not_called()
    assert "MANUAL REVIEW" in result["action_context"]


# 5. Human-required evidence (e.g. photo) — not a time window at all, so
# this module correctly returns UNKNOWN and the EXISTING escalation
# behavior (unchanged) is what actually asks for/defers to a human.
def test_photo_evidence_policy_is_unknown_and_falls_back_to_existing_escalation():
    result, mock_create, mock_autopilot = _run_cancel(
        order_created_at=(datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
        policy_text="Damaged products require photographic evidence before any cancellation or refund.",
    )
    mock_create.assert_awaited_once()
    mock_autopilot.assert_not_called()
    assert "MANUAL REVIEW" in result["action_context"]


# 4 (integration level). Missing Shopify created_at -> UNKNOWN -> existing escalation, never auto-eligible.
def test_missing_created_at_falls_back_to_existing_escalation_never_auto_eligible():
    result, mock_create, mock_autopilot = _run_cancel(
        order_created_at=None,
        policy_text="Orders can be cancelled within 2 hours of placing the order.",
    )
    mock_create.assert_awaited_once()
    mock_autopilot.assert_not_called()
    assert "MANUAL REVIEW" in result["action_context"]
    assert "NOT ELIGIBLE" not in result["action_context"]


# 8. Existing Cancellation Autopilot safety gate is still consulted (not
# bypassed) for a window-verified-eligible cancellation, and remains
# entirely in control of whether execution actually happens.
def test_autopilot_gate_still_decides_execution_for_a_verified_eligible_window():
    order_created_at = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="cancel", order_id="1013", raw_address=None, confidence=0.9)
    eligibility = {
        "eligible": False, "reason": "order not yet fulfilled",
        "order": {"fulfillment_status": "unfulfilled", "created_at": order_created_at},
        "items": [], "order_total": "45.00",
    }
    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=eligibility)), \
         patch.object(integration.actions, "get_custom_policy_text", new=AsyncMock(
             return_value="Orders can be cancelled within 2 hours of placing the order.")), \
         patch.object(integration, "_find_active_action", new=AsyncMock(return_value=None)), \
         patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True, "action_id": "a1"})), \
         patch.object(integration, "_maybe_autopilot_cancel", new=AsyncMock(return_value=None)) as mock_autopilot:
        run(integration.handle_return_intent(
            query="cancel my order", customer_info={"name": "Jane", "email": "jane@example.com"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1", ticket_id="ticket-1", intent_result=intent,
        ))
    # The window-verified-eligible path still routes through the exact same
    # _maybe_autopilot_cancel gate as the no-custom-policy path — never a
    # second, weaker execution path.
    mock_autopilot.assert_awaited_once()


# 7. Tenant/brand isolation — Brand A's policy text is never used for Brand B.
def test_brand_isolation_policy_and_evidence_never_cross_brands():
    order_a_created_at = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()  # within 2h -> eligible
    order_b_created_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()      # outside 2h -> ineligible

    calls = {}

    async def fake_get_custom_policy_text(brand_id):
        calls.setdefault("policy_calls", []).append(brand_id)
        # Each brand has its own, distinct policy text.
        return {
            "brand-A": "Orders can be cancelled within 2 hours of placing the order.",
            "brand-B": "Orders can be cancelled within 2 hours of placing the order.",
        }[brand_id]

    def _eligibility_for(brand_id):
        created_at = order_a_created_at if brand_id == "brand-A" else order_b_created_at
        return {
            "eligible": False, "reason": "order not yet fulfilled",
            "order": {"fulfillment_status": "unfulfilled", "created_at": created_at},
            "items": [], "order_total": "45.00",
        }

    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="cancel", order_id="1013", raw_address=None, confidence=0.9)

    results = {}
    for brand_id in ("brand-A", "brand-B"):
        async def fake_elig(order_id, email, tenant_id=None, brand_id=brand_id):
            return _eligibility_for(brand_id)

        with patch.object(integration.actions, "check_return_eligibility", new=fake_elig), \
             patch.object(integration.actions, "get_custom_policy_text", new=fake_get_custom_policy_text), \
             patch.object(integration, "_find_active_action", new=AsyncMock(return_value=None)), \
             patch.object(integration, "_maybe_autopilot_cancel", new=AsyncMock(return_value=None)), \
             patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True, "action_id": "a1"})) as mock_create:
            results[brand_id] = run(integration.handle_return_intent(
                query="cancel my order", customer_info={"name": "Jane", "email": "jane@example.com"},
                existing_tool_results={}, tenant_id=f"tenant-{brand_id}", brand_id=brand_id, intent_result=intent,
            ))
            results[brand_id + "_created"] = mock_create.await_count > 0

    # Brand A (30 min old order) -> eligible, staged for cancellation.
    assert "NOT ELIGIBLE" not in results["brand-A"]["action_context"]
    assert results["brand-A_created"] is True
    # Brand B (1 day old order, same policy text) -> ineligible, never staged
    # — proves the decision is computed per-brand from that brand's own
    # order data, never leaked/cached from brand A's evaluation.
    assert "NOT ELIGIBLE" in results["brand-B"]["action_context"]
    assert results["brand-B_created"] is False
    assert calls["policy_calls"] == ["brand-A", "brand-B"]


# ══════════════════════════════════════════════════════════════════════════
# Integration — actions_manager.check_return_eligibility (refund/return/exchange)
# ══════════════════════════════════════════════════════════════════════════

def _order(days_old=0, hours_old=None, fulfillment_status="fulfilled"):
    if hours_old is not None:
        created_at = (datetime.now(timezone.utc) - timedelta(hours=hours_old)).isoformat()
    else:
        created_at = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    return {
        "id": 555, "order_number": "#1001", "email": "customer@example.com",
        "created_at": created_at, "fulfillment_status": fulfillment_status, "tags": "",
        "total_price": "45.00", "currency": "USD", "refunds": [], "cancelled_at": None,
        "line_items": [{"id": 1, "product_id": 111, "title": "Essential Hoodie", "quantity": 1,
                         "price": "45.00", "sku": "EH-1", "requires_shipping": True}],
    }


async def _check_eligibility(order, brand, kb_context=""):
    async def fake_get_order(order_id, email, tenant_id=None):
        return order

    async def fake_get_brand(brand_id):
        return brand

    async def fake_get_kb_context(brand_id, query, top_k=3):
        return kb_context

    with patch.object(actions_manager, "_get_order_from_shopify", side_effect=fake_get_order), \
         patch("src.services.actions_manager.supabase_select", return_value=[]), \
         patch("src.services.brand_manager.brand_manager.get_brand", side_effect=fake_get_brand), \
         patch("src.services.brand_knowledge_service.brand_knowledge_service.get_brand_context", side_effect=fake_get_kb_context):
        return await actions_manager.check_return_eligibility(
            "1001", "customer@example.com", tenant_id="t1", brand_id="b1",
        )


def test_refund_window_verified_ineligible_when_order_too_old():
    order = _order(days_old=5, fulfillment_status="fulfilled")
    brand = {"id": "b1", "refund_notes": ""}
    result = run(_check_eligibility(order, brand, kb_context="Refunds must be requested within 24 hours of delivery."))
    assert result["eligible"] is False
    assert result["policy_verification"]["status"] == "INELIGIBLE"


def test_refund_window_verified_eligible_when_order_recent():
    order = _order(hours_old=1, fulfillment_status="fulfilled")
    brand = {"id": "b1", "refund_notes": ""}
    result = run(_check_eligibility(order, brand, kb_context="Refunds must be requested within 24 hours of delivery."))
    assert result["eligible"] is True
    assert result["policy_verification"]["status"] == "ELIGIBLE"


# 9. No false success: a customer must never receive an eligible/success
# claim when verification is uncertain or failed.
def test_no_false_eligibility_when_policy_text_is_unparseable():
    order = _order(days_old=5, fulfillment_status="fulfilled")
    brand = {"id": "b1", "refund_notes": ""}
    result = run(_check_eligibility(order, brand, kb_context="Refund requests are handled case-by-case by our team."))
    # Unparseable -> UNKNOWN -> falls back to existing safe escalation, never eligible.
    assert result["eligible"] is False
    assert result["requires_manual_review"] is True


# 6. Policy retrieval — the correct brand's policy is what gets verified.
def test_policy_retrieval_uses_the_owning_brands_policy_text():
    order = _order(hours_old=1, fulfillment_status="fulfilled")
    brand = {"id": "b1", "refund_notes": "Refunds must be requested within 2 hours of delivery."}
    result = run(_check_eligibility(order, brand))
    assert result["policy_verification"]["evidence"]["policy_window_hours"] == 2.0
