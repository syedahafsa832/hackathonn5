"""
Merchant policy must actually control eligibility decisions, not just be
displayed as text.

Root cause: check_return_eligibility() only ever consulted the structured
policy fields (return_policy_days, final_sale_tags, exclusions) — the
merchant's free-text policy notes (the "notes" field on the refund policy
form) and Knowledge Base content were either ignored entirely or loaded but
never actually enforced. A merchant whose real policy ("orders cannot be
refunded after 24 hours") lives only in free text — with the structured
return_policy_days left at its default — got that policy silently ignored:
an order 5 days old would be auto-approved as "eligible" even though the
merchant's own written policy forbids it.

Fixed with one shared choke point, ActionsManager.get_custom_policy_text()
(brand policy "notes" field, falling back to the same Knowledge Base RAG
lookup already used to answer policy questions — no separate policy
system): whenever an order would otherwise be granted automatic
eligibility, if the merchant has ANY free-text policy on file, that order
is escalated for manual human confirmation instead of auto-approved — a
deterministic checker can't reliably verify compliance with free text, so
it never guesses. When the check itself fails (Knowledge Base lookup
errors), that's treated as "unknown", not "confirmed no policy" — same
safe escalation, never guessed as eligible.

Used from both the fulfilled-order eligibility path (refund/return/
exchange) and the unfulfilled-order fast path (cancel), and reached
identically from both the chat widget and Gmail channels since both funnel
through the same return_actions_integration.handle_return_intent().
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
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


def _order(days_old=5, fulfillment_status="fulfilled"):
    created_at = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    return {
        "id": 555, "order_number": "#1001", "email": "customer@example.com",
        "created_at": created_at, "fulfillment_status": fulfillment_status, "tags": "",
        "total_price": "45.00", "currency": "USD", "refunds": [], "cancelled_at": None,
        "line_items": [{"id": 1, "product_id": 111, "title": "Essential Hoodie", "quantity": 1,
                         "price": "45.00", "sku": "EH-1", "requires_shipping": True}],
    }


async def _check_eligibility(order, brand, kb_context="", kb_raises=False):
    async def fake_get_order(order_id, email, tenant_id=None):
        return order

    async def fake_get_brand(brand_id):
        return brand

    async def fake_get_kb_context(brand_id, query, top_k=3):
        if kb_raises:
            raise RuntimeError("embeddings service unavailable")
        return kb_context

    with patch.object(actions_manager, "_get_order_from_shopify", side_effect=fake_get_order), \
         patch("src.services.actions_manager.supabase_select", return_value=[]), \
         patch("src.services.brand_manager.brand_manager.get_brand", side_effect=fake_get_brand), \
         patch("src.services.brand_knowledge_service.brand_knowledge_service.get_brand_context", side_effect=fake_get_kb_context):
        return await actions_manager.check_return_eligibility(
            "1001", "customer@example.com", tenant_id="t1", brand_id="b1",
        )


# ── 6. Refund respects a merchant policy restricting refunds after 24h ──────

def test_refund_notes_policy_blocks_auto_eligibility_even_within_structured_window():
    """Order is only 5 days old - well inside the default 30-day structured
    window - but the merchant's free-text notes say refunds are restricted
    to 24 hours. Must NOT be auto-approved as eligible."""
    order = _order(days_old=5, fulfillment_status="fulfilled")
    brand = {"id": "b1", "refund_notes": "Orders cannot be refunded after 24 hours."}
    result = run(_check_eligibility(order, brand))

    assert result["eligible"] is False
    assert result["requires_manual_review"] is True
    assert result["staging_required"] is True
    assert "24 hours" in result["custom_policy_text"]


def test_knowledge_base_policy_with_a_parseable_window_is_verified_deterministically_not_escalated():
    """Same free-text-only scenario, but the policy expresses a confidently
    parseable time window ("within 24 hours") — the Policy Evidence layer
    (src/services/policy_evidence.py) now verifies this deterministically
    against the order's real Shopify timestamp instead of blindly escalating
    every custom policy the way this test originally asserted. This order is
    5 days (120h) old, past the 24h window, so the correct result is a
    deterministic INELIGIBLE — not a human re-deriving the same arithmetic
    by hand. A policy the system can't confidently parse still escalates
    exactly as before (see test_return_notes_policy_blocks_auto_eligibility,
    which uses "after 3 days" wording — untouched)."""
    order = _order(days_old=5, fulfillment_status="fulfilled")
    brand = {"id": "b1", "refund_notes": ""}
    result = run(_check_eligibility(order, brand, kb_context="Our policy: refunds must be requested within 24 hours of delivery."))

    assert result["eligible"] is False
    assert result.get("requires_manual_review") is not True
    assert result["policy_verification"]["status"] == "INELIGIBLE"
    assert result["policy_verification"]["evidence"]["policy_window_hours"] == 24.0
    assert result["policy_verification"]["evidence"]["elapsed_hours"] > 24.0


def test_no_custom_policy_anywhere_still_grants_normal_eligibility():
    """Regression guard: a merchant with no free-text policy at all (the
    common case) must be completely unaffected - never invent a policy
    restriction that doesn't exist."""
    order = _order(days_old=5, fulfillment_status="fulfilled")
    brand = {"id": "b1", "refund_notes": ""}
    result = run(_check_eligibility(order, brand, kb_context=""))

    assert result["eligible"] is True


# ── 7. Cancellation respects merchant cancellation policy ──────────────────

def test_cancellation_of_unfulfilled_order_respects_custom_policy_notes():
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="cancel", order_id="1001", raw_address=None, confidence=0.9)
    eligibility = {
        "eligible": False, "reason": "order not yet fulfilled",
        "order": {"fulfillment_status": "unfulfilled"}, "items": [], "order_total": "45.00",
    }

    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=eligibility)), \
         patch.object(integration.actions, "get_custom_policy_text",
                       new=AsyncMock(return_value="Orders can only be cancelled within 1 hour of purchase.")), \
         patch.object(integration, "_find_active_action", new=AsyncMock(return_value=None)), \
         patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True, "action_id": "a1"})) as mock_create:
        result = run(integration.handle_return_intent(
            query="please cancel my order #1001", customer_info={"name": "Jane", "email": "jane@example.com"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1", intent_result=intent,
        ))

    mock_create.assert_awaited_once()
    assert "CANCEL QUEUED" not in result["action_context"]
    assert "MANUAL REVIEW" in result["action_context"]


def test_cancellation_with_no_custom_policy_still_auto_cancels_unfulfilled_order():
    """Regression guard: the existing full-automation behavior for a plain
    unfulfilled-order cancel (no custom merchant policy at all) must be
    unaffected."""
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="cancel", order_id="1001", raw_address=None, confidence=0.9)
    eligibility = {
        "eligible": False, "reason": "order not yet fulfilled",
        "order": {"fulfillment_status": "unfulfilled"}, "items": [], "order_total": "45.00",
    }

    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=eligibility)), \
         patch.object(integration.actions, "get_custom_policy_text", new=AsyncMock(return_value="")), \
         patch.object(integration, "_find_active_action", new=AsyncMock(return_value=None)), \
         patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True, "action_id": "a1"})) as mock_create:
        result = run(integration.handle_return_intent(
            query="please cancel my order #1001", customer_info={"name": "Jane", "email": "jane@example.com"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1", intent_result=intent,
        ))

    mock_create.assert_awaited_once()
    _, kwargs = mock_create.call_args
    assert kwargs["action_type"] == "cancel_order"
    assert "CANCEL QUEUED" in result["action_context"]


# ── 8. Return respects merchant return policy ───────────────────────────────

def test_return_notes_policy_blocks_auto_eligibility():
    order = _order(days_old=2, fulfillment_status="fulfilled")
    brand = {"id": "b1", "refund_notes": "Returns are not accepted on sale items or after 3 days."}
    result = run(_check_eligibility(order, brand))

    assert result["eligible"] is False
    assert result["requires_manual_review"] is True
    assert "3 days" in result["custom_policy_text"]


# ── 9. Chat and Gmail both reach the SAME policy-aware eligibility path ────

def test_chat_and_email_both_reach_the_same_eligibility_function():
    """Both channels call return_actions_integration.handle_return_intent(),
    which is the single place check_return_eligibility() (and therefore the
    new custom-policy check) is invoked - no separate policy system per
    channel. Proven by asserting both a chat-shaped and an email-shaped
    customer_info dict reach the identical mocked eligibility check."""
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="refund", order_id="1001", raw_address=None, confidence=0.9)
    eligible = {
        "eligible": True, "reason": "within window",
        "order": {"fulfillment_status": "fulfilled"},
        "items": [{"title": "Essential Hoodie", "variant_title": "M", "price": "45.00"}],
        "order_total": "45.00",
    }

    for channel_customer_info in (
        {"name": "Jane", "email": "jane@example.com", "channel": "chat"},
        {"name": "Jane", "email": "jane@example.com", "channel": "email"},
    ):
        with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=eligible)) as mock_elig, \
             patch.object(integration, "_find_active_action", new=AsyncMock(return_value=None)), \
             patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True, "action_id": "a1"})):
            run(integration.handle_return_intent(
                query="refund my order #1001", customer_info=channel_customer_info,
                existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1", intent_result=intent,
            ))
        mock_elig.assert_awaited_once_with("1001", "jane@example.com", tenant_id="tenant-1", brand_id="brand-1")


# ── 10. Missing/ambiguous policy fails safely instead of inventing eligibility ──

def test_knowledge_base_lookup_failure_fails_safe_not_auto_eligible():
    """The policy check itself errors (KB service unavailable) - genuinely
    unknown whether a restrictive policy exists. Must NOT be guessed as
    "no policy, fully eligible" - escalates for human confirmation."""
    order = _order(days_old=5, fulfillment_status="fulfilled")
    brand = {"id": "b1", "refund_notes": ""}
    result = run(_check_eligibility(order, brand, kb_raises=True))

    assert result["eligible"] is False
    assert result["requires_manual_review"] is True
    assert result["custom_policy_text"] is None
    assert "couldn't confirm" in result["reason"].lower()


def test_get_custom_policy_text_returns_none_on_lookup_failure_not_empty_string():
    """Direct unit test of the None vs "" distinction the safety check
    depends on."""
    async def failing_get_context(brand_id, query, top_k=3):
        raise RuntimeError("network error")

    with patch("src.services.brand_knowledge_service.brand_knowledge_service.get_brand_context", side_effect=failing_get_context):
        result = run(actions_manager.get_custom_policy_text("b1", policy_notes=""))
    assert result is None


def test_get_custom_policy_text_returns_empty_string_when_confirmed_no_policy():
    with patch("src.services.brand_knowledge_service.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value="")):
        result = run(actions_manager.get_custom_policy_text("b1", policy_notes=""))
    assert result == ""


def test_get_custom_policy_text_never_calls_knowledge_base_without_a_brand_id():
    with patch("src.services.brand_knowledge_service.brand_knowledge_service.get_brand_context", new=AsyncMock()) as mock_kb:
        result = run(actions_manager.get_custom_policy_text(None, policy_notes=""))
    mock_kb.assert_not_called()
    assert result == ""
