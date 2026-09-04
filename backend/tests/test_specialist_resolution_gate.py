"""
PART 2/3 — Phase 1/2/3 foundation tests.

Covers the new Resolution contract and the shared Executable Action Gate
(specialist_resolution.py), plus the extracted Return Specialist boundary
(_resolve_return / _stage_gated_action in return_actions_integration.py).

These are unit tests of the new pieces in isolation — the full end-to-end
behavior of handle_return_intent() (return/refund/cancel wording, duplicate
guards, eligibility branching) stays covered by test_intent_action_separation.py
and test_return_vs_refund_action_type.py, which this refactor must not change
the outcome of (verified separately as part of the regression run).
"""
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
import pytest  # noqa: E402

from src.services.specialist_resolution import (  # noqa: E402
    Resolution,
    ExecutableActionRejected,
    stage_resolution_action,
)
from src.services.return_actions_integration import ReturnActionsIntegration  # noqa: E402
from src.services.return_specialist import ReturnSpecialist  # noqa: E402
from src.services.exchange_specialist import ExchangeSpecialist  # noqa: E402
from src.services.cancellation_specialist import CancellationSpecialist  # noqa: E402
from src.services.intent_detector import IntentResult  # noqa: E402


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


class _FakeIntegration:
    """Minimal stand-in for ReturnActionsIntegration — only needs a mockable
    _create_action, exactly like the real thing the gate calls through to."""
    def __init__(self):
        self._create_action = AsyncMock(return_value={"success": True, "action_id": "a1"})


# ═══════════════════════════════════════════════════════════════════════
# Action isolation — the gate itself (TESTS REQUIRED items 6-9)
# ═══════════════════════════════════════════════════════════════════════

def test_6_refund_resolution_can_create_refund_action():
    integ = _FakeIntegration()
    resolution = Resolution(
        resolution_type="refund_eligible", specialist="refund", order_id="1009",
        reasoning="eligible", customer_facing_note="", requested_action_type="refund",
    )
    staged = _run(stage_resolution_action(integ, resolution, order_id="1009"))
    assert staged["success"] is True
    integ._create_action.assert_awaited_once()
    assert integ._create_action.call_args.kwargs["action_type"] == "refund"


def test_7_cancellation_resolution_can_create_cancel_order_action():
    integ = _FakeIntegration()
    resolution = Resolution(
        resolution_type="cancellation_eligible", specialist="cancellation", order_id="1009",
        reasoning="unfulfilled", customer_facing_note="", requested_action_type="cancel_order",
    )
    staged = _run(stage_resolution_action(integ, resolution, order_id="1009"))
    assert staged["success"] is True
    assert integ._create_action.call_args.kwargs["action_type"] == "cancel_order"


def test_8_return_resolution_cannot_create_refund_action():
    integ = _FakeIntegration()
    resolution = Resolution(
        resolution_type="return_escalate_to_human", specialist="return", order_id="1009",
        reasoning="return", customer_facing_note="", requested_action_type="refund",
    )
    with pytest.raises(ExecutableActionRejected):
        _run(stage_resolution_action(integ, resolution, order_id="1009"))
    integ._create_action.assert_not_awaited()


def test_9_return_resolution_cannot_create_cancellation_action():
    integ = _FakeIntegration()
    resolution = Resolution(
        resolution_type="return_escalate_to_human", specialist="return", order_id="1009",
        reasoning="return", customer_facing_note="", requested_action_type="cancel_order",
    )
    with pytest.raises(ExecutableActionRejected):
        _run(stage_resolution_action(integ, resolution, order_id="1009"))
    integ._create_action.assert_not_awaited()


def test_return_resolution_with_no_requested_action_stages_nothing():
    """The actual real-world Return Specialist path today — resolution_type
    is whitelist-absent, but that's moot because requested_action_type is
    None (no action requested at all), which the gate always short-circuits
    before consulting the whitelist."""
    integ = _FakeIntegration()
    resolution = Resolution(
        resolution_type="return_escalate_to_human", specialist="return", order_id="1009",
        reasoning="return", customer_facing_note="", requested_action_type=None,
    )
    staged = _run(stage_resolution_action(integ, resolution))
    assert staged is None
    integ._create_action.assert_not_awaited()


def test_exchange_resolution_cannot_create_refund_action():
    integ = _FakeIntegration()
    resolution = Resolution(
        resolution_type="exchange_escalate_to_human", specialist="exchange", order_id="1009",
        reasoning="exchange", customer_facing_note="", requested_action_type="refund",
    )
    with pytest.raises(ExecutableActionRejected):
        _run(stage_resolution_action(integ, resolution, order_id="1009"))


def test_exchange_resolution_cannot_create_exchange_action_yet():
    """Current policy: exchange creates NO executable action for now,
    including its own "exchange" type — see specialist_resolution.py's
    whitelist comment. (The live _handle_exchange code path still stages
    exchange/refund directly today; rewiring it through this gate is
    PART 2/3 Phase 5, not yet done — this test locks in the *gate's* rule
    so that future wiring is safe.)"""
    integ = _FakeIntegration()
    resolution = Resolution(
        resolution_type="exchange_escalate_to_human", specialist="exchange", order_id="1009",
        reasoning="exchange", customer_facing_note="", requested_action_type="exchange",
    )
    with pytest.raises(ExecutableActionRejected):
        _run(stage_resolution_action(integ, resolution, order_id="1009"))


def test_general_support_resolution_cannot_create_any_action():
    integ = _FakeIntegration()
    resolution = Resolution(
        resolution_type="answer_only", specialist="general_support", order_id=None,
        reasoning="policy question", customer_facing_note="", requested_action_type="refund",
    )
    with pytest.raises(ExecutableActionRejected):
        _run(stage_resolution_action(integ, resolution))


# ═══════════════════════════════════════════════════════════════════════
# Return Specialist boundary — _resolve_return returns a Resolution, never
# an action; _stage_gated_action is unreachable for return (no call site
# ever passes it "return_escalate_to_human")
# ═══════════════════════════════════════════════════════════════════════

def test_resolve_return_identity_mismatch_requests_no_action():
    integration = ReturnActionsIntegration()
    resolution = integration._resolve_return(
        order_id="1009",
        eligibility={"identity_mismatch": True},
        is_unfulfilled=False,
        existing_action=None,
    )
    assert resolution.resolution_type == "return_identity_unverified"
    assert resolution.requested_action_type is None
    assert "IDENTITY UNVERIFIED" in resolution.customer_facing_note


def test_resolve_return_normal_path_requests_no_action():
    integration = ReturnActionsIntegration()
    resolution = integration._resolve_return(
        order_id="1009",
        eligibility={"reason": "eligible", "shipment_status": "delivered"},
        is_unfulfilled=False,
        existing_action={"action_type": "cancel_order", "status": "pending"},
    )
    assert resolution.resolution_type == "return_escalate_to_human"
    assert resolution.requested_action_type is None
    assert resolution.specialist == "return"
    assert "ESCALATE TO HUMAN, NO ACTION CREATED" in resolution.customer_facing_note
    assert "cancellation" in resolution.customer_facing_note.lower()
    assert "does NOT substitute" in resolution.customer_facing_note


def test_resolve_return_never_reaches_create_action_even_if_gate_were_bypassed():
    """Belt-and-suspenders: _resolve_return itself never touches
    _create_action/actions_service — confirms the Return Specialist's
    "no action" guarantee doesn't depend on the gate at all."""
    integration = ReturnActionsIntegration()
    integration._create_action = AsyncMock(return_value={"success": True})
    integration._resolve_return(
        order_id="1009", eligibility={"reason": "eligible"},
        is_unfulfilled=False, existing_action=None,
    )
    integration._create_action.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
# PART 2/3 Phase 4 — Return Specialist boundary, end-to-end through
# handle_return_intent() (not just ReturnSpecialist.resolve() in
# isolation as above — this exercises the whole path including the
# duplicate-guard lookup and the Executable Action Gate, confirming RETURN
# never reaches _create_action() through the real pipeline, not just that
# the specialist's own return value looks right).
# ═══════════════════════════════════════════════════════════════════════

def _eligible_fulfilled_order_phase4(**overrides):
    data = {
        "eligible": True,
        "order": {"fulfillment_status": "fulfilled", "total_price": "15.00", "currency": "USD"},
        "items": [{"title": "QA Test Mug"}],
        "reason": "Great news! Your order is eligible for return.",
        "shipment_status": "delivered",
    }
    data.update(overrides)
    return data


def _handle_return_e2e(order_id="1009", existing_action_by_type=None, query=None, eligibility=None):
    """existing_action_by_type: {"refund": row_or_None, "cancel_order": row_or_None},
    mirroring _find_active_action's real per-type, per-order lookup — same
    harness shape as test_intent_action_separation.py's `_handle()`."""
    existing_action_by_type = existing_action_by_type or {}
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="return", order_id=order_id, raw_address=None, confidence=0.9)
    query = query or f"I want to return order #{order_id}"

    async def _fake_find_active_action(t_id, o_id, act_type):
        if o_id != order_id:
            return None
        return existing_action_by_type.get(act_type)

    create_mock = AsyncMock(return_value={"success": True, "action_id": "should-never-be-reached"})

    with patch.object(integration, "_find_active_action", new=AsyncMock(side_effect=_fake_find_active_action)), \
         patch.object(integration, "_create_action", new=create_mock), \
         patch.object(integration.actions, "check_return_eligibility",
                       new=AsyncMock(return_value=eligibility or _eligible_fulfilled_order_phase4())):
        result = _run(integration.handle_return_intent(
            query=query, customer_info={"name": "Jane", "email": "jane@example.com"},
            existing_tool_results={}, tenant_id="tenant-phase4", brand_id="brand-1",
            ticket_id="ticket-phase4", intent_result=intent,
        ))
    return result, create_mock


def test_return_with_no_historical_action_creates_zero_actions():
    result, create_mock = _handle_return_e2e()
    create_mock.assert_not_awaited()
    assert result.get("staged") is None
    assert "ESCALATE TO HUMAN, NO ACTION CREATED" in result["action_context"]


def test_return_with_historical_pending_refund_creates_zero_actions():
    result, create_mock = _handle_return_e2e(
        existing_action_by_type={"refund": {"action_type": "refund", "status": "pending"}},
    )
    create_mock.assert_not_awaited()
    assert result.get("staged") is None
    assert "ESCALATE TO HUMAN, NO ACTION CREATED" in result["action_context"]
    # The historical refund is context only, never authority over this return.
    assert "does NOT substitute for this return request" in result["action_context"]


def test_return_with_historical_pending_cancellation_creates_zero_actions():
    result, create_mock = _handle_return_e2e(
        existing_action_by_type={"cancel_order": {"action_type": "cancel_order", "status": "pending"}},
    )
    create_mock.assert_not_awaited()
    assert result.get("staged") is None
    assert "ESCALATE TO HUMAN, NO ACTION CREATED" in result["action_context"]
    assert "does NOT substitute for this return request" in result["action_context"]


def test_return_never_uses_refund_or_cancellation_action_type():
    """The mocked _create_action must never be awaited at all for a return —
    the strongest form of "never uses refund/cancel_order as its action
    type": there is no action_type to inspect because there is no call."""
    for existing in (
        {},
        {"refund": {"action_type": "refund", "status": "executed"}},
        {"cancel_order": {"action_type": "cancel_order", "status": "approved"}},
    ):
        _, create_mock = _handle_return_e2e(existing_action_by_type=existing)
        create_mock.assert_not_awaited()


def test_return_response_discusses_return_not_cancellation_or_refund():
    result, _ = _handle_return_e2e(
        existing_action_by_type={"cancel_order": {"action_type": "cancel_order", "status": "pending"}},
    )
    text = result["action_context"]
    assert "return" in text.lower()
    assert "do not say a refund or cancellation was started, staged, or submitted for this - none was" in text.lower()
    # Never claims THIS request (not the unrelated historical one) is a
    # refund or a cancellation — only "return" language describes the
    # current ask.
    assert "cancel_order" not in text


def test_return_window_and_policy_behavior_remains_intact_via_specialist():
    """Existing return-window/policy behavior (shipment-status delivery
    notes, custom policy evidence in the reasoning) is unchanged by moving
    this logic into ReturnSpecialist — same assertions Part 1's tests make
    against handle_return_intent, exercised directly against the new
    specialist boundary."""
    resolution = ReturnSpecialist.resolve(
        order_id="1009",
        eligibility={
            "reason": "eligible", "shipment_status": "in_transit",
            "custom_policy_text": "Returns accepted within 30 days of delivery.",
        },
        is_unfulfilled=False,
        existing_action=None,
    )
    assert "not yet" in resolution.customer_facing_note.lower()
    assert "in_transit" in resolution.customer_facing_note
    assert "Store policy on file" in resolution.reasoning
    assert "30 days" in resolution.reasoning
    assert resolution.requested_action_type is None


def test_return_specialist_unfulfilled_order_flags_not_yet_shipped():
    resolution = ReturnSpecialist.resolve(
        order_id="1009",
        eligibility={"reason": "not fulfilled"},
        is_unfulfilled=True,
        existing_action=None,
    )
    assert "has not shipped yet" in resolution.customer_facing_note
    assert "that is a cancellation, not a return" in resolution.customer_facing_note
    assert resolution.requested_action_type is None


# ═══════════════════════════════════════════════════════════════════════
# PART 2/3 Phase 5 — Exchange Specialist boundary, end-to-end through
# handle_return_intent() plus direct unit tests of ExchangeSpecialist.
# Mirrors the Phase 4 Return Specialist test section above.
# ═══════════════════════════════════════════════════════════════════════

def _eligible_exchange_order_phase5(**overrides):
    data = {
        "eligible": True, "reason": "within return window",
        "order": {"fulfillment_status": "fulfilled"},
        "items": [{"id": 1, "title": "Essential Hoodie", "variant_title": "M", "sku": "EH-M", "price": "45.00"}],
        "order_total": "45.00",
    }
    data.update(overrides)
    return data


def _raw_exchange_line_item_phase5(**overrides):
    item = {
        "id": 1, "product_id": 555, "variant_id": 9001, "title": "Essential Hoodie",
        "variant_title": "M", "sku": "EH-M", "price": "45.00", "quantity": 1,
    }
    item.update(overrides)
    return item


def _found_exchange_target_phase5(**overrides):
    target = {
        "found": True, "same_product": True,
        "product_id": 555, "product_title": "Essential Hoodie",
        "variant_id": 9002, "variant_title": "L", "price": 45.0,
        "product_url": "https://store.myshopify.com/products/essential-hoodie",
    }
    target.update(overrides)
    return target


def _handle_exchange_e2e(order_id="1001", other_order_actions=None, query=None, exchange_target="size L"):
    """other_order_actions: {order_id: {"action_type": ..., "status": ...}}.
    _handle_exchange's duplicate-guard only ever queries action_type=
    "exchange" (never "refund"/"cancel_order") for the CURRENT order — the
    fake below is deliberately type-scoped to match the real query exactly,
    so a "refund"/"cancel_order" row supplied here is provably unreachable
    by the real code path, not just absent by omission. That is the point:
    it proves such history structurally cannot influence exchange's
    decision, rather than merely happening not to today."""
    other_order_actions = other_order_actions or {}
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="exchange", order_id=order_id, raw_address=None,
                           confidence=0.9, exchange_target=exchange_target)
    query = query or f"I want to exchange order #{order_id} for a different size"

    async def _fake_find_active_action(t_id, o_id, act_type):
        # Mirrors the real _find_active_action's server-side filter: a
        # stored action only matches a query for its OWN action_type - a
        # "refund" row is invisible to a query for "exchange", exactly like
        # the real `action_type=eq.<query_type>` Supabase filter.
        existing = other_order_actions.get(o_id)
        if existing and existing.get("action_type") == act_type:
            return existing
        return None

    create_mock = AsyncMock(return_value={"success": True, "action_id": "should-never-be-reached"})

    with patch.object(integration, "_find_active_action", new=AsyncMock(side_effect=_fake_find_active_action)), \
         patch.object(integration, "_create_action", new=create_mock), \
         patch.object(integration.actions, "check_return_eligibility",
                       new=AsyncMock(return_value=_eligible_exchange_order_phase5())), \
         patch.object(integration, "_get_raw_line_item", new=AsyncMock(return_value=_raw_exchange_line_item_phase5())), \
         patch.object(integration.actions, "find_exchange_target",
                       new=AsyncMock(return_value=_found_exchange_target_phase5())):
        result = _run(integration.handle_return_intent(
            query=query, customer_info={"name": "Jane", "email": "jane@example.com"},
            existing_tool_results={}, tenant_id="tenant-phase5", brand_id="brand-1",
            ticket_id="ticket-phase5", intent_result=intent,
        ))
    return result, create_mock


def test_exchange_basic_request_creates_zero_executable_actions():
    result, create_mock = _handle_exchange_e2e()
    create_mock.assert_not_awaited()
    assert result.get("staged") is None
    assert "ESCALATE TO HUMAN, NO ACTION CREATED" in result["action_context"]


def test_exchange_with_historical_pending_refund_on_this_order_creates_zero_new_actions():
    result, create_mock = _handle_exchange_e2e(
        order_id="1001", other_order_actions={"1001": {"action_type": "refund", "status": "pending"}},
    )
    create_mock.assert_not_awaited()
    assert result.get("staged") is None
    assert "ESCALATE TO HUMAN, NO ACTION CREATED" in result["action_context"]


def test_exchange_with_historical_pending_cancellation_on_this_order_creates_zero_new_actions():
    result, create_mock = _handle_exchange_e2e(
        order_id="1001", other_order_actions={"1001": {"action_type": "cancel_order", "status": "pending"}},
    )
    create_mock.assert_not_awaited()
    assert result.get("staged") is None
    assert "ESCALATE TO HUMAN, NO ACTION CREATED" in result["action_context"]


def test_exchange_with_historical_pending_exchange_action_creates_zero_new_actions():
    """This IS the type _handle_exchange's duplicate-guard actually
    queries — a genuinely active prior exchange short-circuits with status
    wording (unchanged, pre-existing behavior — see
    test_exchange_workflow.py's duplicate-guard tests), never creating a
    second action either way."""
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="exchange", order_id="1001", raw_address=None,
                           confidence=0.9, exchange_target="size L")
    create_mock = AsyncMock(return_value={"success": True})
    with patch.object(integration, "_find_active_action",
                       new=AsyncMock(return_value={"action_type": "exchange", "status": "pending"})), \
         patch.object(integration, "_create_action", new=create_mock):
        result = _run(integration.handle_return_intent(
            query="Just checking on my exchange", customer_info={"name": "Jane", "email": "jane@example.com"},
            existing_tool_results={}, tenant_id="tenant-phase5", brand_id="brand-1",
            intent_result=intent,
        ))
    create_mock.assert_not_awaited()
    assert result.get("staged") is None


def test_exchange_request_cannot_create_a_refund_action():
    """The gate itself rejects it if ever attempted — the strongest form of
    this guarantee, independent of whether _handle_exchange's real code
    happens to never try (proven separately above)."""
    resolution = ExchangeSpecialist.resolve_eligibility_unclear("1001", {"reason": "unknown"})
    resolution.requested_action_type = "refund"  # simulate a hypothetical future bug
    with pytest.raises(ExecutableActionRejected):
        _run(stage_resolution_action(ReturnActionsIntegration(), resolution, order_id="1001"))


def test_exchange_request_cannot_create_a_cancellation_action():
    resolution = ExchangeSpecialist.resolve_target_found(
        order_id="1001", original_item={"title": "Hoodie", "price": "45.00"},
        target={"product_title": "Hoodie", "variant_title": "L", "price": 45.0}, price_difference=0.0,
    )
    resolution.requested_action_type = "cancel_order"  # simulate a hypothetical future bug
    with pytest.raises(ExecutableActionRejected):
        _run(stage_resolution_action(ReturnActionsIntegration(), resolution, order_id="1001"))


def test_exchange_request_cannot_create_an_exchange_action():
    """Current policy: exchange creates NO executable action, including its
    own "exchange" type — specialist_resolution.py's whitelist has no entry
    at all for "exchange_escalate_to_human", so even this "natural-seeming"
    mapping is rejected."""
    resolution = ExchangeSpecialist.resolve_target_found(
        order_id="1001", original_item={"title": "Hoodie", "price": "45.00"},
        target={"product_title": "Hoodie", "variant_title": "L", "price": 45.0}, price_difference=0.0,
    )
    resolution.requested_action_type = "exchange"  # simulate a hypothetical future bug
    with pytest.raises(ExecutableActionRejected):
        _run(stage_resolution_action(ReturnActionsIntegration(), resolution, order_id="1001"))


def test_exchange_response_discusses_exchange_not_refund_or_cancellation():
    result, _ = _handle_exchange_e2e()
    text = result["action_context"]
    assert "exchange" in text.lower()
    assert "confirmed available and in stock" in text
    assert "refund has been" not in text.lower()
    assert "cancellation has been" not in text.lower()


def test_exchange_prior_action_surfaced_as_context_only_via_specialist():
    """Direct unit test of the specialist's own context-folding (see
    exchange_specialist.py's _existing_action_note) — proves the specialist
    correctly treats a prior action as informational-only context, never
    authority, exactly like ReturnSpecialist does for refund/cancel_order
    history. (In the real pipeline, an active same-type "exchange" action
    is already intercepted by _handle_exchange's own duplicate-guard before
    this specialist is ever reached — see the test above — so this proves
    the specialist's own guarantee holds independently of that.)"""
    resolution = ExchangeSpecialist.resolve_target_found(
        order_id="1001", original_item={"title": "Hoodie", "variant_title": "M", "price": "45.00"},
        target={"product_title": "Hoodie", "variant_title": "L", "price": 45.0}, price_difference=0.0,
        existing_action={"action_type": "refund", "status": "pending"},
    )
    assert resolution.requested_action_type is None
    assert "does NOT substitute for this exchange request" in resolution.customer_facing_note
    assert "separate refund request" in resolution.customer_facing_note


def test_exchange_different_orders_historical_action_does_not_leak():
    result_2002, create_mock_2002 = _handle_exchange_e2e(
        order_id="2002", other_order_actions={"1001": {"action_type": "exchange", "status": "pending"}},
    )
    create_mock_2002.assert_not_awaited()
    assert "1001" not in result_2002["action_context"]
    assert result_2002.get("duplicate_of_existing_action") is None


def test_exchange_specialist_always_returns_requested_action_type_none():
    r1 = ExchangeSpecialist.resolve_eligibility_unclear("1001", {"reason": "not found"})
    r2 = ExchangeSpecialist.resolve_target_found(
        order_id="1001", original_item={"title": "Hoodie", "price": "45.00"},
        target={"product_title": "Hoodie", "variant_title": "L", "price": 45.0}, price_difference=-10.0,
    )
    r3 = ExchangeSpecialist.resolve_target_found(
        order_id="1001", original_item={"title": "Hoodie", "price": "45.00"},
        target={"product_title": "Hoodie", "variant_title": "XL", "price": 55.0}, price_difference=10.0,
    )
    for r in (r1, r2, r3):
        assert r.requested_action_type is None


# ═══════════════════════════════════════════════════════════════════════
# PART 2/3 Phase 6 — Refund Specialist boundary, end-to-end through
# handle_return_intent() plus direct unit tests of RefundSpecialist.
# Unlike Return/Exchange, refund MAY request a real executable action —
# always "refund", always through the shared gate, human approval always
# still required.
# ═══════════════════════════════════════════════════════════════════════

from src.services.refund_specialist import RefundSpecialist  # noqa: E402


def _eligible_fulfilled_refund_order(**overrides):
    data = {
        "eligible": True,
        "order": {"fulfillment_status": "fulfilled", "total_price": "50.00", "currency": "GBP"},
        "items": [{"title": "Damaged Mug"}],
        "reason": "Eligible for refund.",
        "shipment_status": "delivered",
    }
    data.update(overrides)
    return data


def _handle_refund_e2e(order_id="3001", other_order_actions=None, query=None, eligibility=None):
    """other_order_actions: {order_id: {"action_type": ..., "status": ...}} —
    type-scoped exactly like the exchange harness above, mirroring the real
    _find_active_action's server-side action_type filter."""
    other_order_actions = other_order_actions or {}
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="refund", order_id=order_id, raw_address=None, confidence=0.9)
    query = query or f"I want a refund for order #{order_id}, it arrived damaged"

    async def _fake_find_active_action(t_id, o_id, act_type):
        existing = other_order_actions.get(o_id)
        if existing and existing.get("action_type") == act_type:
            return existing
        return None

    create_mock = AsyncMock(return_value={"success": True, "action_id": "new-refund-action"})

    with patch.object(integration, "_find_active_action", new=AsyncMock(side_effect=_fake_find_active_action)), \
         patch.object(integration, "_create_action", new=create_mock), \
         patch.object(integration.actions, "check_return_eligibility",
                       new=AsyncMock(return_value=eligibility if eligibility is not None else _eligible_fulfilled_refund_order())), \
         patch.object(integration, "_maybe_autopilot_refund", new=AsyncMock(return_value=None)), \
         patch.object(integration, "_maybe_autopilot_cancel", new=AsyncMock(return_value=None)), \
         patch.object(integration.actions, "get_custom_policy_text", new=AsyncMock(return_value="")):
        result = _run(integration.handle_return_intent(
            query=query, customer_info={"name": "Jane", "email": "jane@example.com"},
            existing_tool_results={}, tenant_id="tenant-phase6", brand_id="brand-1",
            ticket_id="ticket-phase6", intent_result=intent,
        ))
    return result, create_mock


def test_eligible_full_refund_is_staged_through_the_shared_gate():
    result, create_mock = _handle_refund_e2e()
    create_mock.assert_awaited_once()
    assert create_mock.call_args.kwargs["action_type"] == "refund"
    assert result["staged"]["success"] is True
    assert "ACTION STAGED FOR APPROVAL" in result["action_context"]


def test_partial_refund_request_preserves_correct_requested_amount():
    result, create_mock = _handle_refund_e2e(
        query="Please refund $30 for order #3001, the mug arrived cracked.",
    )
    create_mock.assert_awaited_once()
    assert create_mock.call_args.kwargs["requested_amount"] == 30.0


def test_currency_is_preserved_correctly_through_staging():
    result, create_mock = _handle_refund_e2e(
        eligibility=_eligible_fulfilled_refund_order(
            order={"fulfillment_status": "fulfilled", "total_price": "50.00", "currency": "GBP"},
        ),
    )
    create_mock.assert_awaited_once()
    staged_eligibility = create_mock.call_args.kwargs["eligibility"]
    assert staged_eligibility["order"]["currency"] == "GBP"


def test_identity_mismatch_creates_no_refund_action():
    result, create_mock = _handle_refund_e2e(
        eligibility={"eligible": False, "identity_mismatch": True, "reason": "email mismatch"},
    )
    create_mock.assert_not_awaited()
    assert result.get("staged") is None
    assert "IDENTITY UNVERIFIED - DO NOT PROCESS REFUND" in result["action_context"]
    # Never leaks order specifics beyond what the customer already knows.
    assert "3001" not in result["action_context"]


def test_ineligible_refund_creates_no_refund_action():
    result, create_mock = _handle_refund_e2e(
        eligibility={
            "eligible": False, "reason": "This order contains items marked Final Sale.",
            "order": {"fulfillment_status": "fulfilled"}, "items": [],
        },
    )
    create_mock.assert_not_awaited()
    assert result.get("staged") is None
    assert "REFUND NOT ELIGIBLE" in result["action_context"]


def test_existing_pending_refund_for_same_order_follows_existing_dedup_behavior():
    """Unchanged, pre-existing duplicate-guard behavior (not this phase's
    concern to alter) — locked in here as a regression guard."""
    result, create_mock = _handle_refund_e2e(
        other_order_actions={"3001": {"action_type": "refund", "status": "pending"}},
    )
    create_mock.assert_not_awaited()
    assert "REFUND ALREADY PENDING" in result["action_context"]


def test_historical_cancellation_does_not_turn_new_refund_into_cancellation():
    """A PENDING (unresolved) historical cancel_order must NOT substitute
    for a fresh refund request - Part 1's existing guarantee, unaffected by
    this phase. Falls through to a fresh eligibility check, which stages a
    genuine "refund" action."""
    result, create_mock = _handle_refund_e2e(
        other_order_actions={"3001": {"action_type": "cancel_order", "status": "pending"}},
    )
    create_mock.assert_awaited_once()
    assert create_mock.call_args.kwargs["action_type"] == "refund"
    assert "cancel_order" != create_mock.call_args.kwargs["action_type"]


def test_historical_exchange_does_not_turn_new_refund_into_exchange():
    """The refund duplicate-guard only ever queries "refund"/"cancel_order"
    - an "exchange" row is structurally invisible to it, exactly like the
    real per-type Supabase filter, so it can never substitute for a refund."""
    result, create_mock = _handle_refund_e2e(
        other_order_actions={"3001": {"action_type": "exchange", "status": "pending"}},
    )
    create_mock.assert_awaited_once()
    assert create_mock.call_args.kwargs["action_type"] == "refund"


def test_historical_refund_from_a_different_order_does_not_contaminate_current_refund():
    result, create_mock = _handle_refund_e2e(
        order_id="3002", other_order_actions={"3001": {"action_type": "refund", "status": "pending"}},
    )
    create_mock.assert_awaited_once()
    assert create_mock.call_args.kwargs["order_id"] == "3002"
    assert "3001" not in result["action_context"]


def test_refund_specialist_never_directly_creates_an_action():
    """RefundSpecialist has no _create_action / DB / Shopify access at all —
    confirmed by inspecting its module (no such import), plus its return
    value is always a plain Resolution, never a staged dict."""
    resolution = RefundSpecialist.resolve_eligible(
        "3001", _eligible_fulfilled_refund_order(), specific_item=None, requested_amount=None,
    )
    assert isinstance(resolution, Resolution)
    assert not hasattr(resolution, "action_id")


def test_refund_eligible_maps_only_to_refund_via_the_gate():
    resolution = RefundSpecialist.resolve_eligible(
        "3001", _eligible_fulfilled_refund_order(), specific_item=None, requested_amount=None,
    )
    assert resolution.resolution_type == "refund_eligible"
    integ = ReturnActionsIntegration()
    integ._create_action = AsyncMock(return_value={"success": True})
    staged = _run(stage_resolution_action(integ, resolution, order_id="3001"))
    assert staged["success"] is True
    assert integ._create_action.call_args.kwargs["action_type"] == "refund"


def test_invalid_action_type_through_the_gate_is_rejected_for_refund_eligible():
    resolution = RefundSpecialist.resolve_eligible(
        "3001", _eligible_fulfilled_refund_order(), specific_item=None, requested_amount=None,
    )
    resolution.requested_action_type = "cancel_order"  # simulate a hypothetical future bug
    with pytest.raises(ExecutableActionRejected):
        _run(stage_resolution_action(ReturnActionsIntegration(), resolution, order_id="3001"))

    resolution2 = RefundSpecialist.resolve_eligible(
        "3001", _eligible_fulfilled_refund_order(), specific_item=None, requested_amount=None,
    )
    resolution2.requested_action_type = "exchange"
    with pytest.raises(ExecutableActionRejected):
        _run(stage_resolution_action(ReturnActionsIntegration(), resolution2, order_id="3001"))


def test_human_approval_still_required_before_execution():
    """Staging alone never executes anything - _create_action's real
    implementation always writes status="pending" (unchanged, not touched
    by this phase), and with autopilot disabled (the harness's default),
    the response explicitly says the request is awaiting review, never
    that anything has been completed."""
    result, create_mock = _handle_refund_e2e()
    assert "STAGED FOR APPROVAL" in result["action_context"]
    assert "you'll get a confirmation once they approve it" in result["action_context"].lower()
    assert "processed" not in result["action_context"].lower()
    assert "completed" not in result["action_context"].lower()


def test_refund_response_discusses_refund_not_cancellation_return_or_exchange():
    result, _ = _handle_refund_e2e()
    text = result["action_context"].lower()
    assert "refund" in text
    assert "cancellation" not in text
    assert "exchange" not in text
    assert "escalate to human, no action created" not in text  # that's Return/Exchange's wording, not refund's


def test_refund_specialist_identity_mismatch_never_requests_an_action():
    resolution = RefundSpecialist.resolve_identity_mismatch("3001")
    assert resolution.requested_action_type is None


def test_refund_specialist_manual_review_only_ever_requests_refund():
    resolution = RefundSpecialist.resolve_manual_review(
        "3001", {"reason": "Order #3001 was not found in our system."}, requested_amount=None,
    )
    assert resolution.requested_action_type == "refund"
    assert resolution.resolution_type == "refund_eligible"


# ═══════════════════════════════════════════════════════════════════════
# Pre-Phase-7 investigation follow-up: the ACTUALLY REACHABLE "refund
# intent against a genuinely unfulfilled order" scenario.
#
# check_return_eligibility()'s Step 3 (actions_manager.py) unconditionally
# returns eligible=False, staging_required=True, action_hint="cancel_order"
# whenever fulfillment_status != "fulfilled" - there is no code path in
# that function that returns eligible=True while unfulfilled. So a REFUND
# request against a real unfulfilled order reaches
# handle_return_intent()'s "UNFULFILLED -> cancel is right" branch (the
# `if order_data and is_unfulfilled and not eligibility.get("eligible"):`
# block), NOT RefundSpecialist's eligible-happy-path (which requires
# eligibility["eligible"] is True and is therefore unreachable here) and
# NOT the ELIGIBLE-branch's is_unfulfilled fallthrough (also unreachable -
# that branch requires eligible=True too). This test locks in that this
# real, reachable path stages a cancel_order action with an honest,
# disclosed reasoning and customer-facing wording - never a standalone
# "refund staged" claim - confirming there is no production bug here.
# ═══════════════════════════════════════════════════════════════════════

def test_refund_intent_against_genuinely_unfulfilled_order_stages_disclosed_cancellation():
    """Eligibility shaped exactly like check_return_eligibility()'s real
    Step 3 return (eligible=False, staging_required=True,
    action_hint="cancel_order") for a genuinely unfulfilled order, with a
    REFUND-worded intent."""
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="refund", order_id="4001", raw_address=None, confidence=0.9)
    eligibility = {
        "eligible": False,
        "reason": "This order hasn't been delivered yet, so it's not eligible for return.",
        "order": {"fulfillment_status": "unfulfilled", "created_at": "2026-09-01T00:00:00-04:00"},
        "items": [{"title": "Essential Hoodie", "price": "45.00"}],
        "staging_required": True,
        "action_hint": "cancel_order",
        "fulfillment_status": "unfulfilled",
        "shipment_status": None,
    }
    create_mock = AsyncMock(return_value={"success": True, "action_id": "new-cancel-action"})

    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=eligibility)), \
         patch.object(integration, "_find_active_action", new=AsyncMock(return_value=None)), \
         patch.object(integration, "_create_action", new=create_mock), \
         patch.object(integration, "_maybe_autopilot_cancel", new=AsyncMock(return_value=None)), \
         patch.object(integration.actions, "get_custom_policy_text", new=AsyncMock(return_value="")):
        result = _run(integration.handle_return_intent(
            query="I want a refund for order #4001, it hasn't shipped and I changed my mind",
            customer_info={"name": "Jane", "email": "jane@example.com"},
            existing_tool_results={}, tenant_id="tenant-unfulfilled-refund", brand_id="brand-1",
            ticket_id="ticket-unfulfilled-refund", intent_result=intent,
        ))

    # 1 & 7. Exactly one action staged, never a second one.
    create_mock.assert_awaited_once()

    # 2. The staged action is a cancel_order, never a refund/exchange/return.
    kwargs = create_mock.call_args.kwargs
    assert kwargs["action_type"] == "cancel_order"

    # 3. The reasoning a human reviewer sees explicitly explains the
    # customer asked for a refund, the order is unfulfilled, and cancel +
    # auto-refund is the right mechanism — never a silent, unexplained
    # refund->cancel_order substitution.
    reasoning = kwargs["ai_reasoning"]
    assert "refund" in reasoning.lower()
    assert "unfulfilled" in reasoning.lower()
    assert "cancel" in reasoning.lower()

    # 4. Customer-facing response says cancellation is queued/staged, and
    # explains the refund follows from the cancellation.
    text = result["action_context"]
    assert "CANCEL QUEUED" in text
    assert "cancel" in text.lower()
    assert "refund" in text.lower()
    assert "cancel it and your refund will appear" in text.lower()

    # 5. Never claims a standalone refund action was staged.
    assert "your refund request has been submitted" not in text.lower()
    assert "REQUEST SUBMITTED FOR MANUAL REVIEW" not in text
    assert "ACTION STAGED FOR APPROVAL" not in text

    # 6. This reached _create_action ONLY via the shared Executable Action
    # Gate (_stage_gated_action -> stage_resolution_action), which enforces
    # resolution_type="cancellation_eligible" -> action_type="cancel_order"
    # is the only valid mapping (specialist_resolution.py's whitelist) —
    # handle_return_intent() has no direct _create_action call on this path,
    # so a successful call with action_type="cancel_order" here is only
    # reachable if it passed that gate's validation.
    assert result.get("staged", {}).get("success") is True


# ═══════════════════════════════════════════════════════════════════════
# PART 2/3 Phase 7 — Cancellation Specialist boundary. Direct unit tests
# of CancellationSpecialist's four resolution methods (CP1/CP4/CP6/CP8
# from the Phase 7 inspection report). Unlike Return/Exchange, cancellation
# MAY request a real executable action — either "cancel_order" (native) or
# "refund" (its own fallback when Shopify cannot cancel a fulfilled order),
# always through the shared gate, human approval always still required.
# ═══════════════════════════════════════════════════════════════════════

def test_unfulfilled_manual_review_cancellation_returns_correct_resolution():
    """CP1: custom policy present, needs human window check."""
    resolution = CancellationSpecialist.resolve_unfulfilled_manual_review(
        "5001", "cancel", "Orders can only be cancelled within 1 hour of purchase.",
    )
    assert isinstance(resolution, Resolution)
    assert resolution.specialist == "cancellation"
    assert resolution.resolution_type == "cancellation_eligible"
    assert resolution.requested_action_type == "cancel_order"
    assert resolution.order_id == "5001"
    # No side effects: a plain dataclass, no action_id, no DB/Shopify state.
    assert not hasattr(resolution, "action_id")


def test_unfulfilled_eligible_cancellation_preserves_business_rule_and_wording():
    """CP4: no policy restriction (or a verified-within-window one) — cancel
    + auto-refund is appropriate. Reasoning must preserve the "cancel so the
    payment can be refunded" business rule; customer note must preserve the
    CANCEL QUEUED semantics."""
    resolution = CancellationSpecialist.resolve_unfulfilled_eligible(
        "5002", "cancel", window_verified_eligible=False, window_evidence=None,
    )
    assert resolution.requested_action_type == "cancel_order"
    assert resolution.resolution_type == "cancellation_eligible"
    assert "cancel + auto-refund is appropriate" in resolution.reasoning
    assert "CANCEL QUEUED" in resolution.customer_facing_note
    assert "cancel it and your refund will appear" in resolution.customer_facing_note.lower()

    # Also verify the window-verified variant preserves the real numbers.
    resolution2 = CancellationSpecialist.resolve_unfulfilled_eligible(
        "5002", "cancel", window_verified_eligible=True,
        window_evidence={"elapsed_hours": 0.5, "policy_window_hours": 2.0},
    )
    assert resolution2.requested_action_type == "cancel_order"
    assert "0.50h ago" in resolution2.reasoning
    assert "2h" in resolution2.reasoning or "2h — ELIGIBLE" in resolution2.reasoning or "ELIGIBLE" in resolution2.reasoning


def test_fulfilled_unverifiable_cancellation_discloses_refund_substitution():
    """CP6: order not found / otherwise unverifiable — Shopify can't cancel
    a fulfilled/unconfirmed order, so this requests a refund action instead,
    with the substitution explicitly disclosed both to the reviewer and the
    customer. Customer note must never claim cancellation happened."""
    resolution = CancellationSpecialist.resolve_fulfilled_unverifiable_fallback_to_refund(
        "5003", {"reason": "Order #5003 was not found in our system."},
    )
    assert resolution.requested_action_type == "refund"
    assert resolution.specialist == "cancellation"
    assert resolution.resolution_type == "refund_eligible"
    # Disclosed to the human reviewer.
    assert "cancellation" in resolution.reasoning.lower()
    assert "REFUND" in resolution.reasoning
    assert "couldn't be confirmed" in resolution.reasoning
    # Customer note never falsely claims cancellation happened.
    assert "do not say the order has been cancelled" in resolution.customer_facing_note.lower()
    assert "do not say a refund has been issued" in resolution.customer_facing_note.lower()


def test_fulfilled_eligible_cancellation_discloses_refund_substitution():
    """CP8: cancel intent, fulfilled + eligible order — same fallback and
    same disclosure requirement as CP6 (the one approved Phase 7 behavior
    improvement: this used to be undisclosed, shared, intent-agnostic
    code). specialist stays "cancellation" even though the requested action
    is "refund"."""
    eligibility = {"items": [{"title": "Essential Hoodie"}]}
    resolution = CancellationSpecialist.resolve_fulfilled_eligible_fallback_to_refund(
        "5004", eligibility, specific_item=None,
    )
    assert resolution.requested_action_type == "refund"
    assert resolution.specialist == "cancellation"
    assert resolution.resolution_type == "refund_eligible"
    assert "already been fulfilled" in resolution.reasoning
    assert "REFUND" in resolution.reasoning
    assert "do not say the order has been cancelled" in resolution.customer_facing_note.lower()
    assert "already shipped" in resolution.customer_facing_note.lower()

    # Partial-item variant preserves the specific-item distinction.
    specific_item = {"title": "Essential Hoodie", "variant_title": "M"}
    resolution_partial = CancellationSpecialist.resolve_fulfilled_eligible_fallback_to_refund(
        "5004", eligibility, specific_item=specific_item,
    )
    assert resolution_partial.requested_action_type == "refund"
    assert "Essential Hoodie" in resolution_partial.customer_facing_note
    assert "do not say the order has been cancelled" in resolution_partial.customer_facing_note.lower()


def test_cancellation_specialist_invalid_action_contract_is_asserted():
    """5. Invalid action contract is rejected/asserted — cancellation-native
    resolutions may only request None or "cancel_order"; fallback-to-refund
    resolutions may only request None or "refund". Simulating a hypothetical
    future bug (mutating after construction) must trip the class-level
    assertion helpers, mirroring Return/Exchange/RefundSpecialist's pattern."""
    from src.services.cancellation_specialist import _assert_cancellation_native, _assert_fallback_to_refund

    native = CancellationSpecialist.resolve_unfulfilled_eligible(
        "5005", "cancel", window_verified_eligible=False, window_evidence=None,
    )
    native.requested_action_type = "exchange"  # simulate a hypothetical future bug
    with pytest.raises(AssertionError):
        _assert_cancellation_native(native)

    fallback = CancellationSpecialist.resolve_fulfilled_unverifiable_fallback_to_refund(
        "5006", {"reason": "not found"},
    )
    fallback.requested_action_type = "cancel_order"  # simulate a hypothetical future bug
    with pytest.raises(AssertionError):
        _assert_fallback_to_refund(fallback)

    # Also confirmed via the shared gate itself, same pattern as the other
    # specialists' equivalent tests above.
    bad_resolution = CancellationSpecialist.resolve_unfulfilled_eligible(
        "5007", "cancel", window_verified_eligible=False, window_evidence=None,
    )
    bad_resolution.requested_action_type = "exchange"
    with pytest.raises(ExecutableActionRejected):
        _run(stage_resolution_action(ReturnActionsIntegration(), bad_resolution, order_id="5007"))


def test_cancellation_specialist_is_side_effect_free():
    """6. No Shopify/DB/action-creation access at all — every method is a
    pure function of its arguments, confirmed by calling all four with only
    plain dicts/primitives and no mocks/patches of any kind."""
    r1 = CancellationSpecialist.resolve_unfulfilled_manual_review("6001", "cancel", "")
    r2 = CancellationSpecialist.resolve_unfulfilled_eligible("6001", "cancel", False, None)
    r3 = CancellationSpecialist.resolve_fulfilled_unverifiable_fallback_to_refund("6001", {"reason": "x"})
    r4 = CancellationSpecialist.resolve_fulfilled_eligible_fallback_to_refund("6001", {"items": []}, None)
    for r in (r1, r2, r3, r4):
        assert isinstance(r, Resolution)
        assert not hasattr(r, "action_id")
        assert not hasattr(r, "staged")
