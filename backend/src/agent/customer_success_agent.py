import os
import json
import re
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List, Callable, Awaitable

# Set OPENAI_API_KEY for compatibility with Mistral's OpenAI-compatible API
if not os.getenv("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = os.getenv("MISTRAL_API_KEY", "")

from openai import OpenAI

from ..services.brand_knowledge_service import brand_knowledge_service
from ..services.sentiment_analyzer import sentiment_analyzer
from ..services.size_engine import size_engine
from ..services.tools import v3_tools
from ..services.return_actions_integration import return_actions
from ..lib.supabase_client import supabase_rpc, supabase_update
from ..services.mistral_limiter import call_with_limit
from ..services.ai_provider_manager import ai_provider_manager, AllProvidersFailedError

logger = logging.getLogger(__name__)


# Placeholder/fallback values that mean "we don't actually know this
# customer's name" - never a real name to greet someone by. Single source
# used by _construct_v3_prompt (the one shared prompt builder for every
# channel/response type) so a placeholder can never be handed to the model
# as if it were the customer's real name (root cause of "Dear There").
_UNKNOWN_NAME_PLACEHOLDERS = {"there", "customer", "website visitor", "unknown", "guest", "friend"}


def _known_customer_name(raw_name: Optional[str]) -> Optional[str]:
    """Returns a real customer name, or None if it's missing/a placeholder.
    Never derives a name from an email address or order number - callers
    only ever pass what was actually verified/provided."""
    if not raw_name:
        return None
    name = str(raw_name).strip()
    if not name or name.lower() in _UNKNOWN_NAME_PLACEHOLDERS:
        return None
    return name


def _format_address(addr: dict) -> str:
    if not addr:
        return "No shipping address"
    parts = [addr.get("name", ""), addr.get("address1", ""), addr.get("city", ""),
             addr.get("province", ""), addr.get("country", "")]
    return ", ".join(p for p in parts if p)


def _build_order_context(order: dict, tracking_context: str = "") -> str:
    """Build an explicit order context block that the LLM cannot ignore."""
    if not order or not order.get("success"):
        return ""

    items = []
    for item in order.get("items", []):
        title = item.get("title", "Unknown item")
        variant = item.get("variant_title", "")
        qty = item.get("quantity", 1)
        price = item.get("price", "")
        item_str = f"{qty}x {title}"
        if variant and variant.lower() not in ("default title", ""):
            item_str += f" ({variant})"
        if price:
            item_str += f" — Rs {price}"
        items.append(item_str)

    order_num = order.get("order_number") or order.get("order_id", "Unknown")
    status = order.get("status", "unfulfilled")
    total = order.get("total_amount", "")
    tracking = order.get("tracking_number", "")
    tracking_url = order.get("tracking_url", "")
    tracking_company = order.get("tracking_company", "")
    shipment_status = order.get("shipment_status")
    shipped_at = order.get("shipped_at")

    financial_status = order.get("financial_status", "")
    cancelled_at = order.get("cancelled_at")

    lines = [
        "=== REAL ORDER DATA FROM SHOPIFY — USE THIS EXACT INFORMATION ===",
        f"Order Number: #{order_num}",
        f"Fulfillment Status: {status}",
        f"Payment Status: {financial_status or 'unknown'}",
    ]
    if cancelled_at:
        lines.append(f"CANCELLED: Yes (cancelled at {cancelled_at})")
    if total:
        lines.append(f"Total: Rs {total}")
    if items:
        lines.append("Items Ordered:")
        for item_line in items:
            lines.append(f"  - {item_line}")

    status_phrases = {
        "in_transit": "in transit",
        "out_for_delivery": "out for delivery today",
        "delivered": "delivered",
        "attempted_delivery": "delivery was attempted but failed",
        "failure": "experiencing a delivery issue",
    }

    def _shipped_day(iso_val):
        if not iso_val:
            return None
        try:
            return datetime.fromisoformat(iso_val.replace("Z", "+00:00")).strftime("%A")
        except Exception:
            return None

    shipments = order.get("fulfillments") or []

    if len(shipments) > 1:
        # Multiple REAL Shopify fulfillments (split shipment, backorder catch-up,
        # multi-warehouse, etc). Each is reported separately — never merged into
        # one fake shipment, and never collapsed to "just the first one" like the
        # single-shipment branch below does for the common case.
        lines.append("")
        lines.append(f"SHIPPING INFO — THIS ORDER HAS {len(shipments)} SEPARATE SHIPMENTS:")
        for i, s in enumerate(shipments, start=1):
            s_tracking = s.get("tracking_number")
            s_company = s.get("tracking_company")
            s_url = s.get("tracking_url")
            s_status = s.get("shipment_status")
            s_day = _shipped_day(s.get("shipped_at"))
            readable = status_phrases.get(s_status, "recently shipped, tracking should update within 24 hours")
            lines.append(f"  Shipment {i}:")
            if s_company:
                lines.append(f"    Carrier: {s_company}")
            if s_tracking:
                lines.append(f"    Tracking Number: {s_tracking}")
            else:
                lines.append("    Tracking Number: not yet available from the carrier")
            if s_url:
                lines.append(f"    Tracking URL: {s_url}")
            if s_day:
                lines.append(f"    Shipped: {s_day}")
            lines.append(f"    Current status: {readable}")
        lines.append("")
        lines.append("IF CUSTOMER ASKS WHERE THEIR ORDER IS:")
        lines.append(f"  This order shipped in {len(shipments)} separate packages — mention EACH shipment distinctly (e.g. 'shipment 1 of 2 is...').")
        lines.append("  Do NOT combine them into a single tracking number or status. Do NOT invent tracking for a shipment that doesn't have one yet.")
        lines.append("  If you cannot clearly explain all shipments, say a team member will confirm the full shipping breakdown rather than guessing.")
    elif tracking_context:
        # Live Aftership data or fallback instructions — injected by caller
        lines.append(tracking_context)
    elif tracking or shipment_status:
        readable_status = status_phrases.get(shipment_status, "recently shipped, tracking should update within 24 hours")

        lines.append("")
        lines.append("SHIPPING INFO:")
        if tracking_company:
            lines.append(f"  Carrier: {tracking_company}")
        if tracking:
            lines.append(f"  Tracking Number: {tracking}")
        if tracking_url:
            lines.append(f"  Tracking URL: {tracking_url}")
        shipped_day = _shipped_day(shipped_at)
        if shipped_day:
            lines.append(f"  Shipped: {shipped_day}")
        lines.append(f"  Current status: {readable_status}")
        lines.append("")
        lines.append("IF CUSTOMER ASKS WHERE THEIR ORDER IS:")
        lines.append("  Answer in plain English using the shipped day + current status above (e.g. 'shipped Tuesday and it's in transit').")
        lines.append("  Do NOT say 'check your email for tracking'. Do NOT paste the raw tracking URL as your main answer.")
        lines.append("  You may offer the tracking URL as a secondary option AFTER the plain-English status.")
    elif status == "unfulfilled":
        lines.append("")
        lines.append("Order has not shipped yet — if customer asks, tell them it's being prepared and hasn't shipped.")

    # Derive what actions are sensible given current order state
    state_notes = []
    if cancelled_at:
        state_notes.append("ORDER IS ALREADY CANCELLED — do not offer to cancel again.")
    if financial_status in ("refunded", "partially_refunded"):
        state_notes.append(f"ORDER IS ALREADY {financial_status.upper()} — do not offer another refund.")
    if status == "fulfilled" and not cancelled_at:
        state_notes.append("ORDER IS FULFILLED (shipped) — cancellation is not possible; address change is not possible.")
    if state_notes:
        lines.append("")
        lines.append("COMMON SENSE RULES FOR THIS ORDER:")
        for note in state_notes:
            lines.append(f"  ⚠ {note}")

    lines.extend([
        "",
        f"CRITICAL: Use ONLY the items listed above. Do NOT invent product names.",
        f"If asked what was ordered, say exactly: {', '.join(items) if items else 'order details unavailable'}",
        "=== END ORDER DATA ===",
    ])
    return "\n".join(lines)


# Shared with the dashboard (TicketDetail.jsx checks this prefix) and the Test
# Luna onboarding endpoint — every configured AI model (all Mistral keys, all
# Groq keys) is out of quota/rate-limited. Worded for a non-technical store
# owner reading it in the Escalations list, not a dev. Module-level (not a
# class attribute) so it resolves correctly even when tests call
# _get_provider_failure_response with a bare MagicMock() as self.
PROVIDER_OUTAGE_REASON = "AI reply limit reached — every connected AI model is temporarily out of quota. This resolves on its own once quota resets; reply manually for now."

# Rule 1 backstop (safety-non-negotiable): refunds/cancellations/address changes
# are only ever *staged* for merchant approval by return_actions_integration.py -
# this pipeline never executes them synchronously. The system prompt already
# instructs the model to never claim these are done (see ACTION RULES in
# _construct_v3_prompt), but that is a prompt instruction, not a guarantee.
# This is the code-level check for when the model ignores it anyway.
_UNCONFIRMED_ACTION_INTENTS = {"refund_request", "return_request", "exchange_request", "cancellation_request", "address_change"}
_UNCONFIRMED_ACTION_DETECTED = {"refund", "return", "exchange", "cancel_order", "change_address"}
_FALSE_SUCCESS_RE = re.compile(
    r"\b(has been|have been|is|was)\s+(processed|approved|completed|confirmed|issued|refunded|cancell?ed|updated)\b"
    r"|\b(successfully|already)\s+(processed|approved|completed|refunded|cancell?ed|updated)\b",
    re.IGNORECASE,
)


def _enforce_no_unconfirmed_action_success(structured: Dict[str, Any]) -> Dict[str, Any]:
    """If the model's reply claims a refund/cancellation/address-change already
    happened, that claim is always false in this pipeline (nothing sensitive is
    executed synchronously here - see module docstring above). Overrides the
    reply with an honest "sent for confirmation" message and forces escalation
    rather than letting a false success claim reach the customer."""
    is_action_reply = (
        structured.get("intent") in _UNCONFIRMED_ACTION_INTENTS
        or structured.get("action_detected") in _UNCONFIRMED_ACTION_DETECTED
    )
    if is_action_reply and _FALSE_SUCCESS_RE.search(structured.get("reply_body") or ""):
        logger.warning(
            f"[Agent] Reply claimed unconfirmed action success (intent={structured.get('intent')}, "
            f"action_detected={structured.get('action_detected')}) — overriding"
        )
        structured["reply_body"] = (
            "I've received your request and sent it to our team to confirm before anything "
            "changes on the order. You'll get an update as soon as it's handled."
        )
        structured["escalate"] = True
        structured["status"] = "escalated"
        structured["escalation_reason"] = "AI reply claimed an unconfirmed action was completed - routed to human review."
    return structured


def _enforce_no_ambiguous_product_claim(structured: Dict[str, Any], inventory_result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """If the live Shopify product lookup couldn't resolve to a single
    product (ambiguous=True — e.g. "Essential Hoodie V1" title-matching
    "Essential Hoodie V10"-"V19" too before the word-boundary fix, or any
    other genuinely ambiguous name), the system prompt already says "do not
    guess" - but confirmed live, the model can still generate a specific
    price/availability/variant claim anyway. Unlike that prompt instruction,
    this always overrides the reply with a clarification built from the
    tool's own verified matches list — it never inspects or trusts the
    model's text, so it works even when the model ignores the instruction."""
    if not inventory_result or not inventory_result.get("ambiguous"):
        return structured

    matches = inventory_result.get("matches") or []
    if matches:
        listed = ", ".join(matches[:8])
        clarification = (
            f"I found a few products matching that — {listed}. "
            "Could you let me know which one you mean so I can check it for you?"
        )
    else:
        clarification = (
            "I found a few products matching that name — could you tell me "
            "the exact one you mean so I can check it for you?"
        )

    logger.info("[Agent] Overriding reply for ambiguous product lookup — model must not guess which product the customer meant")
    structured["reply_body"] = clarification
    return structured


def _enforce_no_ungrounded_recommendation(structured: Dict[str, Any], recommendation_result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Same philosophy as _enforce_no_ambiguous_product_claim, applied to
    recommendations: the model must never present a recommended product,
    invent a complementary/"bought together" pairing, or claim a confident
    match when the live lookup explicitly said it doesn't have one. Whenever
    the tool result is anything other than a genuine success with real
    candidates, the reply is unconditionally replaced with the tool's own
    honest message — never trusting the model's text."""
    if not recommendation_result:
        return structured

    # Genuine success with real candidates: trust the model to summarize the
    # verified list already placed in tool_context (same approach as a
    # single exact inventory match) — nothing to override here.
    if recommendation_result.get("success") and recommendation_result.get("recommendations"):
        return structured

    message = recommendation_result.get("message")
    if not message:
        return structured

    logger.info("[Agent] Overriding reply for ungrounded recommendation result (ambiguous/no-candidates/no-pairing-data/failure) — model must not invent a recommendation")
    structured["reply_body"] = message
    return structured


class CustomerSuccessAgent:
    """
    V3 Customer Success Agent (Luna) for Aurelio & Finch.
    Uses pgvector RAG, deterministic sizing, and live Shopify/AfterShip tools.
    """

    def __init__(self):
        # Chat completions go through ai_provider_manager, which owns key/model
        # selection and failover across MISTRAL_API_KEY_PRIMARY + fallback keys.
        logger.info(
            f"Initializing V3 Agent — AI providers configured: {ai_provider_manager.has_providers}"
        )

    async def process_customer_query(self, query: str, customer_info: Dict[str, Any], tenant_id: Optional[str] = None, store_id: Optional[str] = None, ticket_id: Optional[str] = None, on_progress: Optional[Callable[[str, str], Awaitable[None]]] = None) -> Dict[str, Any]:
        """
        V3 Orchestration:
        1. RAG Retrieval (Policies, Brand, Product Info) - tenant-specific if tenant_id provided
        2. Sizing Check (if applicable)
        3. Tool Calls (Order/Shipping/Inventory) - REAL TIME
        4. Structured Response Generation
        5. Confidence & Escalation Enforcement

        on_progress(stage, label), when provided, is called synchronously at
        each real dispatch point below - immediately before the tool call or
        model call it describes actually runs. It never decides which stage
        happened; it only reports the branch this function already took, so
        the caller can surface accurate activity status to the customer
        without inventing progress that isn't real. Best-effort: a broken
        callback must never break query processing.
        """
        async def _emit(stage: str, label: str) -> None:
            if not on_progress:
                return
            try:
                await on_progress(stage, label)
            except Exception:
                logger.debug("[Agent] on_progress callback failed (non-blocking)", exc_info=True)

        try:
            _is_chat = "[CHAT MODE" in query
            await _emit("thinking", "Analyzing request…")

            # 1. RAG Retrieval - brand_knowledge_service.get_brand_context() is scoped
            # correctly by brand_id via match_brand_rag_chunks. rag_engine.get_relevant_context's
            # tenant-scoped path calls match_tenant_rag_chunks, which references a
            # tenant_id column migration 006 dropped from rag_chunks - that RPC always
            # errors, is silently swallowed, and falls through to an UNSCOPED
            # cross-tenant search every single time. Do not switch back to rag_engine
            # here without first fixing that RPC/table mismatch.
            rag_context = await brand_knowledge_service.get_brand_context(store_id, query) if store_id else ""
            logger.info(f"[Agent] RAG context retrieved: {len(rag_context)} chars")

            # 2. Sizing Engine - Get actual recommendation if we have measurements
            sizing_context = ""
            if any(k in query.lower() for k in ["size", "fit", "small", "medium", "large", "xl"]):
                height = customer_info.get("height")
                weight = customer_info.get("weight")
                fit_preference = customer_info.get("fit_preference", "true")

                if height and weight:
                    # Get actual size recommendation from size engine
                    try:
                        from src.services.size_engine import size_engine
                        product_data = {
                            "fit_type": "tailored",
                            "stretch_level": 1
                        }
                        user_profile = {
                            "height": height,
                            "weight": weight,
                            "fit_preference": fit_preference
                        }
                        size_result = size_engine.recommend_size(user_profile, product_data)

                        if size_result.get("success"):
                            size = size_result.get("recommended_size")
                            confidence = size_result.get("confidence", 0)
                            reasoning = size_result.get("reasoning", "")

                            confidence_text = "pretty confident" if confidence > 0.85 else "fairly sure"
                            sizing_context = f"\nBased on measurements ({height}cm, {weight}kg), I'm {confidence_text} they'd take a **{size}**."
                        else:
                            sizing_context = "\nNeed a bit more info to pin down the perfect size."
                    except Exception as e:
                        logger.error(f"Sizing engine error: {e}")
                        sizing_context = ""
                else:
                    sizing_context = "\nNeed height and weight to give a proper recommendation."

            # 3. REAL TIME TOOL CALLS - Get live data from Shopify & AfterShip
            tool_results = {}
            query_lower = query.lower()

            # Resolve brand-specific Shopify + Aftership credentials
            _brand_name = "our store"
            _agent_name = "Luna"
            _email_signature = None
            from src.services.reply_style_service import build_style_prompt_block
            _style_block = build_style_prompt_block(None)
            _brand_shopify_domain = None
            _brand_shopify_token = None
            _brand_aftership_key = None
            _default_store = "00000000-0000-0000-0000-000000000000"
            if store_id and store_id != _default_store:
                try:
                    from src.lib.supabase_client import supabase_select as _sel
                    from src.services.shopify_service import decrypt_token as _dec
                    _b = _sel("brands", {"id": f"eq.{store_id}"})
                    if _b:
                        _brand_name = _b[0].get("name") or _b[0].get("brand_name") or "our store"
                        _agent_name = _b[0].get("agent_name") or "Luna"
                        _email_signature = _b[0].get("email_signature") or None
                        try:
                            from src.services.reply_style_service import get_active_style
                            _style_block = build_style_prompt_block(get_active_style(_b[0]))
                        except Exception as _style_err:
                            logger.warning(f"[Agent] Reply Style resolution failed: {_style_err}")
                        if _b[0].get("shopify_connected") or _b[0].get("shopify_access_token"):
                            _brand_shopify_domain = _b[0].get("shopify_domain")
                            _raw = _b[0].get("shopify_access_token") or ""
                            _brand_shopify_token = _dec(_raw) if _raw else None
                        _brand_aftership_key = _b[0].get("aftership_api_key") or None
                        logger.info(f"[Agent] Brand found: name={_brand_name}, domain={_brand_shopify_domain}, aftership={'set' if _brand_aftership_key else 'not set'}")
                except Exception as _se:
                    logger.warning(f"[Agent] Brand lookup failed (non-blocking): {_se}")

            # Check for order status inquiry
            if any(kw in query_lower for kw in ["order", "shipped", "tracking", "delivered", "when will", "what did i order"]):
                # Try to extract order number from query
                order_match = re.search(r'#?(\d{3,6})', query)
                if order_match:
                    order_id = order_match.group(1)
                    await _emit("order_lookup", f"Finding order #{order_id}…")
                    tool_results["order_status"] = await v3_tools.get_order_status(
                        order_id,
                        shop_domain=_brand_shopify_domain,
                        access_token=_brand_shopify_token,
                        # Ownership check: a bare order number must never surface another
                        # customer's order data. customer_info["email"] is "" for an
                        # unverified chat-widget visitor, which always fails the check.
                        customer_email=customer_info.get("email") or "",
                    )
                    if tool_results["order_status"].get("success"):
                        await _emit("order_found", "Shopify order found")

                # Also try to look up by customer email if provided in query
                email_match = re.search(r'[\w.-]+@[\w.-]+\.\w+', query)
                customer_email = None
                if email_match:
                    customer_email = email_match.group(0)
                elif customer_info.get("email"):
                    # Use customer's email from their info
                    customer_email = customer_info.get("email")

                if customer_email:
                    if "order_status" not in tool_results:
                        await _emit("order_lookup", "Looking up your order…")
                    tool_results["orders_by_email"] = await v3_tools.get_orders_by_email(
                        customer_email,
                        shop_domain=_brand_shopify_domain,
                        access_token=_brand_shopify_token,
                    )

            # Recommendation intent is checked first so a message like "Do you
            # have anything like the Galactic Space Boots?" is recognized as
            # a recommendation question, not ALSO fired through the plain
            # inventory trigger below (both keyword sets can match the same
            # message, e.g. "do you have" + "anything like" — found live:
            # without this, that phrasing triggered a second, wasted, lower-
            # quality Shopify lookup for "anything like the galactic space
            # boots" as if it were a literal product name).
            _complementary_kw = ["goes well with", "wear with", "wear it with", "pair with", "pairs with", "pair it with", "buy with", "buy alongside"]
            _similar_kw = ["similar", "something like", "anything like", "alternative", "recommend", "what other", "other hoodies", "other shirts", "other options"]
            _is_recommendation_query = any(kw in query_lower for kw in _complementary_kw + _similar_kw)

            # Discovery queries — "I need a hoodie for winter, what would you
            # suggest" — have no specific anchor product to be "similar to",
            # so they don't match _similar_kw's patterns (which all require an
            # anchor after the keyword) and previously fell through to a plain
            # LLM response with no live product data at all, which is exactly
            # how the model ended up inventing material/fit details for
            # products it never actually looked up. Handled as its own
            # category-based path (discover_products_by_category), not routed
            # through the anchor-based recommendation flow above.
            _discovery_kw = ["what would you suggest", "any suggestions", "what do you suggest", "what should i get",
                              "what should i buy", "looking for a", "looking for something", "need something for",
                              "help me choose", "help me pick", "what would you recommend"]
            _is_discovery_query = (not _is_recommendation_query) and any(kw in query_lower for kw in _discovery_kw)

            # Check for inventory/product/price inquiry. The keyword gate below
            # is what keeps unrelated messages from ever reaching Shopify — a
            # product search only runs when both the gate AND one of the
            # extraction patterns below actually match. Extraction used to
            # depend on a tiny hardcoded category-word list (hoodie/jacket/
            # pants/...), which missed any merchant-specific product name and
            # every price question. These patterns pull the product name out
            # of the phrasing itself instead, so "Essential Crewneck" or "how
            # much is the Winter Parka?" reach the live tool the same as
            # "hoodie" always did. A miss here just means no live lookup runs
            # (falls back to normal RAG/LLM handling) — never a guess.
            if not _is_recommendation_query and not _is_discovery_query and any(kw in query_lower for kw in ["in stock", "available", "inventory", "do you have", "how much", "price", "cost", "tell me about", "describe"]):
                product = None
                for pattern in (
                    # Trigger-word variants first — non-greedy up to the FIRST
                    # occurrence of "in stock"/"available", not end-of-string,
                    # so trailing qualifiers ("...available in size M?") don't
                    # get swallowed into the product name. Found live: without
                    # this, "Is the Essential Hoodie V1 available in size M?"
                    # extracted the entire trailing clause instead of just the
                    # product name.
                    r"do you have\s+(?:the |a |an )?(.+?)\s+(?:in stock|available)\b",
                    r"do you have\s+(?:the |a |an )?(.+?)\s*\??$",
                    r"is\s+(?:the |a |an )?(.+?)\s+(?:in stock|available)\b",
                    r"how much (?:is|does)\s+(?:the |a |an )?(.+?)(?:\s+cost)?\s*\??$",
                    r"what(?:'s| is) the price of\s+(?:the |a |an )?(.+?)\s*\??$",
                    # Product-detail requests ("tell me about the Premium Hoodie
                    # V23", "describe the Essential Hoodie") — previously
                    # matched nothing, so this class of question got only
                    # whatever RAG happened to retrieve (frozen at last import,
                    # no live price/availability, no signal to the model that
                    # it might be stale) instead of a live lookup.
                    r"tell me (?:more )?about\s+(?:the |a |an )?(.+?)\s*\??$",
                    r"describe\s+(?:the |a |an )?(.+?)\s*\??$",
                ):
                    m = re.search(pattern, query_lower)
                    if m:
                        candidate = m.group(1).strip(" ?.!")
                        if len(candidate) >= 2:
                            product = candidate
                            break
                if not product:
                    # Fallback for phrasing the patterns above don't cover.
                    fallback_match = re.search(r'(hoodie|jacket|pants|shirt|tshirt|coat|dress|skirt)', query_lower)
                    if fallback_match:
                        product = fallback_match.group(1)
                if product:
                    await _emit("product_lookup", "Finding product…")
                    tool_results["inventory"] = await v3_tools.get_inventory_status(
                        product,
                        shop_domain=_brand_shopify_domain,
                        access_token=_brand_shopify_token,
                    )
                    if tool_results["inventory"].get("success"):
                        await _emit("product_found", "Shopify product found")

            # "what is X" is deliberately NOT in the keyword gate above — it's
            # also the single most common phrasing for policy/account
            # questions ("what is your return policy", "what is my order
            # status"), which would otherwise get a wasted, wrong Shopify
            # lookup and an honest-but-unhelpful "couldn't find that" instead
            # of their real answer from RAG/order lookup. Only fires here when
            # the extracted phrase itself looks product-like — contains a
            # digit (e.g. "V23") or one of the known category words — narrow
            # enough to catch "what is the Premium Hoodie V23" without
            # catching ordinary policy questions.
            if (
                "inventory" not in tool_results
                and not _is_recommendation_query and not _is_discovery_query
                and "what is" in query_lower
            ):
                m = re.search(r"what is\s+(?:the |a |an )?(.+?)\s*\??$", query_lower)
                if m:
                    candidate = m.group(1).strip(" ?.!")
                    looks_product_like = bool(re.search(r'\d', candidate)) or bool(
                        re.search(r'(hoodie|jacket|pants|shirt|tshirt|coat|dress|skirt)', candidate)
                    )
                    if len(candidate) >= 2 and looks_product_like:
                        await _emit("product_lookup", "Finding product…")
                        tool_results["inventory"] = await v3_tools.get_inventory_status(
                            candidate,
                            shop_domain=_brand_shopify_domain,
                            access_token=_brand_shopify_token,
                        )
                        if tool_results["inventory"].get("success"):
                            await _emit("product_found", "Shopify product found")

            # General product-mention fallback — catches product-specific
            # questions in any phrasing the more specific triggers above
            # miss: "Is Premium Hoodie V23 soft and durable?", "What
            # material is Premium Hoodie V23?", "How does it fit?", "Does
            # Hoodie V23 come in cotton?", "Is Hoodie V23 good for winter?".
            # Enumerating every possible question phrasing doesn't scale;
            # this instead looks for a product-name-shaped mention anywhere
            # in the message (a category word, optionally with one
            # preceding qualifier and/or a version suffix) and always
            # attempts a live lookup when one is found. Reuses the exact
            # same get_inventory_status()/find_products_by_title() search
            # and its existing exact/ambiguous handling — this only decides
            # WHETHER to look something up, never WHAT the answer is.
            if "inventory" not in tool_results and not _is_recommendation_query and not _is_discovery_query:
                _mention = re.search(
                    r'\b(\w+\s+)?(hoodie|jacket|pants|shirt|tshirt|coat|dress|skirt)(\s+v\d+)?\b',
                    query_lower,
                )
                if _mention:
                    # The preceding-word group is meant to catch a real
                    # product qualifier ("Premium"/"Essential"/"Signature"
                    # Hoodie) — filter out common question/verb words that
                    # happen to sit immediately before the category word
                    # ("Does Hoodie V23...", "Is Hoodie V23...") so they
                    # don't get folded into the search term and cause an
                    # honest but wrong "not found" (find_products_by_title
                    # requires the whole captured phrase to appear in the
                    # title).
                    _prefix = (_mention.group(1) or "").strip()
                    _prefix_stopwords = {
                        "does", "do", "is", "are", "was", "were", "can", "could",
                        "will", "would", "should", "the", "a", "an", "this", "that",
                        "your", "our", "my", "have", "has", "had",
                    }
                    if _prefix in _prefix_stopwords:
                        _prefix = ""
                    _candidate = " ".join(g for g in (_prefix, _mention.group(2), (_mention.group(3) or "").strip()) if g)
                    if _candidate:
                        await _emit("product_lookup", "Finding product…")
                        tool_results["inventory"] = await v3_tools.get_inventory_status(
                            _candidate,
                            shop_domain=_brand_shopify_domain,
                            access_token=_brand_shopify_token,
                        )
                        if tool_results["inventory"].get("success"):
                            await _emit("product_found", "Shopify product found")

            # Check for a product-recommendation request ("show me something
            # similar", "what else do you have", "what goes with this").
            # Distinguishes "similar" (deterministic type/tag/vendor scoring —
            # real data) from "complementary" (no real pairing data exists
            # anywhere in this architecture — handled honestly inside
            # get_product_recommendations, never faked as same-type results).
            # "this"/"that" alone (no product actually named) is deliberately
            # left unresolved — no cross-turn anchor tracking exists yet, so
            # skipping the tool call here is the honest choice, not a guess.
            if _is_recommendation_query:
                rec_intent = "complementary" if any(kw in query_lower for kw in _complementary_kw) else "similar"
                anchor = None
                for pattern in (
                    r"(?:goes well with|wear with|wear it with|pair(?:s)? with|pair it with|buy with|buy alongside)\s+(?:the |a |an )?(.+?)\s*\??$",
                    r"(?:similar to|anything like|something like|alternatives? to)\s+(?:the |a |an )?(.+?)\s*\??$",
                    r"other\s+(.+?)(?:\s+do you have|\s+available|\s+in stock)?\s*\??$",
                ):
                    m = re.search(pattern, query_lower)
                    if m:
                        candidate = m.group(1).strip(" ?.!")
                        # A bare pronoun isn't a product name — no cross-turn
                        # anchor tracking exists to resolve "this"/"that" to a
                        # real product yet, so leave it unresolved rather than
                        # searching Shopify for the literal word "this".
                        if len(candidate) >= 2 and candidate not in ("this", "that", "it", "this one", "that one"):
                            anchor = candidate
                            break
                if anchor:
                    await _emit("product_search", "Finding products…")
                    tool_results["recommendations"] = await v3_tools.get_product_recommendations(
                        anchor,
                        shop_domain=_brand_shopify_domain,
                        access_token=_brand_shopify_token,
                        intent=rec_intent,
                    )

            # Discovery query — "I need a hoodie for winter, what would you
            # suggest" — no anchor product to compare against, so pull a
            # plain category word out of the message instead (same small,
            # deterministic category list the inventory-trigger fallback
            # already uses) and hand it to discover_products_by_category().
            # No category word found just means no live lookup runs, same
            # fallback-to-normal-handling rule as every other trigger here.
            elif _is_discovery_query:
                _category_match = re.search(r'(hoodie|jacket|pants|shirt|tshirt|coat|dress|skirt)', query_lower)
                if _category_match:
                    await _emit("product_search", "Finding products…")
                    tool_results["recommendations"] = await v3_tools.discover_products_by_category(
                        _category_match.group(1),
                        shop_domain=_brand_shopify_domain,
                        access_token=_brand_shopify_token,
                    )

            # 3b. Aftership live tracking — runs only when order was found and has a tracking number
            if "order_status" in tool_results and tool_results["order_status"].get("success"):
                _order = tool_results["order_status"]
                _tn = _order.get("tracking_number")
                _tc = _order.get("tracking_company") or ""
                if _tn and _brand_aftership_key:
                    try:
                        from src.services.tracking_service import (
                            get_tracking_status,
                            shopify_carrier_to_aftership_slug,
                            build_tracking_context,
                        )
                        _slug = shopify_carrier_to_aftership_slug(_tc)
                        if _slug:
                            await _emit("shipping_lookup", "Checking shipping status…")
                            _tracking_info = await get_tracking_status(_tn, _slug, _brand_aftership_key)
                            tool_results["tracking_info"] = _tracking_info
                            logger.info(f"[Agent] Aftership tracking fetched: status={(_tracking_info or {}).get('status')}")
                        else:
                            logger.info(f"[Agent] Carrier '{_tc}' not in Aftership map — skipping live tracking")
                    except Exception as _te:
                        logger.warning(f"[Agent] Aftership call failed (non-blocking): {_te}")

            # 4. Build tool context for the AI (explicit Shopify data — AI must use this verbatim)
            tool_context = ""
            if tool_results:
                if "order_status" in tool_results:
                    order = tool_results["order_status"]
                    if order.get("success"):
                        # Build Aftership tracking block (or URL fallback)
                        try:
                            from src.services.tracking_service import build_tracking_context
                            _tracking_ctx = build_tracking_context(
                                tracking_info=tool_results.get("tracking_info"),
                                tracking_number=order.get("tracking_number"),
                                tracking_url=order.get("tracking_url"),
                                tracking_company=order.get("tracking_company"),
                            )
                        except Exception as _tce:
                            logger.warning(f"[Agent] Tracking context build failed (non-blocking): {_tce}")
                            _tracking_ctx = ""
                        order_block = _build_order_context(order, tracking_context=_tracking_ctx)
                        tool_context += order_block + "\n"
                        logger.info(f"[Agent] Order context built:\n{order_block}")
                    elif order.get("error"):
                        mentioned_num = order.get("order_number", "")
                        tool_context += f"ORDER LOOKUP FAILED: Could not retrieve order #{mentioned_num} from Shopify.\n"
                        tool_context += "Do NOT invent product names or pretend to know the order. Tell the customer you're unable to pull up their order right now and ask them to reply with their email address or order confirmation number so a team member can follow up.\n"

                if "orders_by_email" in tool_results:
                    orders = tool_results["orders_by_email"]
                    if orders.get("success") and orders.get("orders"):
                        order_list = [f"#{o.get('order_number')} ({o.get('status')})" for o in orders.get("orders", [])]
                        if len(order_list) > 1:
                            tool_context += f"Customer has multiple orders: {', '.join(order_list)}. Ask which order they mean before answering — do not guess.\n"
                        else:
                            tool_context += f"Customer's orders: {', '.join(order_list)}\n"
                    elif orders.get("error"):
                        tool_context += "ORDER LOOKUP BY EMAIL FAILED: Do not invent or guess order details. Ask the customer for their order number, or let them know a team member will follow up.\n"

                if "inventory" in tool_results:
                    inv = tool_results["inventory"]
                    if inv.get("ambiguous"):
                        tool_context += f"{inv.get('message')} Ask the customer to clarify which product before answering — do not guess or pick one.\n"
                    elif inv.get("success"):
                        tool_context += f"{inv.get('message', 'Available')}\n"
                        # Only ever included when Shopify actually returned it —
                        # never construct or guess a link/image the customer wasn't
                        # given by the live lookup.
                        if inv.get("product_url"):
                            tool_context += f"Product link: {inv['product_url']}\n"
                        if inv.get("image_url"):
                            tool_context += f"Product image: {inv['image_url']}\n"
                        if inv.get("price") is not None:
                            tool_context += f"LIVE Shopify price: {inv['price']}\n"
                        if inv.get("description"):
                            tool_context += f"LIVE Shopify description: {inv['description']}\n"
                        variant_opts = [v.get("options") for v in (inv.get("variants") or []) if v.get("options")]
                        if variant_opts:
                            opt_desc = "; ".join(", ".join(f"{k} {v}" for k, v in opts.items()) for opts in variant_opts)
                            tool_context += f"Available options: {opt_desc}\n"
                        # Explicit source hierarchy for this exact, identified
                        # product: this live Shopify data is authoritative for
                        # price/description/attributes — it overrides anything
                        # KNOWLEDGE BASE below says about the same product if
                        # the two ever disagree (e.g. an older RAG-imported
                        # price). Any attribute the customer asks about that
                        # isn't in this data or in KNOWLEDGE BASE must be
                        # answered as unverified — never inferred from what
                        # the material/category "usually" means (Organic
                        # Cotton does NOT imply soft, breathable, durable, or
                        # warm unless that word is actually written above).
                        tool_context += (
                            "This live Shopify data is the AUTHORITATIVE source for this product's price, "
                            "description, and attributes — it overrides KNOWLEDGE BASE below if they conflict. "
                            "Only state material/fit/quality/softness/durability/warmth/popularity claims that "
                            "are explicitly written above. If the customer asks about an attribute not shown "
                            "here (e.g. softness, durability, warmth) and it isn't written above, say you don't "
                            "have verified information about it rather than guessing or inferring it from the "
                            "material name.\n"
                        )
                    else:
                        tool_context += f"INVENTORY LOOKUP: {inv.get('message', 'Could not verify inventory')}. Do NOT guess stock levels — use this message as-is or offer to have a team member confirm.\n"

                if "recommendations" in tool_results:
                    rec = tool_results["recommendations"]
                    if rec.get("ambiguous"):
                        tool_context += f"{rec.get('message')} Ask the customer to clarify which product before recommending anything — do not guess.\n"
                    elif rec.get("no_pairing_data"):
                        tool_context += f"{rec.get('message')} Do NOT invent a complementary/pairing suggestion — say this honestly, exactly as given.\n"
                    elif rec.get("no_candidates"):
                        tool_context += f"{rec.get('message')} Do NOT invent a similar product — there genuinely isn't a confident match.\n"
                    elif rec.get("needs_clarification"):
                        tool_context += (
                            f"TOO MANY MATCHES for '{rec.get('category')}' ({rec.get('match_count')} products) — "
                            f"do not list them. Ask the customer this exact clarifying question instead: {rec.get('message')}\n"
                        )
                    elif rec.get("success") and rec.get("recommendations"):
                        tool_context += f"{rec.get('message')}\n"
                        for r in rec["recommendations"]:
                            line = f"  - {r['title']}"
                            if r.get("price"):
                                line += f" (${r['price']})"
                            line += f" — {'in stock' if r.get('available') else 'currently out of stock'}"
                            if r.get("matching_reason"):
                                line += f" — why it's suggested: {r['matching_reason']}"
                            if r.get("description"):
                                line += f" — description: {r['description']}"
                            if r.get("product_url"):
                                line += f" — link: {r['product_url']}"
                            tool_context += line + "\n"
                        tool_context += (
                            "Only mention the products listed above with their actual reason/availability/link "
                            "exactly as given — do not invent additional recommendations, prices, or links. "
                            "CRITICAL: only state material, warmth, fit, fabric, or other physical/quality attributes "
                            "that are explicitly written in the \"description\" field above for that exact product — "
                            "if a product has no description field, do NOT invent one (no \"soft\", \"breathable\", "
                            "\"relaxed fit\", \"Merino wool\", etc. unless that exact word appears in its description). "
                            "Never claim \"customers also bought\" style behavioral claims not present here.\n"
                        )
                    else:
                        tool_context += f"RECOMMENDATION LOOKUP: {rec.get('message', 'Could not look up recommendations')}. Do NOT invent a recommendation — use this message as-is or offer to have a team member help.\n"

            # 4. Return/Exchange Action Layer — runs identically for chat and
            # Gmail. Chat used to skip this entirely (relying on the main
            # structured-response "intent" field the model self-reports),
            # which meant chat customers could be told a refund/cancel/
            # address-change request was "sent to our team" when no action
            # row was ever created. Real eligibility check + staging must run
            # on every channel — this is the only source of truth for
            # whether an action actually exists.
            action_context = ""
            action_taken = None
            from src.services.intent_detector import intent_detector as _intent_detector
            _intent_result = await _intent_detector.detect(query)
            if _intent_result.has_action:
                logger.info(f"[ReturnActions] Intent detected: {_intent_result.action_type} (order={_intent_result.order_id}, source={_intent_result.source})")
                action_result = await return_actions.handle_return_intent(
                    query=query,
                    customer_info=customer_info,
                    existing_tool_results=tool_results,
                    tenant_id=tenant_id,
                    brand_id=store_id,
                    ticket_id=ticket_id,
                    intent_result=_intent_result,
                    on_progress=on_progress,
                )
                action_context = action_result.get("action_context", "")
                logger.info(f"[ReturnActions] Action context: {action_context[:200] if action_context else 'EMPTY'}")
                tool_results["return_action"] = action_result
                action_taken = action_result.get("staged")
            else:
                logger.info(f"[ReturnActions] No action intent (source={_intent_result.source})")

            # 5. Response Generation
            # Only announce a policy check when no live Shopify tool ran and we
            # actually have knowledge-base content to ground the answer in —
            # otherwise this is just "Preparing your answer…" like any other
            # query with nothing brand-specific to check.
            if not tool_results and rag_context:
                await _emit("policy_check", "Checking our policies…")
            await _emit("preparing", "Preparing your answer…")
            system_prompt = self._construct_v3_prompt(customer_info, rag_context, sizing_context, tool_context, action_context, brand_name=_brand_name, agent_name=_agent_name, style_block=_style_block)

            # Defensive check - ensure at least one AI provider is configured
            if not ai_provider_manager.has_providers:
                logger.error("No AI providers configured - API key(s) may be missing")
                return self._get_fallback_response("No AI providers configured", brand_name=_brand_name, agent_name=_agent_name, email_signature=_email_signature)

            same_prompt_messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Customer: {query}"}
            ]
            try:
                # Same prompt/context/temperature on every attempt — failover only
                # changes which API key (and its paired model) serves the request.
                response, provider_label, _model, _ai_usage = await ai_provider_manager.create_chat_completion(
                    messages=same_prompt_messages,
                    temperature=0.1,
                    response_format={"type": "json_object"},
                )
            except AllProvidersFailedError as api_error:
                logger.error(f"[Agent] All AI providers failed: {api_error}")
                return self._get_provider_failure_response(brand_name=_brand_name, agent_name=_agent_name, email_signature=_email_signature)

            raw_content = response.choices[0].message.content
            if not raw_content:
                logger.error("Empty response from API")
                return self._get_fallback_response("Empty API response", brand_name=_brand_name, agent_name=_agent_name, email_signature=_email_signature)

            try:
                # Clean up response - remove markdown code blocks if present
                clean_content = raw_content.strip()
                if clean_content.startswith("```json"):
                    clean_content = clean_content[7:]
                if clean_content.startswith("```"):
                    clean_content = clean_content[3:]
                if clean_content.endswith("```"):
                    clean_content = clean_content[:-3]
                clean_content = clean_content.strip()

                structured = json.loads(clean_content)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON response: {e}. Raw content: {raw_content[:500]}")
                return self._get_fallback_response(f"JSON parse error: {str(e)}", brand_name=_brand_name, agent_name=_agent_name, email_signature=_email_signature)

            # 4. Confidence Calculation - Be more lenient
            sentiment = sentiment_analyzer.analyze_sentiment_detailed(query)

            # Start higher and be less aggressive with penalties
            confidence = 0.80
            if not rag_context: confidence -= 0.15  # Reduced penalty
            if sentiment["label"] == "negative": confidence -= 0.10  # Reduced penalty

            # Boost for standard, low-risk intents
            if structured.get("intent") in ["order_status_inquiry", "shipping_inquiry", "sizing_inquiry", "product_inquiry"] and structured.get("risk_level") == "low":
                confidence += 0.10

            # Ensure minimum confidence of 30% if we got a valid response
            confidence_out_of_100 = int(max(0.30, min(1, confidence)) * 100)
            structured["confidence_score"] = confidence_out_of_100

            # 5. Escalation Thresholds (more lenient)
            if confidence_out_of_100 < 30:
                logger.warning(f"Low confidence: {confidence_out_of_100}%. Still sending response.")
                structured["status"] = "auto_resolved"  # Send anyway
            elif confidence_out_of_100 < 70 or structured.get("risk_level") == "high":
                structured["status"] = "escalated"
                structured["escalate"] = True
            else:
                structured["status"] = "auto_resolved"

            # 5b. Safety backstop: never let a false "action completed" claim through
            structured = _enforce_no_unconfirmed_action_success(structured)

            # 5c. Safety backstop: never let a specific product/price/
            # availability claim through when the live Shopify lookup
            # couldn't resolve to exactly one product.
            structured = _enforce_no_ambiguous_product_claim(structured, tool_results.get("inventory"))

            # 5d. Safety backstop: never let the model present a recommended
            # product or invented pairing when the live lookup didn't
            # actually produce a confident, grounded candidate.
            structured = _enforce_no_ungrounded_recommendation(structured, tool_results.get("recommendations"))

            # 6. Signature Enforcement - Make it natural, not robotic. Falls
            # back to the neutral idiom "there" ("Hey there,") - never a
            # placeholder treated as a real name - when none is known.
            name = (_known_customer_name(customer_info.get("name")) or "there").split()[0]
            reply = structured.get("reply_body", "")

            # Post-process: ensure each sentence is on its own line for readability
            # Split on sentence endings and add newlines
            import re as regex_module
            sentences = regex_module.split(r'([.!?])\s+', reply)
            if len(sentences) > 1:
                formatted_parts = []
                for i in range(0, len(sentences)-1, 2):
                    sent = sentences[i].strip()
                    punct = sentences[i+1] if i+1 < len(sentences) else ''
                    if sent:
                        formatted_parts.append(sent + punct)
                reply = '\n'.join(formatted_parts)

            # For email: add a greeting, but only if the model's own reply
            # doesn't already open with one — Reply Style presets like
            # Professional/Premium instruct the model to write "Dear {name},"
            # style openings, which the old "hi"/"hey"/"thanks" prefix check
            # didn't recognize, so it prepended a second greeting on top.
            # Chat skips this entirely — widget is already mid-conversation.
            if not _is_chat:
                if reply and name.lower() not in reply.lower()[:30]:
                    structured["reply_body"] = f"Hey {name},\n\n{reply}"

            # Merchant-set signature wins verbatim; otherwise fall back to the
            # generated "— {agent_name}\n{brand_name}" sign-off.
            if _email_signature:
                if _email_signature not in structured["reply_body"]:
                    structured["reply_body"] += f"\n\n{_email_signature}"
            elif _agent_name not in structured["reply_body"]:
                structured["reply_body"] += f"\n\n— {_agent_name}\n{_brand_name}"

            # Attach order_data for widget card display
            _os = tool_results.get("order_status", {})
            if _os.get("success"):
                _fs = _os.get("status") or "pending"
                if _os.get("cancelled_at"):
                    _widget_status = "cancelled"
                elif _fs == "fulfilled":
                    _widget_status = "fulfilled"
                elif _fs in ("partial", "unfulfilled"):
                    _widget_status = "processing"
                else:
                    _widget_status = "pending"
                _fin = _os.get("financial_status") or ""
                _payment_status = "refunded" if "refund" in _fin else ("paid" if _fin == "paid" else "pending")
                structured["order_data"] = {
                    "orderNumber": str(_os.get("order_number", "")),
                    "items": [
                        {"name": i.get("title", ""), "quantity": i.get("quantity", 1), "price": i.get("price", "")}
                        for i in _os.get("items", [])
                    ],
                    "status": _widget_status,
                    "paymentStatus": _payment_status,
                    "cancelledAt": _os.get("cancelled_at"),
                }

            # The real staging outcome (or None if no action was created) —
            # callers must use this, not the "intent"/"status" fields above,
            # to decide whether to tell the customer an action was staged.
            structured["action_taken"] = action_taken

            structured["model_used"] = _model
            # Real per-call usage from the provider layer (tokens may be None
            # if the provider's response didn't include a usage block — never
            # fabricated). Callers that persist conversation records (e.g. the
            # email worker's _log_conversation) can read this to populate
            # ai_conversations.tokens_used/latency_ms instead of leaving them
            # NULL. Only covers this one model call, not intent_detector's or
            # email_guardian's separate (uninstrumented) calls.
            structured["ai_usage"] = _ai_usage
            # Only the real, model-generated path sets this — both
            # _get_provider_failure_response (all providers exhausted) and
            # _get_fallback_response (empty response / JSON parse error /
            # any other exception) return the same canned "having trouble"
            # text without it, so callers gating AI-reply quota on this flag
            # never charge a customer's trial/plan quota for a failed call.
            structured["ai_reply_generated"] = True
            return structured

        except Exception as e:
            import traceback
            logger.error(f"V3 Agent Error: {e}")
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return self._get_fallback_response(str(e), brand_name=_brand_name, agent_name=_agent_name, email_signature=_email_signature)

    def _construct_v3_prompt(self, customer_info: Dict[str, Any], rag_context: str, sizing_context: str, tool_context: str = "", action_context: str = "", brand_name: str = "our store", agent_name: str = "Luna", style_block: str = "") -> str:
        order_critical = (
            "\n⚠ LIVE DATA FROM SHOPIFY — USE ONLY THESE DETAILS:\n"
            "• Reference ONLY the product names, quantities, and totals listed below.\n"
            "• Do NOT invent or assume any product names, prices, or details not listed here.\n"
        ) if tool_context.strip() else "\n(No order data fetched — if the customer asks about an order, ask for their order number.)\n"
        return f"""
        You are {agent_name}, the AI customer support employee for {brand_name}. NOT a corporate bot — sound like a real person.

        REPLY STYLE (wording and tone only — this never changes facts, actions, or policy):
        {style_block}

        RULES:
        - Never use words like "algorithm", "system", "deterministic", "variant"
        - Always sound human and friendly
        - NEVER refer to products not listed in ORDER INFO below
        - NEVER say "let me check", "I'll look into it", "give me a moment", or anything that implies you will do something later — respond fully RIGHT NOW based only on what is in ORDER INFO
        - If ORDER INFO shows a lookup failure, apologize you can't see the order and ask the customer for their order confirmation email or contact details so someone can follow up

        TRACKING RULES (CRITICAL — follow exactly):
        - If ORDER INFO gives you a shipped day + status, answer in plain English using both — e.g. "Your order shipped Tuesday and it's in transit, should arrive in a couple days."
        - Do NOT say "check your email for tracking." Do NOT paste the raw tracking URL as your main answer.
        - If a tracking URL is available, you MAY offer it as a secondary option AFTER the plain-English status: "You can also track it here: [url]"
        - If status is unknown, say it was recently shipped and tracking should update within 24 hours.
        - If the order hasn't shipped yet, say it's being prepared and hasn't shipped.

        FORMATTING RULES:
        - NEVER use em dashes (—) or en dashes (–) anywhere in your response
        - NEVER use hyphens to join or separate clauses in a sentence
        - Use a comma or start a new sentence instead of a dash
        - WRONG: "I'd love to help—could you share your order number?"
        - RIGHT: "I'd love to help! Could you share your order number?"

        KNOWLEDGE BASE (authoritative ONLY for claims explicitly present in the text below —
        do NOT invent material, fit, texture, quality, popularity, durability, price,
        availability, or marketing claims that aren't written here):
        {rag_context}

        SIZING:
        {sizing_context}

        ORDER INFO:{order_critical}
        {tool_context}

        RETURN/EXCHANGE STATUS:
        {action_context}

        CUSTOMER:
        Name: {_known_customer_name(customer_info.get('name')) or "Not known - do NOT guess or invent one, and never derive one from the email address or an order number. If the greeting style above calls for a name (e.g. \"Dear {name},\"), use a neutral opening instead - \"Hi there,\" or \"Thanks for reaching out,\" - and NEVER write \"Dear There\" or treat any placeholder word as if it were the customer's real name."}
        Email: {customer_info.get('email')}
        History: {customer_info.get('history', 'New customer')}

        ACTION RULES (IMPORTANT - DO NOT AUTO-CONFIRM):
        1. For refunds, returns, exchanges, cancellations, or address changes - NEVER say it's done
        2. Instead say: "I've prepared your request and sent it to our team for confirmation. You'll receive an update shortly!"
        3. NEVER use words like "processed", "approved", "completed", "done", "exchanged"
        4. Always say the request is "being reviewed" or "sent for confirmation"
        5. If not eligible - be honest and offer alternatives
        6. NEVER invent a specific policy detail - a time window ("within 2 hours of ordering"),
           a cutoff, a fee, a percentage, a return/exchange window, a restocking fee, or any other
           concrete rule - unless that exact detail appears in KNOWLEDGE BASE or RETURN/EXCHANGE
           STATUS below. If asked how a policy works and no grounded detail is available, say
           you'll need to confirm the specifics rather than guessing a number.
        7. For an exchange: RETURN/EXCHANGE STATUS below already reflects LIVE Shopify stock/price -
           never say a size/color/product is available or unavailable except exactly as stated there.
           Never invent a replacement item, variant, or price difference that isn't given to you.
        8. If RETURN/EXCHANGE STATUS says a request is already pending, approved, or completed - do
           NOT say a new request was sent. Reflect the real, current status truthfully instead.
        9. Only greet the customer by name if CUSTOMER Name above gives you a real one. If it says
           "Not known", use a neutral opening instead - never write "Dear There" or greet them by
           any placeholder word as if it were their real name.

        COMMON SENSE — READ ORDER STATUS BEFORE RESPONDING:
        - If ORDER DATA says "CANCELLED" — do NOT offer cancellation. Acknowledge it is cancelled already.
        - If ORDER DATA says "refunded" or "partially_refunded" — do NOT offer a refund. Acknowledge it is refunded already.
        - If ORDER DATA says "fulfilled" (shipped) AND a tracking URL is present — share that URL directly in your reply. Never say "check your email".
        - If ORDER DATA says "fulfilled" (shipped) — do NOT offer cancellation or address change. Offer reship/refund if relevant.
        - Never suggest an action that the order state makes impossible.

        RESPONSE (JSON only):
        {{
            "intent": "what they want (refund_request|return_request|exchange_request|cancellation_request|address_change|order_status_inquiry|shipping_inquiry|sizing_inquiry|product_inquiry|general_inquiry)",
            "sentiment": "positive|neutral|negative",
            "risk_level": "low|medium|high",
            "escalate": false,
            "action_detected": "refund|return|exchange|cancel_order|change_address|none",
            "confidence_score": 80,
            "reply_body": "your friendly response - NEVER confirm actions are done, only say they're being reviewed",
            "suggested_actions": []
        }}

        Include "confidence_score" as an integer 0-100 reflecting how certain you are the reply fully resolves the issue.
        95-100: definitive answer. 80-94: quite sure. 60-79: mostly sure. Below 60: escalate.
        """

    def _get_fallback_response(self, error: str, brand_name: str = "", agent_name: str = "Luna", email_signature: str = None) -> Dict[str, Any]:
        logger.error(f"Using fallback response due to error: {error}")
        sign_off = email_signature or (f"— {agent_name}\n{brand_name}" if brand_name else f"— {agent_name}")
        return {
            "intent": "general_inquiry",
            "sentiment": "neutral",
            "risk_level": "medium",
            "confidence_score": 40,
            "escalate": True,
            "escalation_reason": f"System error: {error}",
            "reply_body": f"Hey there!\n\nThanks for reaching out. I'm having a bit of trouble processing your message right now, but I've flagged this for my team to take a look.\n\nSomeone will get back to you shortly!\n\n{sign_off}",
            "status": "escalated"
        }

    def _get_provider_failure_response(self, brand_name: str = "", agent_name: str = "Luna", email_signature: str = None) -> Dict[str, Any]:
        """Every configured AI provider (all Mistral keys, all Groq fallback keys)
        failed for this request — distinct from _get_fallback_response so the
        escalation card reads as a known, temporary quota problem, not a generic
        "system error". provider_outage=True lets callers (Test Luna) detect this
        specific case without string-matching escalation_reason."""
        sign_off = email_signature or (f"— {agent_name}\n{brand_name}" if brand_name else f"— {agent_name}")
        return {
            "intent": "general_inquiry",
            "sentiment": "neutral",
            "risk_level": "medium",
            "confidence_score": 40,
            "escalate": True,
            "provider_outage": True,
            "escalation_reason": PROVIDER_OUTAGE_REASON,
            "reply_body": f"Hey there!\n\nThanks for reaching out. I'm having a bit of trouble processing your message right now, but I've flagged this for my team to take a look.\n\nSomeone will get back to you shortly!\n\n{sign_off}",
            "status": "escalated"
        }

    async def generate_channel_appropriate_response(self, query: str, customer_info: Dict[str, Any], channel: str, tenant_id: Optional[str] = None, store_id: Optional[str] = None, ticket_id: Optional[str] = None) -> Dict[str, Any]:
        return await self.process_customer_query(query, customer_info, tenant_id=tenant_id, store_id=store_id, ticket_id=ticket_id)

customer_success_agent = CustomerSuccessAgent()
