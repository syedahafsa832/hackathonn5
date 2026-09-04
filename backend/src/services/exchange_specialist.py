"""
Exchange Specialist (PART 2/3 Phase 5)
========================================
The one place EXCHANGE intent is resolved — a dedicated, clearly
identifiable boundary, following the same pattern as return_specialist.py's
ReturnSpecialist.

    EXCHANGE
    -> understand current customer intent (already resolved by the router —
       see intent_detector.py — before this specialist is ever reached)
    -> inspect relevant order/policy/product context (eligibility, matched
       item, live Shopify target lookup — all already gathered by the
       caller; this class makes no Shopify/DB calls of its own)
    -> determine what information can safely be provided
    -> escalate to human
    -> NEVER create an executable action

Current product policy (non-negotiable, see the PART 2/3 task spec):
exchange is not automated at this stage. Every genuine exchange decision
this class makes ends in escalation — `requested_action_type` is
hard-coded to None on every path, never computed, and asserted just before
every return as a redundant safety net (same discipline as
ReturnSpecialist). Never substitutes a refund, cancel_order, or the
exchange's own action type for an exchange request.

Two entry points, matching the two places return_actions_integration.py's
_handle_exchange() used to call _create_action() with real decision
content (as opposed to the early "ask for more info" returns — WHICH ITEM,
WHAT REPLACEMENT, OUT OF STOCK, etc. — that already created no action and
stay exactly as they were, unchanged, in the adapter):

- resolve_eligibility_unclear(): eligibility couldn't be confirmed
  automatically (order not found, sender mismatch, or any other
  manual-review case) — there's no verified exchange target either way.
- resolve_target_found(): a live, in-stock replacement was actually found
  — the "everything checks out" path that used to auto-stage a real
  "exchange" action. The useful information gathered (item, replacement,
  price difference) is preserved in the reasoning/customer note so nothing
  valuable is thrown away just because no action is created.
"""
from typing import Any, Dict, Optional

from src.services.specialist_resolution import Resolution


def _existing_action_note(existing_action: Optional[Dict[str, Any]], current_noun: str) -> str:
    """Historical-action context, worded identically to
    return_specialist.py's equivalent helper — a previous action is
    mentioned as related context only, never treated as authority over the
    current request. (return_actions_integration.py's own duplicate-guard
    already short-circuits before this specialist is reached whenever a
    same-type ["exchange"] action is active for this order — see
    _handle_exchange — so in practice `existing_action` reaching here today
    is always None; this parameter/helper exists so the specialist itself
    correctly folds in historical context if a caller ever does pass one,
    exactly like ReturnSpecialist does for refund/cancel_order history.)"""
    if not existing_action:
        return ""
    noun = {"cancel_order": "cancellation", "refund": "refund", "exchange": "exchange"}.get(
        existing_action.get("action_type"), "request"
    )
    return (
        f" Note: this order also has a separate {noun} request on file, currently "
        f"'{existing_action.get('status')}' - mention it as related context if relevant, but "
        f"it does NOT substitute for this {current_noun} request; do not describe it as satisfying or "
        "replacing what the customer is asking about now."
    )


class ExchangeSpecialist:
    """No instance state — every method is a pure function of its
    arguments. A class (rather than bare module-level functions) so the
    Exchange Specialist boundary is a clearly identifiable, individually
    referenceable unit, matching ReturnSpecialist's shape."""

    @staticmethod
    def resolve_eligibility_unclear(
        order_id: str,
        eligibility: Dict[str, Any],
        existing_action: Optional[Dict[str, Any]] = None,
    ) -> Resolution:
        reason = eligibility.get("reason") or ""
        existing_action_note = _existing_action_note(existing_action, "exchange")
        reasoning = (
            f"Customer requested an exchange for order #{order_id}, but eligibility couldn't be "
            f"verified automatically ({reason}).{existing_action_note}"
        )
        if eligibility.get("custom_policy_text"):
            reasoning += f" | Store policy on file: {eligibility['custom_policy_text']}"

        customer_facing_note = (
            "**EXCHANGE REQUEST - ESCALATE TO HUMAN, NO ACTION CREATED**: Exchanges are not automated "
            f"yet, and eligibility couldn't be confirmed automatically here ({reason}).{existing_action_note} "
            "Do NOT say a refund, cancellation, or exchange was started, staged, or submitted for this - "
            "none was. Tell the customer honestly that you've noted their exchange request and a team "
            "member will follow up to confirm eligibility and next steps."
        )
        resolution = Resolution(
            resolution_type="exchange_escalate_to_human",
            specialist="exchange",
            order_id=order_id,
            reasoning=reasoning,
            customer_facing_note=customer_facing_note,
            eligible=False,
            requested_action_type=None,
            existing_action_ref=existing_action,
        )
        assert resolution.requested_action_type is None, "Exchange Specialist must never request an executable action"
        return resolution

    @staticmethod
    def resolve_target_found(
        order_id: str,
        original_item: Dict[str, Any],
        target: Dict[str, Any],
        price_difference: float,
        existing_action: Optional[Dict[str, Any]] = None,
    ) -> Resolution:
        item_label = f"{original_item.get('title', 'the item')} ({original_item.get('variant_title') or 'one size'})"
        variant_label = f"{target.get('product_title')} ({target.get('variant_title')})"
        existing_action_note = _existing_action_note(existing_action, "exchange")

        if price_difference < 0:
            price_note = f" The replacement is ${abs(price_difference):.2f} cheaper than the original item."
        elif price_difference > 0:
            price_note = f" The replacement is ${price_difference:.2f} more than the original item."
        else:
            price_note = " The replacement is the same price as the original item."

        reasoning = (
            f"Customer requests exchange for order #{order_id}: {item_label} -> {variant_label}. "
            f"Live availability confirmed.{price_note}{existing_action_note}"
        )

        difference_note = (
            f", including how to handle the ${abs(price_difference):.2f} price difference"
            if price_difference != 0 else ""
        )
        customer_facing_note = (
            "**EXCHANGE REQUEST - ESCALATE TO HUMAN, NO ACTION CREATED**: Exchanges are not automated "
            f"yet. {variant_label} is confirmed available and in stock.{price_note}{existing_action_note} "
            "Do NOT say the exchange has been staged, approved, or completed - none of that has "
            "happened. Tell the customer honestly that you've noted exactly what they want to exchange "
            f"for and a team member will follow up to complete it{difference_note}."
        )
        resolution = Resolution(
            resolution_type="exchange_escalate_to_human",
            specialist="exchange",
            order_id=order_id,
            reasoning=reasoning,
            customer_facing_note=customer_facing_note,
            eligible=True,
            requested_action_type=None,
            existing_action_ref=existing_action,
        )
        assert resolution.requested_action_type is None, "Exchange Specialist must never request an executable action"
        return resolution
