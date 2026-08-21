"""
Cancellation order-number extraction + routing (production bug).

Live bug: "hi cancel my order #1012" in the chat widget got the generic
"I'm unable to pull up your order... share your email or order confirmation
number?" reply, even though the customer had already given an explicit
order number. Root cause: return_actions_integration.py's shared
RETURN/REFUND/CANCEL block gated real Shopify lookup on
`not order_id or not email` - so a chat visitor who gave an order number
but (as is normal for an unverified widget session) hadn't shared an email
got the same "ask for everything" fallback as someone who'd given nothing
at all. order_id alone is enough to ATTEMPT a real lookup; email is for
ownership verification, which check_return_eligibility already performs
downstream (missing/mismatched email -> staged for manual review, never
silently treated as eligible - sender verification is preserved, not
weakened). Fixed by gating only on `not order_id`.

Also covers: real activity/progress events emitted at the actual
cancellation execution points (order lookup, eligibility check, staging),
and that policy/status phrasing sharing the word "cancel" doesn't get
misrouted into the mutation workflow.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
import json  # noqa: E402
from src.services.intent_detector import _extract_order_id, _keyword_fallback, IntentResult  # noqa: E402
from src.services.return_actions_integration import ReturnActionsIntegration, return_actions  # noqa: E402
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


# ══════════════════════════════════════════════════════════════════════════
# PART 4.1/4.2 — order-number extraction
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("text,expected", [
    ("#1012", "1012"),
    ("1012", "1012"),
    ("order #1012", "1012"),
    ("order 1012", "1012"),
    ("hi cancel my order #1012", "1012"),
])
def test_extract_order_id_recognizes_all_required_formats(text, expected):
    assert _extract_order_id(text) == expected


@pytest.mark.parametrize("query", [
    "cancel my order #1012",
    "I want to cancel order #1012",
    "can you cancel #1012",
    "please cancel order 1012",
    "cancel order 1012 please",
    "can I cancel #1012?",
])
def test_keyword_fallback_extracts_cancel_intent_and_order_id_across_phrasings(query):
    result = _keyword_fallback(query)
    assert result.action_type == "cancel"
    assert result.order_id == "1012"


def test_keyword_fallback_cancel_without_order_number_has_no_order_id():
    result = _keyword_fallback("I need to cancel my order")
    assert result.action_type == "cancel"
    assert result.order_id is None


# ══════════════════════════════════════════════════════════════════════════
# PART 3 — negative phrases must never enter the mutation workflow
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("query", [
    "what is your cancellation policy?",
    "why was my order cancelled?",
    "my order was cancelled",
    "what happens if I cancel?",
])
def test_policy_and_status_phrases_are_not_classified_as_cancel_action(query):
    result = _keyword_fallback(query)
    assert result.action_type == "none"


# ══════════════════════════════════════════════════════════════════════════
# PART 4.3-4.8 — handle_return_intent routing, eligibility, verification
# ══════════════════════════════════════════════════════════════════════════

def _eligible_unfulfilled(**overrides):
    e = {
        "eligible": False, "reason": "order not yet fulfilled",
        "order": {"fulfillment_status": "unfulfilled"},
        "items": [{"title": "Essential Hoodie", "variant_title": "M", "price": "45.00"}],
        "order_total": "45.00",
    }
    e.update(overrides)
    return e


def _run_cancel(query, order_id="1012", email="customer@example.com", eligibility=None,
                 existing_action=None, create_result=None):
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="cancel", order_id=order_id, raw_address=None, confidence=0.9)
    events = []

    async def on_progress(stage, label):
        events.append((stage, label))

    with patch.object(integration.actions, "check_return_eligibility",
                       new=AsyncMock(return_value=eligibility if eligibility is not None else _eligible_unfulfilled())) as mock_elig, \
         patch.object(integration, "_find_active_action", new=AsyncMock(return_value=existing_action)), \
         patch.object(integration, "_create_action", new=AsyncMock(return_value=create_result or {"success": True, "action_id": "a1"})) as mock_create:
        result = run(integration.handle_return_intent(
            query=query, customer_info={"name": "Jane", "email": email},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1",
            intent_result=intent, on_progress=on_progress,
        ))
    return result, mock_elig, mock_create, events


# 3. "cancel my order #1012" reaches live order lookup
def test_cancel_with_order_number_reaches_live_order_lookup():
    result, mock_elig, mock_create, _ = _run_cancel("hi cancel my order #1012")
    mock_elig.assert_awaited_once_with("1012", "customer@example.com", tenant_id="tenant-1", brand_id="brand-1")
    assert "ACTION REQUIRED: Ask customer for their order number" not in result["action_context"]


# 4. A found (unfulfilled) order reaches cancellation eligibility and is staged
def test_found_unfulfilled_order_is_staged_for_cancellation():
    result, mock_elig, mock_create, _ = _run_cancel("hi cancel my order #1012")
    mock_elig.assert_awaited_once()
    mock_create.assert_awaited_once()
    _, kwargs = mock_create.call_args
    assert kwargs["action_type"] == "cancel_order"
    assert "CANCEL QUEUED" in result["action_context"]
    assert result["staged"]["success"] is True


# 5. Unknown order is not treated as a valid order
def test_unknown_order_number_is_not_treated_as_a_valid_order():
    result, mock_elig, mock_create, _ = _run_cancel(
        "hi cancel my order #9999", order_id="9999",
        eligibility={
            "eligible": False,
            "reason": "Order #9999 was not found in our system. Our team will verify and process your request manually.",
            "order": None, "items": [], "requires_manual_review": True, "staging_required": True,
        },
    )
    # Staged for MANUAL REVIEW (a human verifies), never claimed as a real,
    # confirmed cancellation of an order that doesn't exist.
    mock_create.assert_awaited_once()
    _, kwargs = mock_create.call_args
    assert kwargs["action_type"] != "cancel_order"  # generic manual-review action, not a real Shopify cancel
    assert "MANUAL REVIEW" in result["action_context"]
    assert "CANCEL QUEUED" not in result["action_context"]


# 6. Sender/customer verification is preserved: order found by number alone
# (no email given, the real bug scenario) still requires ownership
# verification before anything is treated as eligible.
def test_order_found_by_number_without_email_still_requires_verification():
    result, mock_elig, mock_create, _ = _run_cancel(
        "hi cancel my order #1012", email="",
        eligibility={
            "eligible": False, "eligibility_verified": False,
            "reason": "sender email does not match order email on file",
            "order": {"fulfillment_status": "fulfilled"},
            "items": [{"title": "Essential Hoodie", "variant_title": "M", "price": "45.00"}],
            "requires_manual_review": True, "staging_required": True,
        },
    )
    # Real lookup DID happen (order_id alone was enough to attempt it) ...
    mock_elig.assert_awaited_once_with("1012", "", tenant_id="tenant-1", brand_id="brand-1")
    # ... but it was NOT auto-approved just because an order number was given.
    mock_create.assert_awaited_once()
    assert "MANUAL REVIEW" in result["action_context"]
    assert "not eligible" not in result["action_context"].lower()


# 7. Already-cancelled orders do not receive another cancellation action
def test_already_cancelled_order_does_not_get_another_cancel_action():
    result, mock_elig, mock_create, _ = _run_cancel(
        "hi cancel my order #1012",
        eligibility={
            "eligible": False, "reason": "This order has already been cancelled.",
            "order": {"fulfillment_status": "unfulfilled"}, "items": [],
        },
    )
    mock_create.assert_not_called()
    assert "NOT NEEDED" in result["action_context"]


def test_existing_pending_cancel_action_is_not_duplicated():
    result, mock_elig, mock_create, _ = _run_cancel(
        "hi cancel my order #1012",
        existing_action={"action_type": "cancel_order", "status": "pending"},
    )
    mock_elig.assert_not_called()
    mock_create.assert_not_called()
    assert "ALREADY PENDING" in result["action_context"]


# 8. Cancellation requiring approval remains staged, not executed
def test_cancel_remains_staged_never_claims_it_was_executed():
    result, mock_elig, mock_create, _ = _run_cancel("hi cancel my order #1012")
    context = result["action_context"]
    assert "CANCEL QUEUED" in context
    assert "cancelled successfully" not in context.lower()
    assert "has been cancelled" not in context.lower()
    assert "your order is cancelled" not in context.lower()
    # The wording tells the model to say a request was SENT, not completed.
    assert "sent your cancellation request" in context.lower()


# ══════════════════════════════════════════════════════════════════════════
# 9/10. Progress events: correct order, nothing fabricated
# ══════════════════════════════════════════════════════════════════════════

def test_progress_events_for_a_staged_cancellation_are_correct_and_in_order():
    _, _, _, events = _run_cancel("hi cancel my order #1012")
    stages = [s for s, _ in events]
    assert stages == ["order_lookup", "eligibility_check", "staging_action"]
    labels = dict(events)
    assert "Finding your order" in labels["order_lookup"]
    assert "eligibility" in labels["eligibility_check"].lower()
    assert "cancellation" in labels["staging_action"].lower() or "request" in labels["staging_action"].lower()


def test_no_staging_event_when_nothing_was_actually_staged():
    """Ineligible + fulfilled + no manual-review flag -> no action is ever
    created. The progress stream must not claim a 'preparing your request'
    step that never happened."""
    _, _, mock_create, events = _run_cancel(
        "hi cancel my order #1012",
        eligibility={
            "eligible": False, "reason": "This item is marked Final Sale.",
            "order": {"fulfillment_status": "fulfilled"}, "items": [],
        },
    )
    mock_create.assert_not_called()
    stages = [s for s, _ in events]
    assert "order_lookup" in stages
    assert "eligibility_check" in stages
    assert "staging_action" not in stages


def test_no_progress_events_at_all_when_order_id_is_missing():
    """Nothing real happens (no lookup is even attempted) - no events."""
    _, _, _, events = _run_cancel("I need to cancel my order", order_id=None)
    assert events == []


# ══════════════════════════════════════════════════════════════════════════
# PART 5 — full-pipeline reproduction of the actual reported bug
# ══════════════════════════════════════════════════════════════════════════

_FAKE_USAGE = {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}


def _fake_ai_response(reply_body: str):
    content = json.dumps({"intent": "cancellation_request", "reply_body": reply_body, "risk_level": "low"})
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def test_reported_bug_hi_cancel_my_order_1012_no_longer_falls_back_to_generic_ask():
    """The exact live interaction: 'hi cancel my order #1012'. Exercises the
    REAL pipeline - real (unmocked) intent_detector fallback (extracts
    order_id="1012" from the message itself), real return_actions_integration
    routing, with Shopify mocked at the shopify_service.get_client_for_tenant
    boundary (an order that actually exists and is unfulfilled) and only the
    final Supabase action-insert + reply-generation LLM call mocked."""
    shopify_order = {
        "id": 555, "order_number": "1012", "name": "#1012",
        "email": "customer@example.com", "fulfillment_status": "unfulfilled",
        "financial_status": "paid", "total_price": "45.00",
        "line_items": [{"id": 1, "title": "Essential Hoodie", "variant_title": "M", "price": "45.00", "quantity": 1}],
        "refunds": [], "cancelled_at": None,
    }
    mock_client = MagicMock()
    mock_client.get_order = AsyncMock(return_value={"success": True, "order": shopify_order})

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion",
               new=AsyncMock(return_value=(_fake_ai_response("On it!"), "test_provider", "test_model", _FAKE_USAGE))), \
         patch("src.agent.customer_success_agent.v3_tools.get_order_status", new=AsyncMock(return_value={"success": False})), \
         patch("src.agent.customer_success_agent.v3_tools.get_orders_by_email", new=AsyncMock(return_value={"success": False})), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value="")), \
         patch("src.lib.supabase_client.supabase_select", return_value=[]), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock(return_value=mock_client)), \
         patch.object(return_actions, "_find_active_action", new=AsyncMock(return_value=None)), \
         patch.object(return_actions, "_create_action", new=AsyncMock(return_value={"success": True, "action_id": "a1"})) as mock_create:
        result = run(customer_success_agent.process_customer_query(
            query="hi cancel my order #1012",
            customer_info={"name": "Jane", "email": ""},  # unverified chat visitor - the real bug scenario
            tenant_id="tenant-1",
            store_id="brand-1",
            ticket_id="ticket-1",
        ))

    # The order WAS found and used - a real cancellation-workflow action was
    # created, proving this never fell through to the generic "can't find
    # your order, share your email or order number" ask-again response.
    mock_client.get_order.assert_awaited_once_with("1012")
    mock_create.assert_awaited_once()
    _, kwargs = mock_create.call_args
    assert kwargs["order_id"] == "1012"
    # action_taken is the real, non-fabricated staged-action outcome
    # (customer_success_agent.py: action_taken = action_result.get("staged")).
    assert result.get("action_taken", {}).get("success") is True
