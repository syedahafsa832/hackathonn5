"""
Return Actions Integration Helper
=================================
Routes customer action requests (refund, cancel, address change, reship)
to the human-in-the-loop approval queue.
Uses AI intent detection — no static keyword lists.
"""
import asyncio
import logging
from typing import Dict, Any, Optional, TYPE_CHECKING

from src.services.intent_detector import intent_detector, IntentResult

from .actions_manager import actions_manager, stage_pending_action

logger = logging.getLogger(__name__)

_ACTION_TYPE_MAP = {
    "Refund": "refund",
    "Exchange": "refund",
    "Cancel": "cancel_order",
    "cancel_order": "cancel_order",
    "cancel": "cancel_order",
    "refund": "refund",
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
    ) -> Dict[str, Any]:
        result = {
            "return_checked": False,
            "eligibility": None,
            "exchange": None,
            "action_context": "",
        }

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

        # ── REFUND / CANCEL — needs eligibility check ───────────────────────
        result["return_checked"] = True

        if not order_id or not email:
            result["action_context"] = (
                "ACTION REQUIRED: Ask customer for their order number and email to verify eligibility. "
                "Do NOT assume or guess order details."
            )
            return result

        eligibility = await self.actions.check_return_eligibility(
            order_id, email, tenant_id=tenant_id, brand_id=brand_id
        )
        result["eligibility"] = eligibility

        order_data = eligibility.get("order", {}) or {}
        fulfillment_status = order_data.get("fulfillment_status")
        is_unfulfilled = fulfillment_status != "fulfilled"

        # UNFULFILLED → cancel is right (not refund)
        if is_unfulfilled and not eligibility.get("eligible"):
            ai_reasoning = (
                f"Customer requests {intent_type} for order #{order_id}. "
                f"Order is unfulfilled — cancel + auto-refund is appropriate."
            )
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
                ai_reasoning = f"Customer requests refund for order #{order_id}. Manual review required: {eligibility.get('reason')}"
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
                    f"**RETURN NOT ELIGIBLE**: {eligibility.get('reason')}. "
                    "Do NOT process return. Acknowledge and offer to escalate to human support if frustrated."
                )
            return result

        # ELIGIBLE → stage refund or exchange
        size_issue = self._is_size_issue(query)
        action_type = "Exchange" if size_issue else "Refund"

        exchange_suggestion = None
        if size_issue:
            preferred_size = self._extract_size_preference(query)
            exchange_result = await self.actions.suggest_exchange(eligibility, preferred_size)
            if exchange_result.get("has_exchange"):
                exchange_suggestion = (
                    exchange_result.get("suggestions", [{}])[0]
                    if exchange_result.get("suggestions") else None
                )
            result["exchange"] = exchange_result

        items = eligibility.get("items", [])
        item_names = ", ".join([i.get("title", "item") for i in items[:2]])
        ai_reasoning = f"Customer requests {action_type.lower()} for order #{order_id}: {item_names}"

        staged = await self._create_action(
            tenant_id=tenant_id, brand_id=brand_id, ticket_id=ticket_id,
            action_type=action_type, order_id=order_id, email=email,
            customer_name=customer_info.get("name"), query=query,
            ai_reasoning=ai_reasoning, eligibility=eligibility,
            exchange_suggestion=exchange_suggestion,
        )
        result["staged"] = staged

        if staged.get("success"):
            result["action_context"] = (
                f"**ACTION STAGED FOR APPROVAL**: Your {action_type.lower()} request has been submitted for review. "
                "Tell the customer: 'I've prepared your request for my team to review. "
                "You'll get a confirmation as soon as they approve it (usually under 2 hours).'"
            )
        else:
            result["action_context"] = (
                f"Return eligible but staging failed: {staged.get('message') or staged.get('error')}. "
                "Process normally but flag for manual review."
            )

        return result

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

    def _is_size_issue(self, query: str) -> bool:
        size_keywords = [
            'wrong size', "doesn't fit", 'doesnt fit',
            'too small', 'too big', 'too large',
            'different size', 'other size',
            'size', 'small', 'medium', 'large', 'xl'
        ]
        q = query.lower()
        return any(kw in q for kw in size_keywords)

    def _extract_size_preference(self, query: str) -> Optional[str]:
        import re
        size_patterns = [
            r'(extra\s*small|xs)',
            r'(small|s(?!mall))',
            r'(medium|m(?!edium))',
            r'(large|l(?!arge))',
            r'(extra\s*large|xl)',
            r'(xxl|double\s*xl)',
            r'(size\s*(small|medium|large|xs|xl))'
        ]
        for pattern in size_patterns:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                size_text = match.group(1).lower()
                if 'xs' in size_text or 'extra small' in size_text:
                    return 'XS'
                elif 'small' in size_text and 'extra' not in size_text:
                    return 'S'
                elif 'medium' in size_text or size_text == 'm':
                    return 'M'
                elif 'large' in size_text and 'extra' not in size_text:
                    return 'L'
                elif 'xl' in size_text or 'extra large' in size_text:
                    return 'XL'
                elif 'xxl' in size_text:
                    return 'XXL'
        return None

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
