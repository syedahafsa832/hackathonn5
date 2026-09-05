"""
Return Specialist (PART 2/3 Phase 4)
=====================================
The one place RETURN intent is resolved — a dedicated, clearly identifiable
boundary rather than a branch inside a larger shared function, so the
RETURN-never-creates-an-executable-action invariant lives in exactly one
place and can't be silently reintroduced by an edit somewhere else.

    RETURN
    -> understand current customer intent (already resolved by the router —
       see intent_detector.py — before this specialist is ever reached)
    -> inspect relevant order/policy/delivery context (eligibility, passed
       in by the caller — this class makes no Shopify/DB calls of its own)
    -> resolve return eligibility/information
    -> escalate to human when appropriate
    -> NEVER create an executable action

Stateless and side-effect-free by design: given the same eligibility/
context inputs, always produces the same Resolution. The caller
(ReturnActionsIntegration — the adapter/integration layer) owns fetching
that context (Shopify order lookup, the duplicate-guard's existing-action
lookup) and owns turning the returned Resolution into the pipeline's
result/context/logging shape (see handle_return_intent's `if intent_type ==
"return":` branch). This class owns only the RETURN decision itself.

`existing_action` (the caller's duplicate-guard lookup for any prior
refund/cancel_order on this order) is surfaced ONLY as informational
context in the reply text — never as something that resolves, substitutes
for, or is treated as authority over the current return request. A
previous action must never silently define the current intent.
"""
from typing import Any, Dict, Optional

from src.services.specialist_resolution import Resolution, policy_evidence_excerpt


class ReturnSpecialist:
    """No instance state — `resolve()` is a pure function of its arguments.
    A class (rather than a bare module-level function) so the Return
    Specialist boundary is a clearly identifiable, individually referenceable
    unit — consistent with how refund/exchange/cancellation specialists are
    expected to be organized in later phases."""

    @staticmethod
    def resolve(
        order_id: str,
        eligibility: Dict[str, Any],
        is_unfulfilled: bool,
        existing_action: Optional[Dict[str, Any]],
    ) -> Resolution:
        """Always escalates to a human. There is no "return" executable
        action type anywhere in this system, and staging a refund/
        cancel_order as a silent substitute is exactly the RETURN ->
        REFUND/CANCEL contamination Part 1 fixed — so `requested_action_type`
        is hard-coded to None on every path through this method, never
        computed. The assertion just before each return is a deliberate,
        redundant safety net: even a future edit to this method that
        accidentally set requested_action_type would fail loudly here,
        before the caller (or the shared Executable Action Gate) ever sees
        it — RETURN reaching _create_action()/stage_resolution_action() is
        not just unlikely, it is asserted unreachable."""
        if eligibility.get("identity_mismatch"):
            note = (
                "**IDENTITY UNVERIFIED - DO NOT PROCESS RETURN**: The email this customer is "
                "contacting from does not match the email on file for this order. Do NOT create "
                "a request. Do NOT say this has been escalated, sent to the team, or that anyone "
                "will follow up - nothing has been submitted anywhere. Resolve this in this one "
                "reply: state plainly that the order was found but the contact email doesn't "
                "match the one on the order, so for security this request can't be processed "
                "from this email, and ask the customer to reach out to us from the email address "
                "used when placing the order so we can help right away."
            )
            resolution = Resolution(
                resolution_type="return_identity_unverified",
                specialist="return",
                order_id=order_id,
                reasoning="Sender email does not match the order's email on file.",
                customer_facing_note=note,
                eligible=False,
                requested_action_type=None,
                existing_action_ref=existing_action,
            )
            assert resolution.requested_action_type is None, "Return Specialist must never request an executable action"
            return resolution

        # fulfillment_status == "fulfilled" means Shopify marked the order
        # shipped - it does NOT mean the package has arrived (see
        # actions_manager.check_return_eligibility's own comment).
        # shipment_status is Shopify's own carrier-reported signal, when
        # present; never invented when the carrier hasn't reported one.
        shipment_status = eligibility.get("shipment_status")
        if is_unfulfilled:
            delivery_note = (
                " This order has not shipped yet, so there is nothing to physically return yet - "
                "if the customer wants to stop the order before it ships, that is a cancellation, "
                "not a return; ask which they mean if it's unclear."
            )
        elif shipment_status and shipment_status != "delivered":
            delivery_note = (
                f" Shipment tracking shows this order is currently '{shipment_status}', not yet "
                "confirmed delivered - do not treat this as a normal post-delivery return without "
                "first checking with the customer whether the package has actually arrived."
            )
        else:
            delivery_note = ""

        existing_action_note = ""
        if existing_action:
            existing_noun = {"cancel_order": "cancellation", "refund": "refund"}.get(
                existing_action.get("action_type"), "request"
            )
            existing_action_note = (
                f" Note: this order also has a separate {existing_noun} request on file, currently "
                f"'{existing_action.get('status')}' - mention it as related context if relevant, but "
                "it does NOT substitute for this return request; do not describe it as satisfying or "
                "replacing what the customer is asking about now."
            )

        reason = eligibility.get("reason") or ""
        policy_evidence = policy_evidence_excerpt(
            eligibility.get("custom_policy_text") or eligibility.get("policy_evidence")
        )
        reasoning = (
            f"Customer requests a return for order #{order_id}."
            f"{(' ' + reason) if reason else ''}{delivery_note}{existing_action_note}"
        )
        if policy_evidence:
            reasoning += f" | Store policy on file: {policy_evidence}"

        customer_facing_note = (
            "**RETURN REQUEST - ESCALATE TO HUMAN, NO ACTION CREATED**: Returns are not automated "
            f"yet.{delivery_note}{existing_action_note} "
            f"{('Known order/policy context: ' + reason) if reason else ''} "
            "Do NOT say a refund or cancellation was started, staged, or submitted for this - none "
            "was, and do NOT confuse this with any unrelated request mentioned above. Tell the "
            "customer honestly that you've noted their return request and a team member will "
            "follow up with next steps (like a return label or return address) shortly. Never say "
            "the return, or any refund, has already been processed or approved."
        )

        resolution = Resolution(
            resolution_type="return_escalate_to_human",
            specialist="return",
            order_id=order_id,
            reasoning=reasoning,
            customer_facing_note=customer_facing_note,
            eligible=None,
            requested_action_type=None,
            existing_action_ref=existing_action,
        )
        assert resolution.requested_action_type is None, "Return Specialist must never request an executable action"
        return resolution
