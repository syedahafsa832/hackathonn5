"""
Cancellation Specialist (PART 2/3 Phase 7)
============================================
The place a genuine CANCELLATION intent is resolved into a decision — a
dedicated, clearly identifiable boundary, following the same pattern as
refund_specialist.py's RefundSpecialist. Cancellation is like Refund (not
Return/Exchange): it MAY request a real executable action — but only ever
through the shared Executable Action Gate
(specialist_resolution.py's stage_resolution_action /
return_actions_integration.py's _stage_gated_action), and only when this
system's existing eligibility rules actually allow it. Human approval
remains mandatory either way — this class only decides which action type
should be REQUESTED, not whether it executes.

    CANCELLATION
    -> understand current customer intent (already resolved by the router
       — see intent_detector.py — before this specialist is ever reached)
    -> inspect relevant order/policy/fulfillment context (eligibility and
       policy-window evaluation, already gathered by the caller — this
       class makes no Shopify/DB calls of its own)
    -> resolve the cancellation request:
         - unfulfilled + custom policy on file -> escalate for a human
           window check, cancel_order action REQUESTED
         - unfulfilled + eligible (no policy, or policy verified within
           window) -> cancel + auto-refund, cancel_order action REQUESTED
         - fulfilled/unverifiable -> Shopify cannot cancel a fulfilled
           order, so this falls back to a refund action REQUESTED instead
           — explicitly disclosed, never silently substituted
         - fulfilled + eligible -> same fallback, same disclosure
    -> existing human approval remains mandatory either way (unchanged —
       actions_service.py's approve_action, untouched by this class)

IMPORTANT BUSINESS RULE (unchanged, just now owned by this specialist): a
genuinely unfulfilled order is cancelled — never "just refunded in place"
— specifically so its payment can then be refunded as part of that same
Shopify mutation (cancel_order auto-refunds). This is never confused with
a refund intent: the resolution's reasoning/customer note always reflects
the CURRENT customer wording (via the `intent_type` the caller passes in),
never a hardcoded assumption.

Four entry points, matching the four cancellation decision points
identified in the PART 2/3 Phase 7 inspection (return_actions_integration.py's
handle_return_intent(), CP1/CP4/CP6/CP8):

- resolve_unfulfilled_manual_review(): CP1 — a merchant free-text
  cancellation-window policy exists but couldn't be deterministically
  verified against the order's real timestamp; escalate for a human check,
  but still request cancel_order (Shopify's cancel_order() is guaranteed
  to succeed on a confirmed-unfulfilled order).
- resolve_unfulfilled_eligible(): CP4 — no policy restriction, or one that
  was deterministically verified as within window; cancel + auto-refund is
  the right, safe action. This is the ONE path Cancellation Autopilot may
  ever fire from (decided by the caller, not this class).
- resolve_fulfilled_unverifiable_fallback_to_refund(): CP6 — eligibility
  couldn't confirm the order is unfulfilled (order not found, or any other
  non-identity manual-review case) — Shopify's cancel_order() hard-rejects
  a fulfilled order, so a refund action is requested instead, with the
  substitution explicitly disclosed to both the human reviewer
  (`reasoning`) and the customer (`customer_facing_note`, which must never
  claim the order was cancelled).
- resolve_fulfilled_eligible_fallback_to_refund(): CP8's cancel-intent,
  fulfilled+eligible case — same fallback and same disclosure requirement
  as CP6, now made explicit (this used to be undisclosed, shared,
  intent-agnostic code — see the PART 2/3 Phase 7 inspection report for
  why that was a real gap, mirroring the disclosure Part 1 already
  required for return->refund/cancel contamination).
"""
from typing import Any, Dict, Optional

from src.services.specialist_resolution import Resolution


def _assert_cancellation_native(resolution: Resolution) -> None:
    assert resolution.requested_action_type in (None, "cancel_order"), (
        "Cancellation-native resolutions may only request None or 'cancel_order'"
    )


def _assert_fallback_to_refund(resolution: Resolution) -> None:
    assert resolution.requested_action_type in (None, "refund"), (
        "Cancellation's fallback-to-refund resolutions may only request None or 'refund'"
    )


class CancellationSpecialist:
    """No instance state — every method is a pure function of its
    arguments; makes no Shopify/DB calls and never calls _create_action or
    the gate itself. A class (rather than bare module-level functions) so
    the Cancellation Specialist boundary is a clearly identifiable,
    individually referenceable unit, matching RefundSpecialist's shape."""

    @staticmethod
    def resolve_unfulfilled_manual_review(
        order_id: str,
        intent_type: str,
        cancel_policy_text: Optional[str],
    ) -> Resolution:
        reasoning = (
            f"Customer requests {intent_type} for order #{order_id}. "
            "Store policy requires a human check before cancelling."
            if cancel_policy_text else
            f"Customer requests {intent_type} for order #{order_id}. "
            "Store policy details could not be confirmed just now — needs a human check before cancelling."
        )
        customer_facing_note = (
            "**REQUEST SUBMITTED FOR MANUAL REVIEW**: This store has additional cancellation policy "
            "details on file that need a human check. "
            "Tell the customer: 'I've sent your cancellation request to our team for a quick review "
            "given our store policy. They'll follow up once it's reviewed.'"
        )
        resolution = Resolution(
            resolution_type="cancellation_eligible",
            specialist="cancellation",
            order_id=order_id,
            reasoning=reasoning,
            customer_facing_note=customer_facing_note,
            eligible=False,
            requested_action_type="cancel_order",
        )
        _assert_cancellation_native(resolution)
        return resolution

    @staticmethod
    def resolve_unfulfilled_eligible(
        order_id: str,
        intent_type: str,
        window_verified_eligible: bool,
        window_evidence: Optional[Dict[str, Any]],
    ) -> Resolution:
        if window_verified_eligible and window_evidence:
            ev = window_evidence
            reasoning = (
                f"Customer requests {intent_type} for order #{order_id}. "
                f"Order is unfulfilled — cancel + auto-refund is appropriate. "
                f"Store's free-text cancellation window verified against the real order timestamp: "
                f"placed {ev['elapsed_hours']:.2f}h ago, policy allows {ev['policy_window_hours']:.0f}h — ELIGIBLE."
            )
        else:
            reasoning = (
                f"Customer requests {intent_type} for order #{order_id}. "
                f"Order is unfulfilled — cancel + auto-refund is appropriate."
            )
        customer_facing_note = (
            "**CANCEL QUEUED**: Order hasn't shipped yet — cancel + refund is the right action. "
            "Tell the customer: 'Since your order hasn't shipped yet, I've sent your cancellation request "
            "to our team. They'll cancel it and your refund will appear within 3–5 business days.'"
        )
        resolution = Resolution(
            resolution_type="cancellation_eligible",
            specialist="cancellation",
            order_id=order_id,
            reasoning=reasoning,
            customer_facing_note=customer_facing_note,
            eligible=True,
            requested_action_type="cancel_order",
        )
        _assert_cancellation_native(resolution)
        return resolution

    @staticmethod
    def resolve_fulfilled_unverifiable_fallback_to_refund(
        order_id: str,
        eligibility: Dict[str, Any],
    ) -> Resolution:
        reason = eligibility.get("reason")
        reasoning = (
            f"Customer requested a cancellation for order #{order_id}, but it's being staged as a "
            "REFUND for manual review instead - Shopify can only cancel a confirmed-unfulfilled "
            f"order, and that couldn't be confirmed here. Manual review required: {reason}"
        )
        customer_facing_note = (
            f"**REQUEST SUBMITTED FOR MANUAL REVIEW**: {reason} "
            "Do NOT say the order has been cancelled. Do NOT say a refund has been issued. "
            "Neither has happened yet - this is going to a human for review. Do NOT use "
            "internal terms like 'action type', 'staging', 'refund-family', or 'fallback' - "
            "the customer has no reason to know how this works internally. Tell the customer "
            "plainly and briefly, in your own natural words, covering exactly these points: "
            "(1) they asked to cancel their order, (2) it couldn't be safely cancelled "
            "automatically, (3) their request has been sent to the team for review, (4) the "
            "team may handle it as a refund instead if that's the right outcome. For example: "
            "'You're asking to cancel your order. I wasn't able to safely cancel it "
            "automatically, so I've sent your request for review. The team will check it and "
            "let you know what can be done, including whether a refund is possible.'"
        )
        resolution = Resolution(
            resolution_type="refund_eligible",
            specialist="cancellation",
            order_id=order_id,
            reasoning=reasoning,
            customer_facing_note=customer_facing_note,
            eligible=False,
            requested_action_type="refund",
        )
        _assert_fallback_to_refund(resolution)
        return resolution

    @staticmethod
    def resolve_fulfilled_eligible_fallback_to_refund(
        order_id: str,
        eligibility: Dict[str, Any],
        specific_item: Optional[Dict[str, Any]],
    ) -> Resolution:
        """PART 2/3 Phase 7's one approved behavior improvement: this case
        used to be undisclosed, shared, intent-agnostic code (the customer
        was told "**ACTION STAGED FOR APPROVAL**: Your cancellation request
        has been submitted..." while a refund action was silently stored).
        Now matches CP6's disclosure style exactly: never claims the order
        was cancelled, explains the order has already shipped so
        cancellation isn't possible, and that a refund is the reviewed
        outcome instead."""
        items = eligibility.get("items", [])
        item_names = ", ".join([i.get("title", "item") for i in items[:2]])

        if specific_item:
            reasoning = (
                f"Customer requested a cancellation for order #{order_id}, SPECIFICALLY ONLY: "
                f"{specific_item.get('title')} ({specific_item.get('variant_title') or 'one size'}) - "
                "but the order has already been fulfilled, so Shopify's cancellation mutation cannot "
                f"be used here; being staged as a REFUND for manual review instead, not the full order "
                f"({item_names})."
            )
            customer_facing_note = (
                f"**REQUEST SUBMITTED FOR MANUAL REVIEW (PARTIAL — only {specific_item.get('title')})**: "
                "Do NOT say the order has been cancelled - this order has already shipped, so it can't "
                "be cancelled through our system. The team will instead review refunding just this item. "
                f"Tell the customer: 'Since your order has already shipped, I'm not able to cancel it - "
                f"but I've sent a request to my team to refund just the {specific_item.get('title')}, "
                "the rest of your order is unaffected. You'll get a confirmation once they approve it.'"
            )
        else:
            reasoning = (
                f"Customer requested a cancellation for order #{order_id}, but the order has already "
                "been fulfilled, so Shopify's cancellation mutation cannot be used here; being staged "
                f"as a REFUND for manual review instead. Order contents: {item_names}."
            )
            customer_facing_note = (
                "**REQUEST SUBMITTED FOR MANUAL REVIEW**: Do NOT say the order has been cancelled - "
                "this order has already shipped, so it can't be cancelled through our system. The team "
                "will instead review issuing a refund. Tell the customer: 'Since your order has already "
                "shipped, I'm not able to cancel it directly - but I've sent your request to my team, "
                "who can review issuing a refund instead. You'll get a confirmation once it's approved.'"
            )

        resolution = Resolution(
            resolution_type="refund_eligible",
            specialist="cancellation",
            order_id=order_id,
            reasoning=reasoning,
            customer_facing_note=customer_facing_note,
            eligible=True,
            requested_action_type="refund",
        )
        _assert_fallback_to_refund(resolution)
        return resolution
