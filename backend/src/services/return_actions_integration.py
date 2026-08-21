"""
Return Actions Integration Helper
=================================
Routes customer action requests (refund, cancel, address change, reship)
to the human-in-the-loop approval queue.
Uses AI intent detection — no static keyword lists.
"""
import asyncio
import logging
from typing import Dict, Any, List, Optional, Tuple, TYPE_CHECKING, Callable, Awaitable

from src.lib.supabase_client import supabase_select
from src.services.intent_detector import intent_detector, IntentResult

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

        order_id, email = self._extract_order_info(query, customer_info, existing_tool_results, intent_result)
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
                    "They'll do everything they can and get back to you shortly.'"
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
                    missing_str = ", ".join(missing)
                    result["action_context"] = (
                        f"ADDRESS INCOMPLETE — DO NOT CONFIRM. "
                        f"The customer's address is missing: {missing_str}. "
                        f"Tell the customer politely: 'I'd be happy to update your delivery address! "
                        f"Could you please reply with your full name, complete street address "
                        f"(house/flat number and street name), city, and country? "
                        f"I'll get it updated right away once I have those details.' "
                        f"Do NOT say you've queued anything."
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

            ai_reasoning = (
                f"Customer requests address change for order #{order_id}. "
                f"Requested address: {new_address_text} [Auto-parsed ✓]"
            )
            staged = await self._create_action(
                tenant_id=tenant_id, brand_id=brand_id, ticket_id=ticket_id,
                action_type="change_address", order_id=order_id, email=email or "",
                customer_name=customer_info.get("name"), query=query,
                ai_reasoning=ai_reasoning, eligibility={},
                new_address_text=new_address_text,
                structured_address=structured_address,
            )
            result["staged"] = staged
            result["action_context"] = (
                "**ADDRESS CHANGE QUEUED (auto-parsed)**: Structured address stored — will update in Shopify automatically on approval. "
                "Tell the customer: 'I've queued your address update. It will be updated right away and you'll get a confirmation email.'"
            )
            return result

        # ── RESHIP / LOST PACKAGE ───────────────────────────────────────────
        if intent_type == "reship":
            if not order_id:
                result["action_context"] = (
                    "ACTION REQUIRED: Ask customer for their order number so the team can investigate the delivery."
                )
                return result

            ai_reasoning = f"Customer reports delivery issue for order #{order_id} — package not received."
            staged = await self._create_action(
                tenant_id=tenant_id, brand_id=brand_id, ticket_id=ticket_id,
                action_type="reship", order_id=order_id, email=email or "",
                customer_name=customer_info.get("name"), query=query,
                ai_reasoning=ai_reasoning, eligibility={},
            )
            result["staged"] = staged
            result["action_context"] = (
                "**DELIVERY ISSUE QUEUED**: Team will check with the carrier and arrange reship or refund. "
                "Tell the customer: 'I've flagged this with our team — they'll investigate with the carrier "
                "and sort this out for you within 24 hours.'"
            )
            return result

        # ── EXCHANGE — different size/color/product, live Shopify grounded ──
        if intent_type == "exchange":
            return await self._handle_exchange(
                query, customer_info, order_id, email, intent_result,
                tenant_id, brand_id, ticket_id, result,
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
        if existing_action:
            result["action_context"] = self._duplicate_status_context(existing_action, intent_type)
            return result

        await _emit("order_lookup", "Finding your order…")
        eligibility = await self.actions.check_return_eligibility(
            order_id, email, tenant_id=tenant_id, brand_id=brand_id
        )
        result["eligibility"] = eligibility
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

        # UNFULFILLED → cancel is right (not refund). Only when we actually
        # HAVE order data confirming this — an unverified/not-found order
        # (order_data empty) must fall through to manual review below, never
        # be assumed unfulfilled-therefore-cancel.
        if order_data and is_unfulfilled and not eligibility.get("eligible"):
            ai_reasoning = (
                f"Customer requests {intent_type} for order #{order_id}. "
                f"Order is unfulfilled — cancel + auto-refund is appropriate."
            )
            await _emit("staging_action", f"Preparing your {_noun} request…")
            staged = await self._create_action(
                tenant_id=tenant_id, brand_id=brand_id, ticket_id=ticket_id,
                action_type="cancel_order", order_id=order_id, email=email,
                customer_name=customer_info.get("name"), query=query,
                ai_reasoning=ai_reasoning, eligibility=eligibility,
            )
            result["staged"] = staged
            result["action_context"] = (
                "**CANCEL QUEUED**: Order hasn't shipped yet — cancel + refund is the right action. "
                "Tell the customer: 'Since your order hasn't shipped yet, I've sent your cancellation request "
                "to our team. They'll cancel it and your refund will appear within 3–5 business days.'"
            )
            return result

        # NOT ELIGIBLE and fulfilled
        if not eligibility.get("eligible"):
            if eligibility.get("staging_required") or eligibility.get("requires_manual_review"):
                ai_reasoning = f"Customer requests {intent_type} for order #{order_id}. Manual review required: {eligibility.get('reason')}"
                await _emit("staging_action", f"Preparing your {_noun} request…")
                staged = await self._create_action(
                    tenant_id=tenant_id, brand_id=brand_id, ticket_id=ticket_id,
                    action_type="refund", order_id=order_id, email=email,
                    customer_name=customer_info.get("name"), query=query,
                    ai_reasoning=ai_reasoning, eligibility=eligibility,
                )
                result["staged"] = staged
                result["action_context"] = (
                    f"**REQUEST SUBMITTED FOR MANUAL REVIEW**: {eligibility.get('reason')} "
                    "Tell the customer: 'I've submitted your request to our team for manual review. "
                    "They'll process it within 2 hours and you'll get an email confirmation.'"
                )
            else:
                result["action_context"] = (
                    f"**{intent_type.upper()} NOT ELIGIBLE**: {eligibility.get('reason')}. "
                    f"Do NOT process the {intent_type}. Acknowledge and offer to escalate to human support if frustrated."
                )
            return result

        # ELIGIBLE → stage the refund (a "return" IS a refund here — see
        # this block's header comment for why there's no separate action type)
        items = eligibility.get("items", [])
        item_names = ", ".join([i.get("title", "item") for i in items[:2]])

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
        if specific_item:
            ai_reasoning = (
                f"Customer requests {intent_type} for order #{order_id}, SPECIFICALLY ONLY: "
                f"{specific_item.get('title')} ({specific_item.get('variant_title') or 'one size'}), "
                f"not the full order ({item_names})."
            )
        else:
            ai_reasoning = f"Customer requests {intent_type} for order #{order_id}: {item_names}"

        await _emit("staging_action", f"Preparing your {_noun} request…")
        staged = await self._create_action(
            tenant_id=tenant_id, brand_id=brand_id, ticket_id=ticket_id,
            action_type="Refund", order_id=order_id, email=email,
            customer_name=customer_info.get("name"), query=query,
            ai_reasoning=ai_reasoning, eligibility=eligibility,
        )
        result["staged"] = staged

        if staged.get("success"):
            noun = "return" if intent_type == "return" else "refund" if intent_type == "refund" else "cancellation"
            if specific_item:
                result["action_context"] = (
                    f"**ACTION STAGED FOR APPROVAL (PARTIAL — only {specific_item.get('title')})**: "
                    f"The customer wants to {noun} only {specific_item.get('title')}, not the rest of the order. "
                    "Our team will confirm the exact refund amount for that item specifically. "
                    f"Tell the customer: 'I've sent a request to my team to {noun} just the "
                    f"{specific_item.get('title')}, the rest of your order is unaffected. "
                    "You'll get a confirmation as soon as they approve it (usually under 2 hours).'"
                )
            else:
                result["action_context"] = (
                    f"**ACTION STAGED FOR APPROVAL**: Your {noun} request has been submitted for review. "
                    "Tell the customer: 'I've prepared your request for my team to review. "
                    "You'll get a confirmation as soon as they approve it (usually under 2 hours).'"
                )
        else:
            result["action_context"] = (
                f"{intent_type.capitalize()} eligible but staging failed: {staged.get('message') or staged.get('error')}. "
                "Process normally but flag for manual review."
            )

        return result

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
                "status": "in.(pending,approved,executed)",
                "order": "created_at.desc",
                "limit": "1",
            })
            return existing[0] if existing else None
        except Exception as e:
            logger.warning(f"[ReturnActions] Dedup check failed for order {order_id} ({e}) — continuing without it")
            return None

    def _duplicate_status_context(self, existing: Dict[str, Any], intent_type: str) -> str:
        """Truthful status wording for a repeat request against an action
        that's already pending/approved/executed — never re-stages, and
        never claims completion that hasn't actually happened. Reused by
        both the refund/return/cancel path and the exchange path."""
        noun = "exchange" if existing.get("action_type") == "exchange" else (
            "return" if intent_type == "return" else "refund" if intent_type == "refund" else "cancellation"
        )
        status = existing.get("status")
        if status == "pending":
            return (
                f"**{noun.upper()} ALREADY PENDING**: A {noun} request for this order is already awaiting "
                f"our team's approval (submitted earlier in this conversation or a prior message). "
                f"Do NOT create a new request or say a new one was sent. "
                f"Tell the customer: 'Your {noun} request is already with our team for approval, you'll hear "
                f"back soon, no need to send it again.'"
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
    ) -> Dict[str, Any]:
        """Full exchange workflow: order + customer verification, the exact
        same eligibility/policy check returns and refunds use, item
        identification (asks rather than guesses on a multi-item order),
        LIVE Shopify target-variant resolution (never stale RAG/product
        memory), availability + price-difference checks, then either stages
        a real "exchange" action for approval or explains honestly why it
        can't — never a fabricated "your exchange is being processed"."""
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
            return result

        eligibility = await self.actions.check_return_eligibility(
            order_id, email, tenant_id=tenant_id, brand_id=brand_id
        )
        result["eligibility"] = eligibility

        if not eligibility.get("eligible"):
            if eligibility.get("staging_required") or eligibility.get("requires_manual_review"):
                ai_reasoning = f"Customer requests exchange for order #{order_id}. Manual review required: {eligibility.get('reason')}"
                staged = await self._create_action(
                    tenant_id=tenant_id, brand_id=brand_id, ticket_id=ticket_id,
                    # We can't safely verify eligibility automatically (order
                    # mismatch / lookup failure) — stage as a plain refund-
                    # family review action, exactly like the shared refund/
                    # return path does for the same "requires_manual_review"
                    # case, since there's no live target data to attach yet.
                    action_type="refund", order_id=order_id, email=email,
                    customer_name=customer_info.get("name"), query=query,
                    ai_reasoning=ai_reasoning, eligibility=eligibility,
                )
                result["staged"] = staged
                result["action_context"] = (
                    f"**EXCHANGE REQUEST SUBMITTED FOR MANUAL REVIEW**: {eligibility.get('reason')} "
                    "Tell the customer: 'I've submitted your exchange request to our team for manual review. "
                    "They'll process it within 2 hours and you'll get an email confirmation.'"
                )
            else:
                result["action_context"] = (
                    f"**EXCHANGE NOT ELIGIBLE**: {eligibility.get('reason')}. "
                    "Do NOT process the exchange. Acknowledge and offer to escalate to human support if frustrated."
                )
            return result

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

        target = await self.actions.find_exchange_target(tenant_id, raw_item, target_description)
        result["exchange"] = target

        if not target.get("found"):
            result["action_context"] = self._exchange_target_failure_context(target, original_item)
            return result

        original_price = float(original_item.get("price") or 0)
        price_difference = round(target["price"] - original_price, 2)

        if price_difference < 0:
            # Cheaper replacement — refunding/crediting the difference is a
            # real business decision with no configured store policy
            # anywhere in this system. Never decided here — staged for a
            # human, with the live numbers attached so they don't have to
            # look them up again.
            ai_reasoning = (
                f"Customer requests exchange for order #{order_id}: {original_item.get('title')} -> "
                f"{target.get('product_title')} ({target.get('variant_title')}). "
                f"Replacement is ${abs(price_difference):.2f} cheaper — needs merchant decision on the difference."
            )
            staged = await self._create_action(
                tenant_id=tenant_id, brand_id=brand_id, ticket_id=ticket_id,
                action_type="exchange", order_id=order_id, email=email,
                customer_name=customer_info.get("name"), query=query,
                ai_reasoning=ai_reasoning, eligibility=eligibility,
                exchange_target=target, original_item=raw_item, price_difference=price_difference,
            )
            result["staged"] = staged
            result["action_context"] = (
                f"**EXCHANGE SUBMITTED FOR MANUAL REVIEW (price difference)**: The replacement "
                f"({target.get('product_title')}, {target.get('variant_title')}) is ${abs(price_difference):.2f} "
                "cheaper than the original item, our team needs to decide how to handle the difference. "
                "Tell the customer: 'I found your replacement item and sent this to our team for approval "
                "since there is a price difference to sort out. You will hear back within 2 hours.' "
                "Do NOT say the exchange is done or promise a refund of the difference."
            )
            return result

        ai_reasoning = (
            f"Customer requests exchange for order #{order_id}: "
            f"{original_item.get('title')} ({original_item.get('variant_title')}) -> "
            f"{target.get('product_title')} ({target.get('variant_title')}). "
            f"Price difference: ${price_difference:.2f}."
        )
        staged = await self._create_action(
            tenant_id=tenant_id, brand_id=brand_id, ticket_id=ticket_id,
            action_type="exchange", order_id=order_id, email=email,
            customer_name=customer_info.get("name"), query=query,
            ai_reasoning=ai_reasoning, eligibility=eligibility,
            exchange_target=target, original_item=raw_item, price_difference=price_difference,
        )
        result["staged"] = staged

        if staged.get("success"):
            variant_label = f"{target.get('product_title')} ({target.get('variant_title')})"
            if price_difference == 0:
                result["action_context"] = (
                    f"**EXCHANGE STAGED FOR APPROVAL (no price difference)**: {variant_label} is in stock and "
                    "confirmed available. Tell the customer: 'Good news, that is in stock! I have sent this "
                    "exchange to our team for approval, once confirmed you will not need to pay anything extra "
                    "and we will get your replacement on its way.' Do NOT say the exchange is already done."
                )
            else:
                result["action_context"] = (
                    f"**EXCHANGE STAGED FOR APPROVAL (${price_difference:.2f} difference)**: {variant_label} is "
                    f"available. Tell the customer: 'I found that in stock! There is a ${price_difference:.2f} "
                    "price difference for the new item. I have sent this to our team for approval, once "
                    "confirmed you will get a checkout link to pay the difference and complete the exchange.' "
                    "Do NOT say the exchange is already done."
                )
        else:
            result["action_context"] = (
                f"Exchange target found but staging failed: {staged.get('message') or staged.get('error')}. "
                "Process normally but flag for manual review."
            )

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
    ) -> dict:
        """Create action in `actions` table (new system) when tenant_id is available,
        otherwise fall back to legacy `pending_actions` via stage_pending_action."""
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
    ):
        """Extract order ID and email. Uses LLM-extracted order_id first, then regex fallback."""
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
