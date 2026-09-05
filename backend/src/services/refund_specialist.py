"""
Refund Specialist (PART 2/3 Phase 6)
======================================
The place a genuine REFUND intent is resolved into a decision — a
dedicated, clearly identifiable boundary, following the same pattern as
return_specialist.py's ReturnSpecialist and exchange_specialist.py's
ExchangeSpecialist. Refund is different from Return/Exchange: it MAY
request a real executable action — but only ever "refund", only ever
through the shared Executable Action Gate (specialist_resolution.py's
stage_resolution_action / return_actions_integration.py's
_stage_gated_action), and only when this system's existing eligibility
rules actually allow it. Human approval remains mandatory either way —
this class only decides whether a `refund` action should be REQUESTED, not
whether it executes.

    REFUND
    -> understand current customer intent (already resolved by the router
       — see intent_detector.py — before this specialist is ever reached)
    -> inspect relevant order/customer/policy/payment/fulfillment context
       (eligibility, already gathered by the caller — this class makes no
       Shopify/DB calls of its own)
    -> determine refund eligibility
    -> determine requested refund amount when applicable
    -> resolve the refund request:
         - identity mismatch -> escalate, NO action requested
         - eligibility unclear / needs manual review -> escalate, refund
           action REQUESTED (human still has to approve/act on it)
         - eligible, fulfilled -> refund action REQUESTED
    -> existing human approval remains mandatory either way (unchanged —
       actions_service.py's approve_action, untouched by this class)

Three entry points, matching the three places
return_actions_integration.py's handle_return_intent() has a genuine
refund-specific decision to make (as opposed to the branches that are
cancellation's own decision even when a refund intent happens to fall
through to them — e.g. an unfulfilled order always resolves to a
cancellation, never a refund, regardless of what the customer asked for;
that branch is left untouched, outside this specialist's scope, and is
PART 2/3 Phase 7's territory):

- resolve_identity_mismatch(): the contacting email doesn't match the
  order's email on file — never staged, never escalated as if something
  were pending; resolved honestly in one reply, and no order details beyond
  what the customer already knows are ever disclosed.
- resolve_manual_review(): eligibility couldn't be confirmed automatically
  (order not found, or any other non-identity manual-review case) but a
  refund IS still the right eventual mutation if approved — this still
  requests a `refund` action (human review decides the real outcome).
- resolve_eligible(): a fulfilled order that `check_return_eligibility`
  confirmed eligible — the genuine "yes, refund this" happy path. Full vs.
  partial (a single named item) is preserved via `specific_item`; the
  requested dollar amount (if the customer named one) is preserved via
  `requested_amount` — never itself sufficient to execute anything (only a
  human-typed amount at approval time ever reaches Shopify, per
  actions_service.approve_action's own override_amount contract) but
  captured here so the approval UI can show/pre-fill what was actually
  asked for.
"""
from typing import Any, Dict, Optional

from src.services.specialist_resolution import Resolution


class RefundSpecialist:
    """No instance state — every method is a pure function of its
    arguments; makes no Shopify/DB calls and never calls _create_action or
    the gate itself. A class (rather than bare module-level functions) so
    the Refund Specialist boundary is a clearly identifiable, individually
    referenceable unit, matching ReturnSpecialist/ExchangeSpecialist's shape."""

    @staticmethod
    def resolve_identity_mismatch(order_id: str) -> Resolution:
        note = (
            "**IDENTITY UNVERIFIED - DO NOT PROCESS REFUND**: The email this customer is "
            "contacting from does not match the email on file for this order. Do NOT create "
            "a request. Do NOT say this has been escalated, sent to the team, or that anyone "
            "will follow up - nothing has been submitted anywhere. Resolve this in this one "
            "reply: state plainly that the order was found but the contact email doesn't "
            "match the one on the order, so for security this request can't be processed "
            "from this email, and ask the customer to reach out to us from the email address "
            "used when placing the order so we can help right away."
        )
        resolution = Resolution(
            resolution_type="refund_identity_unverified",
            specialist="refund",
            order_id=order_id,
            reasoning="Sender email does not match the order's email on file.",
            customer_facing_note=note,
            eligible=False,
            requested_action_type=None,
        )
        assert resolution.requested_action_type is None, "Refund identity-mismatch must never request an executable action"
        return resolution

    @staticmethod
    def resolve_manual_review(
        order_id: str,
        eligibility: Dict[str, Any],
        requested_amount: Optional[float],
    ) -> Resolution:
        reason = eligibility.get("reason") or ""
        reasoning = f"Customer requests refund for order #{order_id}. Manual review required: {reason}"

        customer_facing_note = (
            f"**REQUEST SUBMITTED FOR MANUAL REVIEW**: {reason} "
            "Tell the customer: 'I've submitted your request to our team for manual review. "
            "They'll review it and you'll get an email confirmation once it's processed.'"
        )
        resolution = Resolution(
            resolution_type="refund_eligible",
            specialist="refund",
            order_id=order_id,
            reasoning=reasoning,
            customer_facing_note=customer_facing_note,
            eligible=False,
            requested_action_type="refund",
        )
        assert resolution.requested_action_type in (None, "refund"), (
            "Refund Specialist must never request a non-refund executable action"
        )
        return resolution

    @staticmethod
    def resolve_eligible(
        order_id: str,
        eligibility: Dict[str, Any],
        specific_item: Optional[Dict[str, Any]],
        requested_amount: Optional[float],
    ) -> Resolution:
        items = eligibility.get("items", [])
        item_names = ", ".join([i.get("title", "item") for i in items[:2]])

        if specific_item:
            reasoning = (
                f"Customer requests refund for order #{order_id}, SPECIFICALLY ONLY: "
                f"{specific_item.get('title')} ({specific_item.get('variant_title') or 'one size'}), "
                f"not the full order ({item_names})."
            )
            customer_facing_note = (
                f"**ACTION STAGED FOR APPROVAL (PARTIAL — only {specific_item.get('title')})**: "
                f"The customer wants to refund only {specific_item.get('title')}, not the rest of the order. "
                "Our team will confirm the exact refund amount for that item specifically. "
                f"Tell the customer: 'I've sent a request to my team to refund just the "
                f"{specific_item.get('title')}, the rest of your order is unaffected. "
                "You'll get a confirmation once they approve it.'"
            )
        else:
            reasoning = f"Customer requests refund for order #{order_id}: {item_names}"
            customer_facing_note = (
                "**ACTION STAGED FOR APPROVAL**: Your refund request has been submitted for review. "
                "Tell the customer: 'I've prepared your request for my team to review. "
                "You'll get a confirmation once they approve it.'"
            )

        resolution = Resolution(
            resolution_type="refund_eligible",
            specialist="refund",
            order_id=order_id,
            reasoning=reasoning,
            customer_facing_note=customer_facing_note,
            eligible=True,
            requested_action_type="refund",
        )
        assert resolution.requested_action_type == "refund", (
            "Refund Specialist's eligible path must request exactly 'refund', nothing else"
        )
        return resolution
