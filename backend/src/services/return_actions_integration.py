"""
Return Actions Integration Helper
=================================
Routes customer action requests (refund, cancel, address change, reship)
to the human-in-the-loop approval queue.
Uses AI intent detection — no static keyword lists.
"""
import asyncio
import logging
import re
from typing import Dict, Any, List, Optional, Tuple, TYPE_CHECKING, Callable, Awaitable

from src.lib.supabase_client import supabase_select
from src.services.intent_detector import intent_detector, IntentResult
from src.services.policy_evidence import verify_time_window
from src.services.specialist_resolution import (
    Resolution,
    stage_resolution_action,
    policy_evidence_excerpt as _policy_evidence_excerpt,
)
from src.services.return_specialist import ReturnSpecialist
from src.services.exchange_specialist import ExchangeSpecialist
from src.services.refund_specialist import RefundSpecialist
from src.services.cancellation_specialist import CancellationSpecialist

from .actions_manager import actions_manager, stage_pending_action

logger = logging.getLogger(__name__)

_ACTION_TYPE_MAP = {
    "Refund": "refund",
    "Exchange": "exchange",
    "Cancel": "cancel_order",
    "cancel_order": "cancel_order",
    "cancel": "cancel_order",
    "refund": "refund",
    "return": "refund",
    "exchange": "exchange",
    "change_address": "change_address",
    "address_change": "change_address",
    "reship": "reship",
    "restore_order": "restore_order",
}

# _policy_evidence_excerpt: get_custom_policy_text() can return up to
# several full RAG chunks (Store Pages / FAQ Pages) joined together - never
# dumped into the short, merchant-facing "reason" a human reads in ~5-10
# seconds. Bounded there for the dashboard's expandable "View policy
# evidence" section instead; the untruncated eligibility dict (which still
# holds the raw text) is never deleted from extracted_data, only not
# surfaced as the primary reason. Now lives in specialist_resolution.py
# (PART 2/3 Phase 4) so return_specialist.py can share it without a
# circular import — imported here under its original name so none of this
# file's existing call sites needed to change.


class ReturnActionsIntegration:

    def __init__(self):
        self.actions = actions_manager

    async def detect_intent(self, query: str) -> IntentResult:
        """Detect action intent from customer message using LLM."""
        return await intent_detector.detect(query)

    async def handle_return_intent(
        self,
        query: str,
        customer_info: Dict[str, Any],
        existing_tool_results: Dict[str, Any],
        tenant_id: Optional[str] = None,
        brand_id: Optional[str] = None,
        ticket_id: Optional[str] = None,
        intent_result: Optional[IntentResult] = None,
        on_progress: Optional[Callable[[str, str], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        result = {
            "return_checked": False,
            "eligibility": None,
            "exchange": None,
            "action_context": "",
        }

        async def _emit(stage: str, label: str) -> None:
            # Real backend-driven activity states, not a fake spinner — only
            # called right before/after the actual work each label describes
            # (see call sites below), never speculatively.
            if not on_progress:
                return
            try:
                await on_progress(stage, label)
            except Exception:
                logger.debug("[ReturnActions] on_progress callback failed (non-blocking)", exc_info=True)

        # Detect intent if not already provided
        if intent_result is None:
            intent_result = await self.detect_intent(query)

        intent_type = intent_result.action_type
        if not intent_result.has_action:
            return result

        order_id, email = self._extract_order_info(query, customer_info, existing_tool_results, intent_result, ticket_id=ticket_id)
        logger.info(f"[ReturnActions] intent={intent_type}, order_id={order_id}, email={email}")

        # ── RESTORE ORDER (un-cancel) ───────────────────────────────────────
        if intent_type == "restore_order":
            if not order_id:
                result["action_context"] = (
                    "ACTION REQUIRED: Ask the customer for their order number so we can check if it can be restored."
                )
                return result

            # Use order data already fetched by the agent to check restocked status
            order_data = existing_tool_results.get("order_status", {})

            if order_data.get("success"):
                cancelled_at = order_data.get("cancelled_at")
                fulfillment_status = order_data.get("fulfillment_status", "")
                line_items = order_data.get("items", [])

                is_restocked = (
                    fulfillment_status == "restocked" or
                    any(item.get("fulfillment_status") == "restocked" for item in line_items)
                )

                if not cancelled_at:
                    result["action_context"] = (
                        "ORDER IS NOT CANCELLED — nothing to restore. "
                        "Tell the customer their order is active and processing normally. "
                        "Do NOT create a restore action."
                    )
                    return result

                if is_restocked:
                    # Inventory returned to stock — Shopify cannot reopen
                    result["action_context"] = (
                        "CANCELLED ORDER — CANNOT BE RESTORED (inventory has been restocked). "
                        "Tell the customer warmly: 'Unfortunately once an order is cancelled and the "
                        "stock is released back, it can't be reactivated — I'm so sorry! "
                        "The good news is you can place a new order any time and it'll go through "
                        "just as quickly. If you need help with anything else just let me know!' "
                        "Do NOT say anything is queued or being reviewed. Do NOT create any action."
                    )
                    return result

                # Cancelled but NOT restocked — Shopify reopen.json may work
                ai_reasoning = (
                    f"Customer requests restore of cancelled order #{order_id}. "
                    f"Order is cancelled but inventory not yet restocked — team can attempt Shopify reopen."
                )
                staged = await self._create_action(
                    tenant_id=tenant_id, brand_id=brand_id, ticket_id=ticket_id,
                    action_type="restore_order", order_id=order_id, email=email or "",
                    customer_name=customer_info.get("name"), query=query,
                    ai_reasoning=ai_reasoning, eligibility={},
                )
                result["staged"] = staged
                result["action_context"] = (
                    "**RESTORE ORDER QUEUED**: Order is cancelled but inventory not yet restocked — "
                    "our team will try to reactivate it via Shopify. "
                    "Tell the customer: 'I've sent your restoration request to our team. "
                    "They'll take a look and follow up once it's reviewed.'"
                )
            else:
                # No order data available — cannot safely determine restocked status
                result["action_context"] = (
                    "CANCELLED ORDER — CANNOT CONFIRM STATUS. "
                    "Once a Shopify order is cancelled it usually cannot be reactivated. "
                    "Tell the customer warmly: 'Unfortunately once an order is cancelled it can't be "
                    "brought back — I'm so sorry about that! The good news is you can place a new order "
                    "any time and it'll go through just as quickly. "
                    "If you need help with anything else just let me know!' "
                    "Do NOT say anything is queued or being reviewed. Do NOT create any action."
                )
            return result

        # ── ADDRESS CHANGE ──────────────────────────────────────────────────
        if intent_type == "address_change":
            if not order_id:
                result["action_context"] = (
                    "ACTION REQUIRED: Ask the customer for their order number so the team can update the delivery address."
                )
                return result

            # Same duplicate-request guard as refund/cancel/reship above
            # (PART 6) - "please update my address" repeated across several
            # messages must never stage a second address-change escalation
            # for the same order.
            existing_action = await self._find_active_action(tenant_id, order_id, "change_address")
            if existing_action:
                result["action_context"] = self._duplicate_status_context(existing_action, intent_type)
                result["duplicate_of_existing_action"] = existing_action
                return result

            new_address_text = intent_result.raw_address or None

            # Parse raw address into structured fields for automatic Shopify update.
            # Add 500ms gap so we don't hit Mistral rate limits back-to-back.
            structured_address = None
            if new_address_text:
                try:
                    from src.services.intent_detector import intent_detector as _idet
                    await asyncio.sleep(0.5)
                    structured_address = await _idet.parse_address(new_address_text)
                except Exception as _ae:
                    logger.warning(f"[ReturnActions] Address parse failed: {_ae}")

            # Validate: if address is incomplete, ask the customer for missing fields.
            # Never queue an action or confirm an update for an incomplete address.
            if structured_address:
                is_valid, missing = self._validate_address(structured_address)
                if not is_valid:
                    # Ask ONLY for the fields _validate_address actually
                    # flagged as missing - never the whole bundle. The
                    # customer already gave us whatever IS present in
                    # structured_address (street/city/etc. successfully
                    # parsed); re-asking for those too reads as if we never
                    # looked at their message. Echo back what we have so
                    # they can see it was received, not just repeat it back
                    # blind.
                    missing_str = " and ".join(missing) if len(missing) <= 2 else ", ".join(missing[:-1]) + f", and {missing[-1]}"
                    have_parts = [
                        v for k, v in (
                            ("address1", structured_address.get("address1")),
                            ("city", structured_address.get("city")),
                            ("province", structured_address.get("province")),
                            ("zip", structured_address.get("zip")),
                        ) if v
                    ]
                    have_str = ", ".join(have_parts)
                    result["action_context"] = (
                        f"ADDRESS INCOMPLETE — DO NOT CONFIRM. "
                        f"The customer's address is missing ONLY: {missing_str}. "
                        f"Already provided and captured: {have_str or '(nothing else parsed)'} - do NOT ask for these again. "
                        f"Tell the customer politely, referencing what they already gave you: "
                        f"'Got it - {have_str}. I just need the {missing_str} to finish updating your address.' "
                        f"Ask ONLY for {missing_str}, never the full address bundle. Do NOT say you've queued anything."
                    )
                    return result
            elif new_address_text:
                # LLM couldn't parse at all — address text exists but is too vague
                result["action_context"] = (
                    f"ADDRESS TOO VAGUE — DO NOT CONFIRM. "
                    f"The customer wrote '{new_address_text}' but this is not a complete address. "
                    f"Tell the customer: 'I'd be happy to update your address! "
                    f"Could you please provide your full name, street address, city, and country? "
                    f"For example: John Smith, 123 Main Street, Lahore, Pakistan.' "
                    f"Do NOT say you've queued anything."
                )
                return result
            else:
                # No address at all in the message
                result["action_context"] = (
                    "ADDRESS MISSING — DO NOT CONFIRM. "
                    "The customer hasn't provided a new address. "
                    "Ask them: 'What's the new address you'd like us to ship to? "
                    "Please include your full name, street address, city, and country.'"
                )
                return result

            # Best-effort: fetch the order's CURRENT shipping address and
            # fulfillment status so the escalation shows what's being
            # changed FROM, not just the requested new address, and whether
            # Shopify is even expected to allow it (see
            # update_shipping_address's own live fulfillment check, which
            # remains the actual authority at approval time - this is
            # display-only, never a second eligibility gate). Never blocks
            # staging on failure.
            #
            # Same fetch also establishes customer/order identity - the
            # exact same order.email-vs-sender-email comparison
            # check_return_eligibility already does for refund/cancel
            # (actions_manager.py Step 2), reused here rather than
            # reimplemented, since address_change never ran that check at
            # all (confirmed gap - see the read-only security trace before
            # this fix). Stricter than that existing comparison on one
            # point: a MISSING order email there is treated as "nothing to
            # compare against, proceed" (lenient); here it's treated as
            # unverified - the customer is asking to redirect a physical
            # shipment, not just view/adjust their own order, so the
            # default on ambiguity is "can't confirm this is their order"
            # rather than "no conflict found". Never blocks staging either
            # way - the existing human-approval gate is what actually
            # prevents an unverified mutation; this only makes sure that
            # gate is shown accurate information instead of none.
            current_shipping_address = None
            current_fulfillment_status = None
            identity_verified = False
            identity_verification_reason = "Could not verify - order lookup failed"
            if tenant_id:
                try:
                    from src.services.shopify_service import shopify_service
                    client = await shopify_service.get_client_for_tenant(tenant_id)
                    order_resp = await client.get_order(order_id)
                    if order_resp.get("success") and order_resp.get("order"):
                        current_shipping_address = order_resp["order"].get("shipping_address")
                        current_fulfillment_status = order_resp["order"].get("fulfillment_status")
                        order_owner_email = (order_resp["order"].get("email") or "").strip().lower()
                        sender_email = (email or "").strip().lower()
                        if not order_owner_email:
                            identity_verification_reason = "Order has no customer email on file to verify against"
                        elif not sender_email:
                            identity_verification_reason = "No verified sender email available for this conversation"
                        elif order_owner_email == sender_email:
                            identity_verified = True
                            identity_verification_reason = None
                        else:
                            identity_verification_reason = "Sender email does not match the order's customer email on file"
                            logger.warning(
                                f"[ReturnActions] Address-change identity mismatch for order {order_id}: "
                                f"order email={order_owner_email!r}, sender email={sender_email!r}"
                            )
                    else:
                        identity_verification_reason = "Could not verify - order not found in Shopify"
                except Exception as e:
                    logger.warning(f"[ReturnActions] Address-change order lookup failed for order {order_id} (continuing without it): {e}")

            ai_reasoning = (
                f"Customer requests address change for order #{order_id}. "
                f"Requested address: {new_address_text} [Auto-parsed ✓]"
            )
            if not identity_verified:
                ai_reasoning += f" ⚠ IDENTITY NOT VERIFIED: {identity_verification_reason}. Confirm the requester before approving."
            staged = await self._create_action(
                tenant_id=tenant_id, brand_id=brand_id, ticket_id=ticket_id,
                action_type="change_address", order_id=order_id, email=email or "",
                customer_name=customer_info.get("name"), query=query,
                ai_reasoning=ai_reasoning, eligibility={},
                new_address_text=new_address_text,
                structured_address=structured_address,
                identity_verified=identity_verified,
                identity_verification_reason=identity_verification_reason,
                current_shipping_address=current_shipping_address,
                current_fulfillment_status=current_fulfillment_status,
            )
            result["staged"] = staged
            result["action_context"] = (
                "**ADDRESS CHANGE QUEUED (auto-parsed)**: Structured address stored — will update in Shopify automatically on approval. "
                "Tell the customer: 'I've queued your address update for our team to review. You'll get a confirmation email once it's updated.'"
            )
            return result

        # ── RESHIP / LOST PACKAGE ───────────────────────────────────────────
        if intent_type == "reship":
            if not order_id:
                result["action_context"] = (
                    "ACTION REQUIRED: Ask customer for their order number so the team can investigate the delivery."
                )
                return result

            # Same duplicate-request guard as refund/cancel above (PART 6) -
            # "my package never arrived" repeated across several messages
            # must never stage a second reship escalation for the same order.
            existing_action = await self._find_active_action(tenant_id, order_id, "reship")
            if existing_action:
                result["action_context"] = self._duplicate_status_context(existing_action, intent_type)
                result["duplicate_of_existing_action"] = existing_action
                return result

            # Best-effort order enrichment so the human reviewer sees what's
            # actually being requested (item/qty, shipping address,
            # fulfillment + tracking status) instead of a bare order number.
            # Reship never runs the return-eligibility/policy gate (a
            # lost-in-transit package isn't a return), so this fetches the
            # raw order directly rather than going through
            # check_return_eligibility. Never blocks staging on failure -
            # the escalation must still be created even if this lookup fails.
            order_snapshot = None
            if tenant_id:
                try:
                    from src.services.shopify_service import shopify_service
                    client = await shopify_service.get_client_for_tenant(tenant_id)
                    order_resp = await client.get_order(order_id)
                    if order_resp.get("success") and order_resp.get("order"):
                        raw_order = order_resp["order"]
                        fulfillments = raw_order.get("fulfillments") or []
                        latest_fulfillment = fulfillments[-1] if fulfillments else {}
                        order_snapshot = {
                            "items": [
                                {
                                    "title": item.get("title"),
                                    "variant_title": item.get("variant_title"),
                                    "quantity": item.get("quantity"),
                                    "sku": item.get("sku"),
                                }
                                for item in raw_order.get("line_items", [])
                            ],
                            "fulfillment_status": raw_order.get("fulfillment_status"),
                            "shipping_address": raw_order.get("shipping_address"),
                            "tracking_company": latest_fulfillment.get("tracking_company"),
                            "tracking_number": latest_fulfillment.get("tracking_number"),
                            "tracking_url": latest_fulfillment.get("tracking_url"),
                        }
                except Exception as e:
                    logger.warning(f"[ReturnActions] Reship order enrichment failed for order {order_id} (continuing without it): {e}")

            ai_reasoning = f"Customer reports delivery issue for order #{order_id} — package not received."
            staged = await self._create_action(
                tenant_id=tenant_id, brand_id=brand_id, ticket_id=ticket_id,
                action_type="reship", order_id=order_id, email=email or "",
                customer_name=customer_info.get("name"), query=query,
                ai_reasoning=ai_reasoning, eligibility={}, reship_order_snapshot=order_snapshot,
            )
            result["staged"] = staged
            result["action_context"] = (
                "**DELIVERY ISSUE QUEUED**: Team will check with the carrier and arrange reship or refund. "
                "Tell the customer: 'I've flagged this with our team — they'll investigate with the carrier "
                "and follow up once they've looked into it.'"
            )
            return result

        # ── EXCHANGE — different size/color/product, live Shopify grounded ──
        if intent_type == "exchange":
            return await self._handle_exchange(
                query, customer_info, order_id, email, intent_result,
                tenant_id, brand_id, ticket_id, result, _emit,
            )

        # ── RETURN / REFUND / CANCEL — needs eligibility check ──────────────
        # "return" (send an item back) and "refund" (money back for some
        # other reason) both resolve to the exact same Shopify mutation in
        # this REST-only integration — there is no separate Returns-API
        # call, refund IS how a return is actually fulfilled. They share
        # this one block so eligibility/policy logic never has to be
        # maintained twice; only the customer-facing wording below is
        # intent-aware.
        result["return_checked"] = True
        # Reused at each progress-emit site below and by the eligible-branch
        # wording further down — customer-facing activity/labels must match
        # what the customer actually asked for (a cancel request shows
        # "cancellation", not "refund"), even though cancel/return/refund
        # share this one execution path.
        _noun = "return" if intent_type == "return" else "refund" if intent_type == "refund" else "cancellation"

        # Only the order number is required to ATTEMPT a real lookup — email
        # is for ownership verification, not for finding the order, and
        # check_return_eligibility already verifies it (missing/mismatched
        # email -> staged for manual review, never silently treated as
        # eligible). Previously this gated on `not order_id or not email`,
        # which meant a chat visitor who gave an explicit order number but
        # hasn't shared their email (the common case for an unverified
        # widget session) got the generic "ask for your order number and
        # email" message even though the order number WAS understood — the
        # bug reported live as "hi cancel my order #1012" -> "I'm unable to
        # pull up your order... could you share your email or order number?"
        if not order_id:
            result["action_context"] = (
                "ACTION REQUIRED: Ask customer for their order number and email to verify eligibility. "
                "Do NOT assume or guess order details."
            )
            return result

        # ── Duplicate-request guard (PART 6) — "I want to return this" ->
        # "just checking, please do it" -> "did you do it?" must never
        # create a second refund/cancel action for the same order. Checked
        # BEFORE the (real Shopify) eligibility fetch, both for cost and so
        # a genuine status question gets an accurate, current answer rather
        # than a fresh re-evaluation. ────────────────────────────────────
        existing_action = await self._find_active_action(tenant_id, order_id, "refund")
        if not existing_action:
            existing_action = await self._find_active_action(tenant_id, order_id, "cancel_order")

        # "return" is never short-circuited by this guard at all — see the
        # dedicated return-handling block after eligibility is fetched
        # below. There is no "return" executable action type in this system
        # (return and refund share the refund mutation, but only when a
        # human has actually decided that's the outcome), so an existing
        # refund/cancel_order record - pending, approved, or even executed -
        # is never grounds to claim a fresh return request is "already
        # covered": a refund can be issued for reasons that never involved
        # the physical item coming back, and a still-pending cancellation
        # may never even execute (see below). existing_action is preserved
        # (not discarded) so the return branch can still mention it as
        # honest context, never as something that resolves the return ask.
        if existing_action and intent_type != "return":
            # An EXECUTED cancel_order for THIS order already produced the
            # refund the customer is now separately asking about —
            # established, existing business logic, not a new rule:
            # shopify_service.cancel_order() only ever runs for an
            # unfulfilled order (Shopify itself rejects cancelling a
            # fulfilled one — see its ORDER_ALREADY_FULFILLED check) and
            # Shopify auto-refunds a paid order's payment on cancellation;
            # actions_service.py's own cancel_order confirmation email
            # already tells the customer "your refund will appear within
            # 3–5 business days" as part of the SAME action. So an EXECUTED
            # cancel_order match genuinely satisfies a refund intent
            # for the same order — one coherent answer, not two
            # ("cancellation pending" + "I've also noted your refund").
            #
            # A cancel_order that hasn't executed yet (still "pending",
            # "approved", or "awaiting_manual_step" — see
            # _find_active_action) is NOT the same guarantee: cancel_order()
            # hard-rejects a fulfilled order at execution time, so if this
            # order has since become fulfilled (as it may well have, between
            # whenever that cancellation was staged and now), that pending
            # cancellation will never actually execute and never produce a
            # refund. Confirmed live: a fulfilled order's genuine refund
            # request got told "your cancellation request ... will also
            # cover the refund you're asking about" off a still-pending
            # cancel_order — false, and the customer was left with neither.
            # Falls through to a fresh eligibility check below instead,
            # exactly as if no existing action had matched at all — the
            # earlier cancel request is left completely untouched, never
            # re-staged or duplicated here.
            cancel_order_resolved = (
                existing_action.get("action_type") == "cancel_order"
                and existing_action.get("status") == "executed"
            )
            unresolved_cancel_order_for_refund_intent = (
                intent_type == "refund"
                and existing_action.get("action_type") == "cancel_order"
                and not cancel_order_resolved
            )
            if not unresolved_cancel_order_for_refund_intent:
                result["duplicate_of_existing_action"] = existing_action
                # intent_type == "return" can never reach this point at all
                # (see the guard above this whole block) - "return" is
                # included in the tuple only for symmetry with the merged
                # _cancellation_covers_refund_context signature below, which
                # now takes intent_type for correct return/refund wording on
                # the *other* remaining caller (the "return" case here is
                # dead but harmless).
                if intent_type in ("refund", "return") and cancel_order_resolved:
                    result["action_context"] = self._cancellation_covers_refund_context(existing_action, intent_type)
                else:
                    # Never the reverse: a pending refund action does NOT
                    # retroactively cancel the order, so a "cancel" intent
                    # matching an existing "refund" action still gets the
                    # plain, type-accurate duplicate wording here.
                    result["action_context"] = self._duplicate_status_context(existing_action, intent_type)
                return result

        await _emit("order_lookup", f"Finding order #{order_id}…")
        eligibility = await self.actions.check_return_eligibility(
            order_id, email, tenant_id=tenant_id, brand_id=brand_id
        )
        result["eligibility"] = eligibility
        if eligibility.get("order"):
            await _emit("order_found", "Shopify order found")
        await _emit("eligibility_check", f"Checking {_noun} eligibility…")

        order_data = eligibility.get("order", {}) or {}
        fulfillment_status = order_data.get("fulfillment_status")
        is_unfulfilled = fulfillment_status != "fulfilled"

        # Already handled — never re-stage a cancel/refund for an order the
        # eligibility check itself says was already cancelled/refunded/
        # returned. Checked before the unfulfilled fast-path below since
        # that path only looks at fulfillment_status, not at whether the
        # order is already closed out.
        already_handled_reason = eligibility.get("reason") or ""
        if not eligibility.get("eligible") and any(
            phrase in already_handled_reason.lower()
            for phrase in ("already been refunded", "already been cancelled", "already been returned",
                           "already refunded", "already cancelled", "already returned")
        ):
            result["action_context"] = (
                f"**{intent_type.upper()} NOT NEEDED**: {already_handled_reason} "
                f"Do NOT process the {intent_type} again. Tell the customer the truthful current status."
            )
            return result

        # ── RETURN — never automated yet (Part 1 of the intent/action
        # foundation rework). There is no "return" executable action type in
        # this system, and staging a "refund" or "cancel_order" record as a
        # silent substitute is exactly the RETURN -> REFUND/CANCEL
        # contamination this branch exists to stop (confirmed live: a
        # fulfilled order's plain return request was answered as if an
        # unrelated, still-pending cancel_order for that order "also covered"
        # it - the customer never asked to cancel anything). A return always
        # escalates to a human here: no action of any kind is staged, no
        # refund/cancellation is ever claimed to be in progress. The real
        # order/policy/identity context already available from the
        # eligibility check above (and any existing_action found by the
        # duplicate-guard, purely as informational context - never as
        # something that resolves this request) all flow into one honest,
        # human-handoff reply.
        if intent_type == "return":
            resolution = self._resolve_return(order_id, eligibility, is_unfulfilled, existing_action)
            # Adapter-layer safety net (PART 2/3 Phase 4), redundant with
            # ReturnSpecialist.resolve()'s own assertion: this call site is
            # the ONLY place RETURN could reach _stage_gated_action/
            # _create_action, and it never does — the Resolution returned
            # here is used only to build result["action_context"]/logging
            # below, never passed to _stage_gated_action. Asserted, not just
            # assumed, so any future edit that accidentally wired RETURN
            # into the executable-action gate fails loudly here first.
            assert resolution.requested_action_type is None, "RETURN must never request an executable action"
            result["action_context"] = resolution.customer_facing_note
            if resolution.resolution_type == "return_identity_unverified":
                return result
            logger.info(f"[ReturnActions] Return escalated to human, no action staged: {resolution.reasoning}")
            await _emit("staging_action", "Reviewing your return request…")
            return result

        # UNFULFILLED → cancel is right (not refund). Only when we actually
        # HAVE order data confirming this — an unverified/not-found order
        # (order_data empty) must fall through to manual review below, never
        # be assumed unfulfilled-therefore-cancel.
        if order_data and is_unfulfilled and not eligibility.get("eligible"):
            # This branch returns before check_return_eligibility ever loads
            # a refund policy (it exits at the fulfillment-status check), so
            # a merchant's free-text cancellation restriction (e.g. "orders
            # can only be cancelled within 1 hour") would otherwise never be
            # consulted at all. Same safety check the eligible-return path
            # uses — never auto-approved past policy text that was never
            # actually checked.
            cancel_policy_text = await self.actions.get_custom_policy_text(brand_id)

            # Deterministic time-window check FIRST, before the blanket
            # "needs a human" escalation below - a merchant policy like
            # "cancellation within 2 hours" is a fact Shopify's real
            # order.created_at can settle outright, not something that
            # needs a human to eyeball. Only overrides the generic
            # escalation when it produces a definitive answer (real window
            # text + a real order timestamp); a policy that isn't a simple
            # "within N hours/days" window, or a missing timestamp, still
            # falls through to the existing manual-review behavior below
            # unchanged - never guessed.
            window_check = self.actions.evaluate_cancellation_window(
                cancel_policy_text, order_data.get("created_at")
            )
            if window_check and not window_check["eligible"]:
                result["action_context"] = (
                    f"**{intent_type.upper()} NOT ELIGIBLE**: This order was placed "
                    f"{window_check['elapsed_hours']:.1f} hours ago. Store policy only allows "
                    f"cancellation within {window_check['window_hours']:.0f} hours of placing the order. "
                    "This order is outside that window. Tell the customer clearly that the order can no "
                    "longer be cancelled per store policy - do NOT say it 'might still' be eligible, and "
                    "do NOT create a cancellation request."
                )
                return result

            # None = the check couldn't be completed (unknown, not
            # confirmed-empty) - treated the same as real policy text.

            # Policy Evidence layer: if that free-text policy expresses a
            # confidently-parseable "cancel within N hours/days" condition,
            # verify it deterministically against the order's real Shopify
            # creation timestamp instead of blindly escalating every custom
            # policy to a human — this is the exact "2-hour cancellation
            # window" case that previously always fell into manual review
            # regardless of content. Anything the regex can't confidently
            # parse (window_result is None, or status UNKNOWN) still falls
            # through to the existing escalate-for-human-review branch
            # below, unchanged. RAG found the policy; this only checks
            # whether THIS order satisfies it — the LLM never decides this.
            window_result = None
            if cancel_policy_text:
                window_result = verify_time_window(
                    cancel_policy_text, order_data.get("created_at"), keywords=["cancel"],
                )
                logger.info(
                    f"[PolicyEvidence] cancellation window check ticket={ticket_id} order=#{order_id}: "
                    f"status={window_result['status']} reason={window_result['reason']} "
                    f"window_hours={window_result['evidence'].get('policy_window_hours')} "
                    f"elapsed_hours={window_result['evidence'].get('elapsed_hours')}"
                )

            if window_result and window_result["status"] == "INELIGIBLE":
                ev = window_result["evidence"]
                await _emit("policy_verified", "Cancellation policy checked — window expired")
                result["action_context"] = (
                    f"**CANCELLATION NOT ELIGIBLE**: Verified — this order was placed "
                    f"{ev['elapsed_hours']:.1f} hours ago, outside the store's "
                    f"{ev['policy_window_hours']:.0f}-hour cancellation window. Do NOT cancel the order and "
                    "do NOT create a cancellation action. Tell the customer factually, based on these real "
                    "numbers, that the order is outside the cancellation window — never say it 'might' still "
                    "qualify. Offer to check what other options (like a return once delivered, if eligible) "
                    "might help."
                )
                return result

            window_verified_eligible = bool(window_result and window_result["status"] == "ELIGIBLE")
            if window_verified_eligible:
                await _emit("policy_verified", "Cancellation policy verified — within window")

            if cancel_policy_text != "" and not window_verified_eligible:
                # PART 2/3 Phase 7 — Cancellation Specialist boundary (CP1).
                # Merchant-facing reason stays short (a card a human can read
                # in 5-10 seconds) - the raw, possibly multi-document RAG
                # lookup (Store Pages/FAQ Pages) never goes in this field.
                # It's still not discarded: a bounded excerpt goes into
                # extracted_data.policy_evidence for the dashboard's
                # expandable "View policy evidence" section.
                resolution = CancellationSpecialist.resolve_unfulfilled_manual_review(
                    order_id, intent_type, cancel_policy_text,
                )
                assert resolution.requested_action_type in (None, "cancel_order"), "Cancellation must never request a non-cancel_order/None action here"
                policy_evidence = _policy_evidence_excerpt(cancel_policy_text)
                await _emit("staging_action", f"Preparing your {_noun} request…")
                # Unfulfilled order -> Shopify cancel_order (which also
                # refunds the payment), never a separate "refund" action -
                # same convention as the no-custom-policy branch just below.
                # Getting this wrong here previously broke dedup too: a
                # mismatched action_type meant the guard above could not
                # recognize this as "already handled" on a repeat request,
                # letting a second (correctly-typed) action get created for
                # the same order.
                staged = await self._stage_gated_action(
                    resolution.resolution_type, resolution.requested_action_type,
                    tenant_id=tenant_id, brand_id=brand_id, ticket_id=ticket_id,
                    order_id=order_id, email=email,
                    customer_name=customer_info.get("name"), query=query,
                    ai_reasoning=resolution.reasoning, eligibility=eligibility,
                    policy_evidence=policy_evidence,
                    customer_intent=intent_type,
                )
                result["staged"] = staged
                result["action_context"] = resolution.customer_facing_note
                return result

            # PART 2/3 Phase 7 — Cancellation Specialist boundary (CP4).
            policy_evidence = _policy_evidence_excerpt(cancel_policy_text) if window_verified_eligible else None
            resolution = CancellationSpecialist.resolve_unfulfilled_eligible(
                order_id, intent_type, window_verified_eligible,
                window_result["evidence"] if window_verified_eligible else None,
            )
            assert resolution.requested_action_type in (None, "cancel_order"), "Cancellation must never request a non-cancel_order/None action here"
            await _emit("staging_action", f"Preparing your {_noun} request…")
            staged = await self._stage_gated_action(
                resolution.resolution_type, resolution.requested_action_type,
                tenant_id=tenant_id, brand_id=brand_id, ticket_id=ticket_id,
                order_id=order_id, email=email,
                customer_name=customer_info.get("name"), query=query,
                ai_reasoning=resolution.reasoning, eligibility=eligibility,
                policy_evidence=policy_evidence,
                customer_intent=intent_type,
            )
            result["staged"] = staged

            # Cancellation Autopilot: reachable here in two cases, both
            # already fully deterministic — either no merchant free-text
            # policy exists at all, or one exists and was just verified
            # (above) against the order's real Shopify timestamp as
            # confidently within its window. Any policy text that couldn't
            # be confidently parsed, or that verification found expired,
            # already exited above — every time. This never weakens
            # _maybe_autopilot_cancel's own independent safety re-check
            # (fresh Shopify re-verification, brand autopilot flag,
            # idempotency) below; it only decides which staged actions are
            # even offered to it. The backend alone decides whether to
            # auto-execute from here; Luna's own judgment is never consulted
            # or trusted as authorization.
            autopilot_context = await self._maybe_autopilot_cancel(
                tenant_id=tenant_id, brand_id=brand_id, staged=staged, order_id=order_id,
            )
            if autopilot_context:
                result["action_context"] = autopilot_context
                return result

            result["action_context"] = resolution.customer_facing_note
            return result

        # NOT ELIGIBLE and fulfilled
        if not eligibility.get("eligible"):
            if eligibility.get("identity_mismatch"):
                # Never staged, never escalated for this reason alone - see
                # check_return_eligibility's own comment for the confirmed-live
                # incident (a "manual review" action from this exact case
                # reached "executed"). Nothing is pending anywhere for this
                # request, so the reply must resolve it in one message, not
                # promise a follow-up that will never happen.
                #
                # PART 2/3 Phase 6 — a genuine refund intent's identity-
                # mismatch text is now owned by RefundSpecialist; cancel
                # (and any other non-refund intent still reachable here)
                # keeps this exact inline wording, byte for byte unchanged.
                if intent_type == "refund":
                    resolution = RefundSpecialist.resolve_identity_mismatch(order_id)
                    assert resolution.requested_action_type is None, "REFUND identity-mismatch must never request an executable action"
                    result["action_context"] = resolution.customer_facing_note
                else:
                    result["action_context"] = (
                        f"**IDENTITY UNVERIFIED - DO NOT PROCESS {intent_type.upper()}**: The email this "
                        "customer is contacting from does not match the email on file for this order. "
                        "Do NOT create a request. Do NOT say this has been escalated, sent to the team, or "
                        "that anyone will follow up - nothing has been submitted anywhere. Resolve this in "
                        "this one reply: state plainly that the order was found but the contact email "
                        "doesn't match the one on the order, so for security this request can't be "
                        f"processed from this email, and ask the customer to reach out to us from the email "
                        "address used when placing the order so we can help right away."
                    )
            elif eligibility.get("staging_required") or eligibility.get("requires_manual_review"):
                # eligibility["reason"] is a short, deterministic string from
                # check_return_eligibility (e.g. "order not yet fulfilled")
                # - safe to keep here. custom_policy_text is the raw,
                # possibly multi-document RAG lookup - never appended to
                # this short reason (see _policy_evidence_excerpt below).
                #
                # action_type is deliberately ALWAYS "refund" here, same as
                # before - never _ACTION_TYPE_MAP.get(intent_type). Unlike
                # the "eligible + unfulfilled" branches above (which know
                # FOR CERTAIN the order is unfulfilled, so cancel_order is
                # guaranteed to succeed), this branch is reached specifically
                # when the order's fulfillment status is NOT confirmed
                # unfulfilled - either it wasn't found at all, or it's
                # fulfilled-and-ineligible. shopify_service.cancel_order()
                # hard-rejects a fulfilled order (ORDER_ALREADY_FULFILLED),
                # with no retry path other than a human working around the
                # action entirely - so staging as "cancel_order" here would
                # trade one bug for a worse one (a permanently-failing
                # action) on every fulfilled order that reaches this branch.
                # "refund" always executes.
                #
                # PART 2/3 Phase 6 — a genuine refund (or return, which
                # shares this wording) intent's manual-review decision is
                # now owned by RefundSpecialist. Cancel's fallback-to-refund
                # substitution below (with its own disclosure wording) is
                # NOT a refund decision - it's cancellation's own consolation
                # resolution reusing the refund executable type, left
                # completely untouched here (PART 2/3 Phase 7's territory).
                if intent_type in ("refund", "return"):
                    requested_amount = self._extract_requested_refund_amount(query)
                    resolution = RefundSpecialist.resolve_manual_review(order_id, eligibility, requested_amount)
                    assert resolution.requested_action_type in (None, "refund"), "REFUND must never request a non-refund executable action"
                    await _emit("staging_action", f"Preparing your {_noun} request…")
                    staged = await self._stage_gated_action(
                        resolution.resolution_type, resolution.requested_action_type,
                        tenant_id=tenant_id, brand_id=brand_id, ticket_id=ticket_id,
                        order_id=order_id, email=email,
                        customer_name=customer_info.get("name"), query=query,
                        ai_reasoning=resolution.reasoning, eligibility=eligibility,
                        policy_evidence=_policy_evidence_excerpt(eligibility.get("custom_policy_text")),
                        requested_amount=requested_amount,
                        customer_intent=intent_type,
                    )
                    result["staged"] = staged
                    result["action_context"] = resolution.customer_facing_note
                    return result

                # PART 2/3 Phase 7 — Cancellation Specialist boundary (CP6).
                # What WAS a real, confirmed-live bug: this reasoning text
                # silently described the customer's actual ask (e.g. "cancel")
                # while the stored action_type said "refund" - a reviewer
                # would see a self-contradictory card (type badge: Refund;
                # reason: "Customer requests cancel_order..."). Fixed by
                # disclosing the substitution in the text itself whenever the
                # customer's intent isn't already a refund/return, so nothing
                # AI-decided is hidden from the human who has to act on it.
                resolution = CancellationSpecialist.resolve_fulfilled_unverifiable_fallback_to_refund(order_id, eligibility)
                assert resolution.requested_action_type in (None, "refund"), "Cancellation's fallback-to-refund must never request a non-refund/None action here"
                policy_evidence = _policy_evidence_excerpt(eligibility.get("custom_policy_text"))
                await _emit("staging_action", f"Preparing your {_noun} request…")
                staged = await self._stage_gated_action(
                    resolution.resolution_type, resolution.requested_action_type,
                    tenant_id=tenant_id, brand_id=brand_id, ticket_id=ticket_id,
                    order_id=order_id, email=email,
                    customer_name=customer_info.get("name"), query=query,
                    ai_reasoning=resolution.reasoning, eligibility=eligibility,
                    policy_evidence=policy_evidence,
                    requested_amount=self._extract_requested_refund_amount(query),
                    customer_intent=intent_type,
                )
                result["staged"] = staged
                result["action_context"] = resolution.customer_facing_note
            else:
                result["action_context"] = (
                    f"**{intent_type.upper()} NOT ELIGIBLE**: {eligibility.get('reason')}. "
                    f"Do NOT process the {intent_type}. Acknowledge and offer to escalate to human support if frustrated."
                )
            return result

        # ELIGIBLE → stage the refund (a "return" IS a refund here — see
        # this block's header comment for why there's no separate action type)
        await _emit("policy_verified", f"{_noun.capitalize()} policy verified")
        items = eligibility.get("items", [])

        # Partial-order return (PART 3/11): this integration has no
        # line-item-specific refund mutation — process_refund() takes a
        # single dollar amount for the whole order, and the human approver
        # already decides that amount at approval time (see
        # actions_service.approve_action's override_amount — "typed by the
        # approver ... never AI-extracted"). What IS this function's job:
        # never let a "just the hoodie, not the rest" request get lost by
        # the time it reaches that human. Reuses the exact same item-name
        # matcher the exchange flow uses to identify a single named item —
        # never guessed, only surfaced when there's a real, only match.
        specific_item = self._match_order_item(query, items) if len(items) > 1 else None

        # PART 2/3 Phase 6 — Refund Specialist boundary. The genuine
        # refund-eligible happy path: a fulfilled order (is_unfulfilled
        # False) the customer explicitly asked to refund. Every other
        # combination reaching this point (any refund request against an
        # eligible-but-UNFULFILLED order, every cancel intent, "return"
        # here still shares refund's mutation) stays on the unchanged
        # shared path below — those are cancellation's own decisions even
        # when a refund intent happens to fall through to them (PART 2/3
        # Phase 7's territory), never touched by this specialist.
        if intent_type == "refund" and not is_unfulfilled:
            requested_amount = self._extract_requested_refund_amount(query)
            resolution = RefundSpecialist.resolve_eligible(order_id, eligibility, specific_item, requested_amount)
            assert resolution.requested_action_type == "refund", "REFUND eligible path must request exactly 'refund'"
            await _emit("staging_action", f"Preparing your {_noun} request…")
            staged = await self._stage_gated_action(
                resolution.resolution_type, resolution.requested_action_type,
                tenant_id=tenant_id, brand_id=brand_id, ticket_id=ticket_id,
                order_id=order_id, email=email,
                customer_name=customer_info.get("name"), query=query,
                ai_reasoning=resolution.reasoning, eligibility=eligibility,
                requested_amount=requested_amount,
                customer_intent=intent_type,
            )
            result["staged"] = staged

            if staged.get("success"):
                # Refund Autopilot: only ever the plain, whole-order, no-
                # ambiguity happy path (see _maybe_autopilot_refund's own
                # internal gating for the full list of conditions) — human
                # approval remains mandatory otherwise; this hook re-uses
                # the exact same approve_action() a human clicking Approve
                # calls, unchanged.
                autopilot_context = await self._maybe_autopilot_refund(
                    tenant_id=tenant_id, brand_id=brand_id, staged=staged, order_id=order_id,
                    query=query, is_unfulfilled=is_unfulfilled, specific_item=specific_item,
                )
                if autopilot_context:
                    result["action_context"] = autopilot_context
                    return result
                result["action_context"] = resolution.customer_facing_note
            else:
                result["action_context"] = (
                    f"Refund eligible but staging failed: {staged.get('message') or staged.get('error')}. "
                    "Process normally but flag for manual review."
                )
            return result

        # PART 2/3 Phase 7 — Cancellation Specialist boundary (CP8). The
        # genuine cancel-intent-on-a-fulfilled-eligible-order case: Shopify
        # cannot cancel a fulfilled order, so this always resolves to a
        # refund action instead — same fallback CP6 already makes, now with
        # the same explicit disclosure (this used to be undisclosed, shared,
        # intent-agnostic code — see the PART 2/3 Phase 7 inspection report).
        # Every other combination still reaching this point (any cancel
        # intent on an unfulfilled order — impossible per the eligibility
        # contract traced in the Phase 6 investigation, since eligible=True
        # structurally implies fulfilled — plus any other non-refund/cancel
        # intent) stays on the unchanged generic path below, unaltered.
        if intent_type == "cancel" and not is_unfulfilled:
            resolution = CancellationSpecialist.resolve_fulfilled_eligible_fallback_to_refund(
                order_id, eligibility, specific_item,
            )
            assert resolution.requested_action_type in (None, "refund"), "Cancellation's fallback-to-refund must never request a non-refund/None action here"
            await _emit("staging_action", f"Preparing your {_noun} request…")
            staged = await self._stage_gated_action(
                resolution.resolution_type, resolution.requested_action_type,
                tenant_id=tenant_id, brand_id=brand_id, ticket_id=ticket_id,
                order_id=order_id, email=email,
                customer_name=customer_info.get("name"), query=query,
                ai_reasoning=resolution.reasoning, eligibility=eligibility,
                requested_amount=self._extract_requested_refund_amount(query),
                customer_intent=intent_type,
            )
            result["staged"] = staged
            if staged.get("success"):
                result["action_context"] = resolution.customer_facing_note
            else:
                result["action_context"] = (
                    f"{intent_type.capitalize()} eligible but staging failed: {staged.get('message') or staged.get('error')}. "
                    "Process normally but flag for manual review."
                )
            return result

        item_names = ", ".join([i.get("title", "item") for i in items[:2]])
        if specific_item:
            ai_reasoning = (
                f"Customer requests {intent_type} for order #{order_id}, SPECIFICALLY ONLY: "
                f"{specific_item.get('title')} ({specific_item.get('variant_title') or 'one size'}), "
                f"not the full order ({item_names})."
            )
        else:
            ai_reasoning = f"Customer requests {intent_type} for order #{order_id}: {item_names}"

        await _emit("staging_action", f"Preparing your {_noun} request…")
        # This remaining path is reached for BOTH fulfilled orders (only a
        # refund is possible) and unfulfilled orders that eligibility
        # itself didn't flag as ineligible (the unfulfilled-specific branch
        # above only intercepts the *not*-eligible case) - so is_unfulfilled
        # still has to decide the action type here, exactly like it does
        # above, or a "cancel my order" request for an eligible-but-
        # unfulfilled order would wrongly stage a refund action instead of
        # a cancellation. (Previously also had a literal casing bug -
        # "Refund" instead of "refund" - which broke both dedup matching
        # above and approval in actions_service.py, since neither compares
        # against the exact stored string case-insensitively.)
        _resolved_action_type = "cancel_order" if is_unfulfilled else "refund"
        _resolution_type = "cancellation_eligible" if is_unfulfilled else "refund_eligible"
        staged = await self._stage_gated_action(
            _resolution_type, _resolved_action_type,
            tenant_id=tenant_id, brand_id=brand_id, ticket_id=ticket_id,
            order_id=order_id, email=email,
            customer_name=customer_info.get("name"), query=query,
            ai_reasoning=ai_reasoning, eligibility=eligibility,
            # A dollar figure only ever means something for a real refund —
            # cancel_order is a whole-order Shopify cancellation with no
            # partial-amount concept, so never attach one there.
            requested_amount=None if is_unfulfilled else self._extract_requested_refund_amount(query),
            customer_intent=intent_type,
        )
        result["staged"] = staged

        if staged.get("success"):
            noun = "return" if intent_type == "return" else "refund" if intent_type == "refund" else "cancellation"

            # Cancellation Autopilot is a separate, already-existing hook
            # elsewhere and is unaffected by this — this remaining path
            # never reaches Refund Autopilot (that only ever fires from the
            # genuine refund-eligible branch above).
            if specific_item:
                result["action_context"] = (
                    f"**ACTION STAGED FOR APPROVAL (PARTIAL — only {specific_item.get('title')})**: "
                    f"The customer wants to {noun} only {specific_item.get('title')}, not the rest of the order. "
                    "Our team will confirm the exact refund amount for that item specifically. "
                    f"Tell the customer: 'I've sent a request to my team to {noun} just the "
                    f"{specific_item.get('title')}, the rest of your order is unaffected. "
                    "You'll get a confirmation once they approve it.'"
                )
            else:
                result["action_context"] = (
                    f"**ACTION STAGED FOR APPROVAL**: Your {noun} request has been submitted for review. "
                    "Tell the customer: 'I've prepared your request for my team to review. "
                    "You'll get a confirmation once they approve it.'"
                )
        else:
            result["action_context"] = (
                f"{intent_type.capitalize()} eligible but staging failed: {staged.get('message') or staged.get('error')}. "
                "Process normally but flag for manual review."
            )

        return result

    def _resolve_return(
        self,
        order_id: str,
        eligibility: Dict[str, Any],
        is_unfulfilled: bool,
        existing_action: Optional[Dict[str, Any]],
    ) -> Resolution:
        """Adapter method — delegates to the Return Specialist boundary
        (PART 2/3 Phase 4, return_specialist.py's ReturnSpecialist.resolve).
        Kept as a method here (rather than calling ReturnSpecialist directly
        from handle_return_intent) purely so existing callers/tests that
        already reach the Return Specialist through `integration._resolve_return(...)`
        keep working unchanged; this file owns no return-decision logic of
        its own anymore — see ReturnSpecialist's own docstring for that."""
        return ReturnSpecialist.resolve(order_id, eligibility, is_unfulfilled, existing_action)

    async def _stage_gated_action(
        self, resolution_type: str, requested_action_type: str, **create_action_kwargs
    ) -> dict:
        """PART 2/3 Phase 2 — the one path refund/cancellation staging is
        allowed to reach _create_action through. Builds the minimal
        Resolution the shared gate (specialist_resolution.stage_resolution_action)
        needs and lets IT decide, against the whitelist, whether
        requested_action_type may actually be created for this
        resolution_type — never decided here. Raises
        ExecutableActionRejected (a programming-time contract violation,
        not a customer-facing failure) if a call site ever asks for a
        mapping the whitelist doesn't allow.

        Only wired into the refund/cancel_order call sites today — return
        already creates no action (see _resolve_return), and
        restore_order/address_change/reship/exchange stay on the direct
        _create_action path unchanged (outside this policy's whitelist;
        exchange's own gating is PART 2/3 Phase 5, not yet done)."""
        resolution = Resolution(
            resolution_type=resolution_type,
            specialist="refund" if resolution_type == "refund_eligible" else "cancellation",
            order_id=create_action_kwargs.get("order_id"),
            reasoning=create_action_kwargs.get("ai_reasoning", ""),
            customer_facing_note="",
            requested_action_type=requested_action_type,
        )
        return await stage_resolution_action(self, resolution, **create_action_kwargs)

    async def _find_active_action(
        self, tenant_id: Optional[str], order_id: Optional[str], action_type: str
    ) -> Optional[Dict[str, Any]]:
        """Is there already a pending/approved/executed action of this exact
        type for this order? Only meaningful when tenant_id is set (the real
        `actions` table is tenant-scoped) — the legacy pending_actions
        fallback path has no equivalent lookup and stays as it was. Fails
        open (returns None, so staging proceeds) on any DB error, consistent
        with every other guardrail in this codebase — a dedup-check outage
        must never be the reason a real request never reaches a human."""
        if not tenant_id or not order_id:
            return None
        try:
            existing = supabase_select("actions", {
                "tenant_id": f"eq.{tenant_id}",
                "order_id": f"eq.{order_id}",
                "action_type": f"eq.{action_type}",
                "status": "in.(pending,approved,executed,awaiting_manual_step)",
                "order": "created_at.desc",
                "limit": "1",
            })
            return existing[0] if existing else None
        except Exception as e:
            logger.warning(f"[ReturnActions] Dedup check failed for order {order_id} ({e}) — continuing without it")
            return None

    async def find_pending_actions_for_ticket(self, ticket_id: Optional[str]) -> List[Dict[str, Any]]:
        """The durable "is there an active action this reply might be
        confirming/continuing" signal, keyed by ticket_id alone - unlike
        _find_active_action above, which needs order_id/action_type
        already resolved (this is used BEFORE they're known, to decide
        whether to resolve them from conversation state at all). Same
        active-status set as the dedup check, same fail-open behavior.
        Returns every match (not just the latest) so the caller can detect
        real ambiguity — e.g. two pending actions for two different
        orders — and refuse to guess rather than silently picking one."""
        if not ticket_id:
            return []
        try:
            return supabase_select("actions", {
                "ticket_id": f"eq.{ticket_id}",
                "status": "in.(pending,approved,executed,awaiting_manual_step)",
                "order": "created_at.desc",
            }) or []
        except Exception as e:
            logger.warning(f"[ReturnActions] Pending-action lookup failed for ticket {ticket_id} ({e}) — continuing without it")
            return []

    def _duplicate_status_context(self, existing: Dict[str, Any], intent_type: str) -> str:
        """Truthful status wording for a repeat request against an action
        that's already pending/approved/executed — never re-stages, and
        never claims completion that hasn't actually happened. Reused by
        both the refund/return/cancel path and the exchange path.

        `noun` is derived from the EXISTING action's own `action_type`,
        never from the customer's current `intent_type` — the refund/cancel
        dedup check above intentionally matches either action_type against
        the same order (a pending cancellation also covers a later refund
        ask for the same order), so a customer asking for a refund can
        legitimately have this triggered by a `cancel_order` row. Wording
        it as "refund" in that case would tell the customer a refund is
        awaiting approval when the real, only record on file is a
        cancellation — a fabricated action-state claim. Always naming the
        record's actual type keeps every claim traceable to a real row."""
        noun = {
            "exchange": "exchange",
            "cancel_order": "cancellation",
            "refund": "refund",
            "reship": "reship",
            "change_address": "address change",
        }.get(existing.get("action_type"), "request")
        # A "refund"-typed row is the one ambiguous case: this REST-only
        # integration stages both genuine refund asks AND return asks as
        # action_type="refund" (see _create_action's customer_intent
        # docstring - there's no separate Shopify "return" mutation). If
        # the row's own extracted_data still remembers the customer
        # originally asked to RETURN, say that here instead of "refund" -
        # otherwise a later duplicate-request reply would tell the
        # customer a refund is pending when they actually asked to return
        # the item, exactly the contamination this whole fix closes.
        if existing.get("action_type") == "refund" and (existing.get("extracted_data") or {}).get("customer_intent") == "return":
            noun = "return"
        status = existing.get("status")
        if status == "pending":
            return (
                f"**{noun.upper()} ALREADY PENDING**: A {noun} request for this order is already awaiting "
                f"our team's approval (submitted earlier in this conversation or a prior message). "
                f"Do NOT create a new request or say a new one was sent. "
                f"Tell the customer: 'Your {noun} request is already with our team for approval, you'll hear "
                f"back once it's reviewed, no need to send it again.'"
            )
        if status == "approved":
            return (
                f"**{noun.upper()} APPROVED, BEING PROCESSED**: This {noun} was already approved and is being "
                f"finished now. Do NOT create a new request. "
                f"Tell the customer: 'Good news, your {noun} was approved and we're finishing it up now.'"
            )
        if status == "executed":
            execution_result = existing.get("execution_result") or {}
            if execution_result.get("manual_action_required"):
                return (
                    f"**{noun.upper()} APPROVED — TEAM FINISHING LAST STEP**: Do NOT create a new request. "
                    f"Tell the customer honestly: 'Yes, your {noun} was approved. Our team is completing the "
                    f"last step manually and you'll get a confirmation shortly.' Do NOT say it is fully complete."
                )
            return (
                f"**{noun.upper()} ALREADY COMPLETED**: This {noun} was already processed successfully. "
                f"Do NOT create a new request. Tell the customer it's done — reference only real details "
                f"actually present here (do not invent an amount, item, or date if none is given)."
            )
        return f"**{noun.upper()} ALREADY ON FILE** (status: {status}). Do NOT create a new request — ask the customer to give our team a moment."

    def _cancellation_covers_refund_context(self, existing: Dict[str, Any], intent_type: str = "refund") -> str:
        """Only called when the customer's CURRENT intent is refund/return
        but the only real record found is a `cancel_order` action (see the
        refund/cancel duplicate-request guard above). Confirmed-live bug:
        without this, the caller fell back to `_duplicate_status_context`,
        whose wording only ever talks about the cancellation — it never
        tells the model the refund the customer just asked about is the
        SAME outcome, not a separate open question. The model then filled
        that gap itself, producing one reply that both restated the
        cancellation AND separately claimed "I've noted your refund
        request" — a second, entirely fabricated promise with no backing
        action, on top of the first (real) one.

        This is one-directional and never a guess: shopify_service's
        cancel_order() only ever runs for an unfulfilled order (Shopify
        itself refuses to cancel a fulfilled one), and Shopify auto-refunds
        a cancelled paid order's payment — actions_service.py's own
        cancel_order confirmation email already promises the customer
        "your refund will appear within 3–5 business days" as part of
        executing this SAME action, not a separate one. A pending refund
        action, by contrast, never cancels the order — so this is only
        ever invoked for cancel_order-satisfies-refund, never the reverse.

        intent_type: the customer's CURRENT ask ("refund" or "return" - the
        only two values the caller ever passes here). All the wording below
        used to hardcode "refund" regardless, so a customer who asked to
        RETURN an order that already has a pending cancellation would be
        told the cancellation "covers the refund you're asking about" -
        putting a word in the customer's mouth they never said. `noun` below
        makes every sentence match what was actually asked."""
        noun = "return" if intent_type == "return" else "refund"
        status = existing.get("status")
        if status == "pending":
            return (
                f"**EXISTING CANCELLATION ALREADY COVERS THIS {noun.upper()} REQUEST**: This order already has "
                f"a cancellation request awaiting our team's approval, and cancelling it will also "
                f"produce the {noun} the customer is now asking about — they are the SAME outcome, not "
                f"two separate things. Do NOT create a new {noun} request. Do NOT say you've 'also "
                f"noted' or separately logged a {noun} request — there is only one pending request, and "
                "mentioning a second implies a promise nothing backs. Tell the customer ONE coherent "
                "thing: 'Your cancellation request for this order is already pending approval — that "
                f"process covers the {noun} you're asking about, so you don't need to submit a separate "
                "request. You'll hear back once it's reviewed.'"
            )
        if status == "approved":
            return (
                f"**CANCELLATION (COVERING THIS {noun.upper()}) APPROVED, BEING PROCESSED**: Do NOT create a new "
                f"{noun} request. Tell the customer: 'Good news — your cancellation was approved and "
                f"we're finishing it up now. That includes the {noun} you asked about, which follows "
                "automatically once the cancellation completes.'"
            )
        if status == "executed":
            execution_result = existing.get("execution_result") or {}
            if execution_result.get("manual_action_required"):
                return (
                    f"**CANCELLATION (COVERING THIS {noun.upper()}) APPROVED — TEAM FINISHING LAST STEP**: Do NOT "
                    f"create a new {noun} request. Tell the customer honestly: 'Yes, your cancellation "
                    f"was approved, which includes the {noun} you asked about. Our team is completing "
                    f"the last step manually and you'll get a confirmation shortly.' Do NOT say the "
                    f"{noun} has already landed."
                )
            return (
                f"**CANCELLATION (COVERING THIS {noun.upper()}) ALREADY COMPLETED**: This order was already "
                f"cancelled, and that included the {noun} the customer is asking about. Do NOT create a "
                f"new {noun} request. Tell the customer it's done — reference only real details actually "
                "present here (do not invent an amount or date if none is given)."
            )
        return (
            f"**CANCELLATION (COVERING THIS {noun.upper()}) ALREADY ON FILE** (status: {status}). Do NOT create "
            f"a new {noun} request — ask the customer to give our team a moment."
        )

    async def _handle_exchange(
        self,
        query: str,
        customer_info: Dict[str, Any],
        order_id: Optional[str],
        email: Optional[str],
        intent_result: IntentResult,
        tenant_id: Optional[str],
        brand_id: Optional[str],
        ticket_id: Optional[str],
        result: Dict[str, Any],
        _emit: Callable[[str, str], Awaitable[None]],
    ) -> Dict[str, Any]:
        """Full exchange context-gathering: order + customer verification,
        the exact same eligibility/policy check returns and refunds use,
        item identification (asks rather than guesses on a multi-item
        order), LIVE Shopify target-variant resolution (never stale RAG/
        product memory), availability + price-difference checks. This
        adapter method owns all of that I/O; the actual decision of what to
        tell the customer is delegated to ExchangeSpecialist (PART 2/3
        Phase 5) at the two points where a real decision is made (below).
        Current product policy: exchange is not automated — every genuine
        exchange request escalates to a human, NEVER creating an
        executable action of any kind (not exchange, not refund/cancel as
        a substitute) — never a fabricated "your exchange is being
        processed" either way."""
        result["return_checked"] = True

        if not order_id or not email:
            result["action_context"] = (
                "ACTION REQUIRED: Ask customer for their order number and email to verify eligibility "
                "before we can check exchange availability. Do NOT assume or guess order details."
            )
            return result

        # Duplicate-request guard — same mechanism as refund/return/cancel above.
        existing_action = await self._find_active_action(tenant_id, order_id, "exchange")
        if existing_action:
            result["action_context"] = self._duplicate_status_context(existing_action, "exchange")
            result["duplicate_of_existing_action"] = existing_action
            return result

        await _emit("order_lookup", f"Finding order #{order_id}…")
        eligibility = await self.actions.check_return_eligibility(
            order_id, email, tenant_id=tenant_id, brand_id=brand_id
        )
        result["eligibility"] = eligibility
        if (eligibility.get("order") or {}):
            await _emit("order_found", "Shopify order found")
        await _emit("eligibility_check", "Checking return eligibility…")

        if not eligibility.get("eligible"):
            if eligibility.get("staging_required") or eligibility.get("requires_manual_review"):
                # PART 2/3 Phase 5 — Exchange Specialist boundary. There is
                # no live target variant/price-difference data to attach
                # (eligibility itself failed), and current product policy
                # is that exchange is not automated at all: no action of
                # ANY type (not exchange, not refund as a substitute) is
                # ever created here — always escalate to a human instead.
                await _emit("staging_action", "Reviewing your exchange request…")
                resolution = ExchangeSpecialist.resolve_eligibility_unclear(order_id, eligibility)
                assert resolution.requested_action_type is None, "EXCHANGE must never request an executable action"
                result["action_context"] = resolution.customer_facing_note
                logger.info(f"[ReturnActions] Exchange escalated to human, no action staged: {resolution.reasoning}")
            else:
                result["action_context"] = (
                    f"**EXCHANGE NOT ELIGIBLE**: {eligibility.get('reason')}. "
                    "Do NOT process the exchange. Acknowledge and offer to escalate to human support if frustrated."
                )
            return result

        await _emit("policy_verified", "Return policy verified")
        items = eligibility.get("items", [])
        if not items:
            result["action_context"] = (
                "**NO ITEMS FOUND ON ORDER**: Tell the customer we couldn't find items on this order to exchange, "
                "and offer to escalate to our team."
            )
            return result

        # ── Which item? (PART 3 — never exchange the whole order when the
        # customer only meant one item) ──────────────────────────────────
        original_item = self._match_order_item(query, items) if len(items) > 1 else items[0]
        if not original_item:
            titles = ", ".join(f"{i.get('title')} ({i.get('variant_title') or 'one size'})" for i in items)
            result["action_context"] = (
                f"**WHICH ITEM? — DO NOT GUESS**: This order has multiple items: {titles}. "
                "Ask the customer exactly which item they want to exchange before doing anything else. "
                "Do NOT create an action yet."
            )
            return result

        # ── What do they want instead? ───────────────────────────────────
        target_description = intent_result.exchange_target
        if not target_description:
            result["action_context"] = (
                f"**WHAT REPLACEMENT? — DO NOT GUESS**: The customer wants to exchange "
                f"\"{original_item.get('title')}\" but hasn't said what they want instead "
                "(size, color, or a different product). Ask them before doing anything else. "
                "Do NOT create an action yet."
            )
            return result

        # ── LIVE Shopify grounding — eligibility's own items list is
        # trimmed and has no product_id/variant_id, so the real line item
        # is re-fetched to get them. Never trust stale RAG/product memory
        # for the actual swap. ────────────────────────────────────────────
        raw_item = await self._get_raw_line_item(tenant_id, order_id, original_item)
        if not raw_item:
            result["action_context"] = (
                "**COULDN'T VERIFY LIVE ITEM DETAILS**: We couldn't confirm current product details for this "
                "item. Tell the customer we need a moment to verify with our team, and escalate. "
                "Do NOT create an action yet."
            )
            return result

        await _emit("exchange_search", "Finding eligible replacement…")
        target = await self.actions.find_exchange_target(tenant_id, raw_item, target_description)
        result["exchange"] = target

        if not target.get("found"):
            result["action_context"] = self._exchange_target_failure_context(target, original_item)
            return result

        original_price = float(original_item.get("price") or 0)
        price_difference = round(target["price"] - original_price, 2)

        # PART 2/3 Phase 5 — Exchange Specialist boundary. A live, in-stock
        # replacement was found (this used to be the "everything checks
        # out" happy path that auto-staged a real "exchange" action,
        # regardless of whether the price difference was negative, zero, or
        # positive). Current product policy: exchange is not automated at
        # all — always escalate to a human instead, no action of any kind,
        # while still preserving exactly what was found (item, replacement,
        # price difference) so nothing useful is lost.
        await _emit("staging_action", "Preparing your exchange request…")
        resolution = ExchangeSpecialist.resolve_target_found(
            order_id=order_id, original_item=original_item, target=target,
            price_difference=price_difference,
        )
        assert resolution.requested_action_type is None, "EXCHANGE must never request an executable action"
        result["action_context"] = resolution.customer_facing_note
        logger.info(f"[ReturnActions] Exchange escalated to human, no action staged: {resolution.reasoning}")
        return result

    def _match_order_item(self, query: str, items: List[Dict]) -> Optional[Dict]:
        """Which line item is the customer talking about? Whole-word,
        case-insensitive title match against the message text. Returns None
        (never a guess) unless exactly one item matches."""
        q = query.lower()
        import re as _re

        def _whole_word(text: str) -> bool:
            return bool(text) and bool(_re.search(r'\b' + _re.escape(text.lower()) + r'\b', q))

        exact = [i for i in items if _whole_word(i.get("title", ""))]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            return None  # ambiguous full-title match — never guess

        # Fall back to a distinctive word from the title (e.g. "hoodie" for
        # "Essential Hoodie") — real customers rarely type the full product
        # title. Only counts if the word belongs to exactly one item.
        _STOPWORDS = {"the", "and", "with", "for", "size", "item", "color", "default", "title"}

        def _sig_words(title: str) -> set:
            return {w for w in _re.findall(r"[a-z0-9']+", title.lower()) if len(w) >= 4 and w not in _STOPWORDS}

        word_sets = [_sig_words(i.get("title", "")) for i in items]
        candidates = []
        for idx, item in enumerate(items):
            others = set().union(*(w for j, w in enumerate(word_sets) if j != idx)) if len(items) > 1 else set()
            distinctive = word_sets[idx] - others
            if any(_whole_word(w) for w in distinctive):
                candidates.append(item)
        return candidates[0] if len(candidates) == 1 else None

    async def _get_raw_line_item(
        self, tenant_id: Optional[str], order_id: str, matched_item: Dict
    ) -> Optional[Dict]:
        """Re-fetch the order's RAW Shopify line item for the identified
        item — eligibility's own items list (check_return_eligibility's
        _extract_items()) is trimmed to id/title/variant_title/quantity/
        price/sku and has no product_id/variant_id, which live exchange-
        target matching needs. Matches by line_item id first (exact),
        falling back to sku then title+variant so a match is still found
        even if the id field is ever absent."""
        if not tenant_id:
            return None
        try:
            from src.services.shopify_service import shopify_service
            client = await shopify_service.get_client_for_tenant(tenant_id)
            order_resp = await client.get_order(order_id)
            if not order_resp.get("success"):
                return None
            line_items = order_resp["order"].get("line_items", [])
            if matched_item.get("id"):
                for li in line_items:
                    if li.get("id") == matched_item.get("id"):
                        return li
            if matched_item.get("sku"):
                for li in line_items:
                    if li.get("sku") == matched_item.get("sku"):
                        return li
            for li in line_items:
                if li.get("title") == matched_item.get("title") and li.get("variant_title") == matched_item.get("variant_title"):
                    return li
        except Exception as e:
            logger.warning(f"[ReturnActions] Could not re-fetch raw line item for exchange: {e}")
        return None

    def _exchange_target_failure_context(self, target: Dict[str, Any], original_item: Dict[str, Any]) -> str:
        """Honest, specific explanation for every way find_exchange_target()
        can fail to resolve a real, in-stock replacement — never a generic
        'something went wrong', and never a promise the exchange will happen."""
        reason = target.get("reason")
        item_name = original_item.get("title", "this item")

        if reason == "no_shopify_connection":
            return (
                "**CAN'T CHECK LIVE AVAILABILITY**: Shopify isn't connected for this store right now. "
                "Tell the customer we need our team to check availability manually, and escalate."
            )
        if reason == "target_not_specified":
            return (
                f"**WHAT REPLACEMENT? — DO NOT GUESS**: Ask the customer what they'd like instead of "
                f"{item_name} (a size, color, or different product) before doing anything else."
            )
        if reason == "product_unavailable":
            return (
                f"**ORIGINAL PRODUCT NO LONGER AVAILABLE**: {item_name} is no longer in our catalog, so we "
                "can't check exchange options automatically. Tell the customer our team will check manually, "
                "and escalate."
            )
        if reason == "variant_not_found":
            options = ", ".join(target.get("available_options") or []) or "none currently listed"
            return (
                f"**REQUESTED OPTION DOESN'T EXIST**: {target.get('product_title', item_name)} doesn't come in "
                f"what the customer asked for. Real available options: {options}. "
                "Tell the customer honestly that option doesn't exist and offer the real available options "
                "instead. Do NOT create an exchange action."
            )
        if reason == "out_of_stock":
            return (
                f"**REPLACEMENT OUT OF STOCK**: {target.get('product_title', item_name)} "
                f"({target.get('variant_title', 'the requested option')}) is currently out of stock. "
                "Tell the customer honestly it's out of stock right now, offer to notify them when it's back "
                "in or offer a refund instead. Do NOT create an exchange action or promise the exchange."
            )
        if reason == "target_not_found":
            return (
                "**PRODUCT NOT FOUND**: We don't carry anything matching what the customer described. "
                "Tell the customer honestly we couldn't find that product and ask them to clarify or browse "
                "the store. Do NOT create an exchange action."
            )
        if reason == "ambiguous":
            matches = ", ".join(target.get("matches") or [])
            return (
                f"**MULTIPLE PRODUCTS MATCH — DO NOT GUESS**: Found several products matching the request: "
                f"{matches}. Ask the customer which one they mean before doing anything else."
            )
        return (
            "**COULDN'T VERIFY THE REPLACEMENT**: Tell the customer we need to check with our team, and "
            "escalate. Do NOT create an exchange action or promise anything."
        )

    async def _maybe_autopilot_cancel(
        self,
        tenant_id: Optional[str],
        brand_id: Optional[str],
        staged: Optional[dict],
        order_id: str,
    ) -> Optional[str]:
        """Auto-execute a cancellation that has already cleared every
        deterministic eligibility check the caller enforces, but ONLY when
        this brand has explicitly turned Cancellation Autopilot on via the
        dedicated /automation/cancellation/enable endpoint. Returns None
        when Autopilot isn't enabled (caller falls through to the normal
        Copilot "queued for a human" message, unchanged) or when `staged`
        isn't a real new pending action in the `actions` table (e.g.
        actions_service.create_action failed and this fell back to the
        legacy pending_actions table, or a duplicate was returned instead
        of a fresh action) — never attempts to auto-execute on a path it
        can't also safely follow through on.

        Reuses actions_service.approve_action() — the exact same function a
        human clicking Approve calls — so idempotency, the atomic
        pending->approved claim (closing double-execution races from
        retries/duplicate webhooks/concurrent workers), live Shopify
        re-verification, the audit trail, and existing failure handling are
        all inherited unchanged. No second execution path is created here."""
        if not tenant_id or not brand_id or not staged or not staged.get("success"):
            return None
        if staged.get("status") not in ("pending", None):
            # "duplicate_skipped" or anything else — not a fresh action
            # this call originated, never auto-execute on it.
            return None
        action_id = staged.get("action_id")
        if not action_id:
            return None

        try:
            brands = supabase_select("brands", {"id": f"eq.{brand_id}"})
        except Exception as e:
            logger.warning(f"[Autopilot] Could not verify Autopilot flag for brand {brand_id} ({e}) — leaving action pending for human review")
            return None
        if not brands or not brands[0].get("cancellation_autopilot_enabled"):
            return None

        from src.services.actions_service import actions_service

        logger.info(f"[Autopilot] Attempting automatic cancellation for action {action_id} (order #{order_id})")
        outcome = await actions_service.approve_action(
            tenant_id=tenant_id,
            action_id=action_id,
            approved_by="autopilot",
            idempotency_key=f"autopilot-{action_id}",
        )

        if outcome.get("success"):
            logger.info(f"[Autopilot] Cancellation completed automatically for action {action_id}")
            return (
                "**CANCEL COMPLETED AUTOMATICALLY**: Cancellation Autopilot verified every safety check and "
                "Shopify has confirmed the order is cancelled. Tell the customer, briefly and naturally: "
                f"'Done! Your order #{order_id} has been cancelled successfully.'"
            )

        # Shopify itself rejected/failed the cancellation (or the action was
        # already actioned/claimed elsewhere) — the action record already
        # reflects the real failure (actions_service marks it "failed" with
        # the real error on a ShopifyError). Never claim success; this is a
        # genuine escalation, never "best effort."
        logger.warning(f"[Autopilot] Automatic cancellation failed for action {action_id}: {outcome.get('error')}")
        return (
            "**CANCEL AUTOPILOT FAILED — ESCALATED TO HUMAN REVIEW**: Automatic cancellation could not be "
            f"completed ({outcome.get('error') or 'Shopify did not confirm the cancellation'}). Do NOT tell the "
            "customer it succeeded, and do NOT promise a specific response time. Tell the customer: "
            "'I couldn't complete the cancellation automatically, so I've sent this to our team for review.'"
        )

    # A customer stating their own dollar figure ("refund me $30 for the
    # damaged item") is inherently ambiguous for AUTOMATIC execution - a
    # human approver still decides the real amount at approval time, and
    # this is never itself authorization to refund that amount (see
    # actions_service.approve_action's override_amount docstring: only a
    # human-typed figure at approval time ever reaches Shopify). So any
    # dollar mention in the message keeps disqualifying this from
    # _maybe_autopilot_refund below, unchanged.
    #
    # It IS captured (via _extract_requested_refund_amount below) and
    # stored on the staged action as extracted_data.requested_amount so the
    # approval UI can show and pre-fill what the customer actually asked
    # for, instead of always silently defaulting to a full refund the
    # human then has to notice and override by re-reading the raw message.
    # A human still has to click Approve either way - this only fixes what
    # the form defaults to.
    _AMOUNT_MENTION_RE = re.compile(r'\$\s*\d|\b\d+(?:\.\d+)?\s*(?:dollars|usd|bucks)\b', re.IGNORECASE)
    _REQUESTED_AMOUNT_RE = re.compile(
        r'\$\s*(\d+(?:\.\d{1,2})?)|(\d+(?:\.\d{1,2})?)\s*(?:dollars|usd|bucks)\b', re.IGNORECASE
    )

    @classmethod
    def _extract_requested_refund_amount(cls, query: str) -> Optional[float]:
        """Deterministic regex extraction only - never an LLM guess, never
        itself sufficient to execute anything (see the class comment
        above). Returns None when the customer named no figure, or named
        one that doesn't parse to a positive number - the caller's existing
        full-refund default is preserved in both cases."""
        if not query:
            return None
        m = cls._REQUESTED_AMOUNT_RE.search(query)
        if not m:
            return None
        try:
            value = float(m.group(1) or m.group(2))
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    async def _maybe_autopilot_refund(
        self,
        tenant_id: Optional[str],
        brand_id: Optional[str],
        staged: Optional[dict],
        order_id: str,
        query: str,
        is_unfulfilled: bool,
        specific_item: Optional[dict],
    ) -> Optional[str]:
        """Auto-execute a refund, but ONLY the single deterministic case
        this system can verify without any model or customer input: a full,
        whole-order refund for a Shopify-computed amount. Refunds are
        financially sensitive, so this is deliberately far more
        conservative than cancellation's equivalent hook — every one of
        the conditions below must hold, and any doubt falls through to the
        existing "staged for human review" Copilot path unchanged.

        Never auto-executes:
        - a cancel_order staging (is_unfulfilled) — that's Cancellation
          Autopilot's own, entirely separate hook.
        - a specific single-item partial match — this integration has no
          deterministic partial-refund amount for a single item; only a
          human approver decides that figure today.
        - any message that mentions a dollar figure — the customer asked
          for something this system cannot verify was actually approved
          (see _AMOUNT_MENTION_RE above).

        When all of those pass, reuses actions_service.approve_action()
        with NO override_amount, so it falls through to
        extracted_data.get("amount") (never set by this integration for a
        refund action) and then to process_refund()'s own live,
        Shopify-verified amount = refundable_amount (order total minus
        already-refunded, freshly computed from a live order fetch) — the
        backend calculates/validates the amount, never Luna, never a
        guess."""
        if is_unfulfilled or specific_item is not None:
            return None
        if self._AMOUNT_MENTION_RE.search(query or ""):
            return None
        if not tenant_id or not brand_id or not staged or not staged.get("success"):
            return None
        if staged.get("status") not in ("pending", None):
            return None
        action_id = staged.get("action_id")
        if not action_id:
            return None

        try:
            brands = supabase_select("brands", {"id": f"eq.{brand_id}"})
        except Exception as e:
            logger.warning(f"[Autopilot] Could not verify Refund Autopilot flag for brand {brand_id} ({e}) — leaving action pending for human review")
            return None
        if not brands or not brands[0].get("refund_autopilot_enabled"):
            return None

        from src.services.actions_service import actions_service

        logger.info(f"[Autopilot] Attempting automatic refund for action {action_id} (order #{order_id})")
        outcome = await actions_service.approve_action(
            tenant_id=tenant_id,
            action_id=action_id,
            approved_by="autopilot",
            idempotency_key=f"autopilot-{action_id}",
        )

        if outcome.get("success"):
            logger.info(f"[Autopilot] Refund completed automatically for action {action_id}")
            amount = (outcome.get("execution_result") or {}).get("amount")
            amount_str = f" of ${amount:.2f}" if isinstance(amount, (int, float)) else ""
            return (
                "**REFUND COMPLETED AUTOMATICALLY**: Refund Autopilot verified every safety check and "
                "Shopify has confirmed the refund. Tell the customer, briefly and naturally: "
                f"'Done! Your refund{amount_str} has been processed.'"
            )

        # Shopify itself rejected/failed the refund (already fully
        # refunded, no valid payment transaction, etc.) or the action was
        # already actioned/claimed elsewhere — the action record already
        # reflects the real failure. Never claim success.
        logger.warning(f"[Autopilot] Automatic refund failed for action {action_id}: {outcome.get('error')}")
        return (
            "**REFUND AUTOPILOT FAILED — ESCALATED TO HUMAN REVIEW**: Automatic refund could not be "
            f"completed ({outcome.get('error') or 'Shopify did not confirm the refund'}). Do NOT tell the "
            "customer it succeeded, and do NOT promise a specific response time. Tell the customer: "
            "'I couldn't complete that refund automatically, so I've sent it to our team for review.'"
        )

    async def _create_action(
        self,
        tenant_id: Optional[str],
        brand_id: Optional[str],
        ticket_id: Optional[str],
        action_type: str,
        order_id: str,
        email: str,
        customer_name: Optional[str],
        query: str,
        ai_reasoning: str,
        eligibility: dict,
        exchange_suggestion: dict = None,
        new_address_text: Optional[str] = None,
        structured_address: Optional[dict] = None,
        exchange_target: Optional[dict] = None,
        original_item: Optional[dict] = None,
        price_difference: Optional[float] = None,
        policy_evidence: Optional[str] = None,
        reship_order_snapshot: Optional[dict] = None,
        current_shipping_address: Optional[dict] = None,
        current_fulfillment_status: Optional[str] = None,
        identity_verified: Optional[bool] = None,
        identity_verification_reason: Optional[str] = None,
        requested_amount: Optional[float] = None,
        customer_intent: Optional[str] = None,
    ) -> dict:
        """Create action in `actions` table (new system) when tenant_id is available,
        otherwise fall back to legacy `pending_actions` via stage_pending_action.

        customer_intent: the RAW intent_type the customer's own message
        actually classified as ("return", "refund", "cancel", "exchange", ...)
        - never the same thing as `action_type`/`mapped_type` above, which is
        an EXECUTION choice: this REST-only integration has no separate
        Shopify "return" mutation, so a "return" (and, when the order can't
        be confirmed unfulfilled, a "cancel") intent is staged with
        action_type="refund" because that's the only mutation that can
        actually fulfill it once a human approves. Collapsing "return" into
        "refund" at the execution layer is intentional and unavoidable
        given that constraint - but until now nothing preserved the
        customer's ORIGINAL ask anywhere queryable, so a merchant reviewing
        a return request saw a "Refund" card with no way to tell it apart
        from a genuine refund ask (confirmed live: order #1006, "I'd like
        to return order #1006" produced a card titled "Refund", not
        "Return"). Stored here, read by the dashboard (ActionCard.jsx /
        Actions.jsx) for the card's type label and by
        _duplicate_status_context for correctly-worded repeat-request
        replies - never read by approve_action(), which only ever executes
        whatever the stored action_type actually says."""
        mapped_type = _ACTION_TYPE_MAP.get(action_type, "refund")

        if tenant_id:
            try:
                from src.services.actions_service import actions_service
                items = eligibility.get("items", [])
                extracted: dict = {
                    "order_id": order_id,
                    "items": items,
                    "order_total": eligibility.get("order_total"),
                    "eligibility": eligibility,
                    "exchange_suggestion": exchange_suggestion,
                }
                if new_address_text:
                    extracted["new_address_text"] = new_address_text
                if structured_address:
                    extracted["new_address"] = structured_address
                # actions_service.approve_action()'s EXCHANGE branch reads
                # exactly these three keys (target/original_item/price_difference)
                # from extracted_data to run create_exchange_draft_order() —
                # nothing else derives an exchange's execution inputs.
                if exchange_target is not None:
                    extracted["target"] = exchange_target
                if original_item is not None:
                    extracted["original_item"] = original_item
                if price_difference is not None:
                    extracted["price_difference"] = price_difference
                if policy_evidence:
                    extracted["policy_evidence"] = policy_evidence
                if reship_order_snapshot:
                    extracted["order_snapshot"] = reship_order_snapshot
                if current_shipping_address is not None:
                    extracted["current_shipping_address"] = current_shipping_address
                if current_fulfillment_status is not None:
                    extracted["current_fulfillment_status"] = current_fulfillment_status
                if identity_verified is not None:
                    extracted["identity_verified"] = identity_verified
                    extracted["identity_verification_reason"] = identity_verification_reason
                if requested_amount is not None:
                    # Displayed/pre-filled by the approval UI only - never
                    # read by approve_action() itself, which only ever
                    # trusts a human-typed override_amount at approval
                    # time (see that function's own docstring). The human
                    # still submits (or edits) this figure explicitly.
                    extracted["requested_amount"] = requested_amount
                if customer_intent is not None:
                    extracted["customer_intent"] = customer_intent
                return await actions_service.create_action(
                    tenant_id=tenant_id,
                    brand_id=brand_id,
                    action_type=mapped_type,
                    customer_email=email,
                    customer_name=customer_name,
                    order_id=str(order_id),
                    message=query[:1000],
                    extracted_data=extracted,
                    confidence=0.85,
                    ai_reasoning=ai_reasoning,
                    ticket_id=ticket_id,
                )
            except Exception as e:
                logger.warning(f"[ReturnActions] actions_service.create_action failed ({e}), falling back to legacy")

        return await stage_pending_action(
            order_id=order_id,
            customer_email=email,
            action_type=action_type,
            ai_reasoning=ai_reasoning,
            eligibility_data=eligibility,
            exchange_suggestion=exchange_suggestion,
            customer_name=customer_name,
        )

    def _extract_order_info(
        self,
        query: str,
        customer_info: Dict[str, Any],
        existing_tool_results: Dict[str, Any],
        intent_result: Optional[IntentResult] = None,
        ticket_id: Optional[str] = None,
    ):
        """Extract order ID and email. Uses LLM-extracted order_id first, then regex fallback.

        Multi-turn continuity: a follow-up like "yes, cancel it" names no
        order number of its own. When every other source above comes up
        empty, falls back to this ticket's own detected_order_id - the
        same trusted, already-preserved-across-turns field
        customer_success_agent.py's identity-verification follow-up
        already relies on (see message_processor.py STAGE 9's
        preserve-by-omission fix). Only used as a last resort, so an
        unrelated fresh request that genuinely names its own order number
        is never overridden by stale conversation state."""
        import re

        # Email from customer_info first
        email = customer_info.get("email")
        if not email:
            m = re.search(r'[\w.-]+@[\w.-]+\.\w+', query)
            if m:
                email = m.group(0)

        # Order ID: LLM result > regex from query > existing tool results
        order_id = intent_result.order_id if intent_result else None

        if not order_id:
            m = re.search(r'order\s*#?(\d+)', query, re.IGNORECASE)
            if m:
                order_id = m.group(1)

        if not order_id:
            m = re.search(r'#(\d{4,})', query)
            if m:
                order_id = m.group(1)

        if not order_id:
            m = re.search(r'\b(\d{4,6})\b', query)
            if m:
                order_id = m.group(1)

        if not order_id and existing_tool_results.get("orders_by_email"):
            orders = existing_tool_results["orders_by_email"].get("orders", [])
            if orders:
                order_id = orders[0].get("order_number")

        if not order_id and ticket_id:
            try:
                rows = supabase_select("tickets", {"id": f"eq.{ticket_id}"})
                if rows and rows[0].get("detected_order_id"):
                    order_id = rows[0]["detected_order_id"]
            except Exception as e:
                logger.warning(f"[ReturnActions] Ticket order-context lookup failed (non-blocking): {e}")

        if order_id and not email and existing_tool_results.get("orders_by_email"):
            email = existing_tool_results["orders_by_email"].get("email")

        return order_id, email

    def _validate_address(self, parsed: dict) -> tuple:
        """Return (is_valid, missing_fields). Requires address1, city, and country."""
        missing = []
        if not parsed.get("address1", "").strip():
            missing.append("street address (house/flat number and street name)")
        if not parsed.get("city", "").strip():
            missing.append("city name")
        if not parsed.get("country", "").strip():
            missing.append("country")
        return len(missing) == 0, missing

    def _build_action_context(self, eligibility: Dict[str, Any], exchange: Dict[str, Any]) -> str:
        if not eligibility:
            return ""
        eligible = eligibility.get("eligible", False)
        reason = eligibility.get("reason", "")
        items = eligibility.get("items", [])
        if not eligible:
            return f"**RETURN NOT ELIGIBLE**: {reason}. Do NOT process return. Do NOT offer refund. Acknowledge the policy and offer to escalate to human support if customer is unhappy."
        context_parts = [f"**RETURN ELIGIBLE**: {reason}"]
        if items:
            item_names = [f"{i.get('title')} ({i.get('variant_title')})" for i in items]
            context_parts.append(f"Items in order: {', '.join(item_names)}")
        if exchange and exchange.get("has_exchange"):
            suggestions = exchange.get("suggestions", [])
            for s in suggestions:
                direction = "larger" if s["direction"] > 0 else "smaller"
                context_parts.append(
                    f"EXCHANGE AVAILABLE: We have {s['original_item']} in {s['suggested_size']} "
                    f"(one size {direction}). Use this to upsell!"
                )
            context_parts.append(f"Sales pitch: {exchange.get('pitch', '')}")
        return "\n".join(context_parts)


# Singleton instance
return_actions = ReturnActionsIntegration()
