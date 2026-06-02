import os
import json
import re
import logging
from typing import Dict, Any, Optional, List

# Note: tenant_id parameter is used for multi-tenant RAG retrieval

# Set OPENAI_API_KEY for compatibility with Mistral's OpenAI-compatible API
if not os.getenv("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = os.getenv("MISTRAL_API_KEY", "")

from openai import OpenAI

from ..services.rag_engine import rag_engine
from ..services.sentiment_analyzer import sentiment_analyzer
from ..services.size_engine import size_engine
from ..services.tools import v3_tools
from ..services.return_actions_integration import return_actions
from ..lib.supabase_client import supabase_rpc, supabase_update

logger = logging.getLogger(__name__)


def _format_address(addr: dict) -> str:
    if not addr:
        return "No shipping address"
    parts = [addr.get("name", ""), addr.get("address1", ""), addr.get("city", ""),
             addr.get("province", ""), addr.get("country", "")]
    return ", ".join(p for p in parts if p)


def _build_order_context(order: dict) -> str:
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

    lines = [
        "=== REAL ORDER DATA FROM SHOPIFY — USE THIS EXACT INFORMATION ===",
        f"Order Number: #{order_num}",
        f"Status: {status}",
    ]
    if total:
        lines.append(f"Total: Rs {total}")
    if items:
        lines.append("Items Ordered:")
        for item_line in items:
            lines.append(f"  - {item_line}")
    if tracking:
        lines.append(f"Tracking Number: {tracking}")
    if tracking_url:
        lines.append(f"Track Here: {tracking_url}")
    lines.extend([
        "",
        f"CRITICAL: Use ONLY the items listed above. Do NOT invent product names.",
        f"If asked what was ordered, say exactly: {', '.join(items) if items else 'order details unavailable'}",
        "=== END ORDER DATA ===",
    ])
    return "\n".join(lines)


class CustomerSuccessAgent:
    """
    V3 Customer Success Agent (Luna) for Aurelio & Finch.
    Uses pgvector RAG, deterministic sizing, and live Shopify/AfterShip tools.
    """

    def __init__(self):
        self.model = os.getenv("MISTRAL_MODEL", "mistral-large-latest")
        api_key = os.getenv("MISTRAL_API_KEY") or os.getenv("OPENAI_API_KEY")
        logger.info(f"Initializing V3 Agent with API key: {'set' if api_key else 'NOT SET'}, base_url: {os.getenv('MISTRAL_API_BASE_URL', 'https://api.mistral.ai/v1')}")
        if not api_key:
            logger.warning("No MISTRAL_API_KEY found, V3 Agent will use fallback mode")
            self.openai_client = None
        else:
            self.openai_client = OpenAI(
                api_key=api_key,
                base_url=os.getenv("MISTRAL_API_BASE_URL", "https://api.mistral.ai/v1")
            )
            logger.info("V3 Agent OpenAI client initialized successfully")

    async def process_customer_query(self, query: str, customer_info: Dict[str, Any], tenant_id: Optional[str] = None, store_id: Optional[str] = None) -> Dict[str, Any]:
        """
        V3 Orchestration:
        1. RAG Retrieval (Policies, Brand, Product Info) - tenant-specific if tenant_id provided
        2. Sizing Check (if applicable)
        3. Tool Calls (Order/Shipping/Inventory) - REAL TIME
        4. Structured Response Generation
        5. Confidence & Escalation Enforcement
        """
        try:
            # 1. RAG Retrieval - use tenant-specific knowledge base if available
            rag_context = await rag_engine.get_relevant_context(query, tenant_id=tenant_id)
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

            # Resolve brand-specific Shopify credentials (preferred over global env vars)
            _brand_name = "our store"
            _brand_shopify_domain = None
            _brand_shopify_token = None
            _default_store = "00000000-0000-0000-0000-000000000000"
            if store_id and store_id != _default_store:
                try:
                    from src.lib.supabase_client import supabase_select as _sel
                    from src.services.shopify_service import decrypt_token as _dec
                    _b = _sel("brands", {"id": f"eq.{store_id}"})
                    if _b:
                        _brand_name = _b[0].get("name") or _b[0].get("brand_name") or "our store"
                        if _b[0].get("shopify_connected") or _b[0].get("shopify_access_token"):
                            _brand_shopify_domain = _b[0].get("shopify_domain")
                            _raw = _b[0].get("shopify_access_token") or ""
                            _brand_shopify_token = _dec(_raw) if _raw else None
                        logger.info(f"[Agent] Brand found: name={_brand_name}, domain={_brand_shopify_domain}")
                except Exception as _se:
                    logger.warning(f"[Agent] Brand Shopify lookup failed (non-blocking): {_se}")

            # Check for order status inquiry
            if any(kw in query_lower for kw in ["order", "shipped", "tracking", "delivered", "when will", "what did i order"]):
                # Try to extract order number from query
                order_match = re.search(r'#?(\d{3,6})', query)
                if order_match:
                    order_id = order_match.group(1)
                    tool_results["order_status"] = await v3_tools.get_order_status(
                        order_id,
                        shop_domain=_brand_shopify_domain,
                        access_token=_brand_shopify_token,
                    )

                # Check for tracking number in query
                tracking_match = re.search(r'([A-Z]{2}\d{9,10}[A-Z]{0,2})', query.upper())
                if tracking_match:
                    tracking_num = tracking_match.group(1)
                    tool_results["shipping_status"] = await v3_tools.get_shipping_status(tracking_num)

                # Also try to look up by customer email if provided in query
                email_match = re.search(r'[\w.-]+@[\w.-]+\.\w+', query)
                customer_email = None
                if email_match:
                    customer_email = email_match.group(0)
                elif customer_info.get("email"):
                    # Use customer's email from their info
                    customer_email = customer_info.get("email")

                if customer_email:
                    tool_results["orders_by_email"] = await v3_tools.get_orders_by_email(customer_email)

            # Check for inventory/product inquiry
            if any(kw in query_lower for kw in ["in stock", "available", "inventory", "do you have"]):
                # Extract product name
                product_match = re.search(r'(hoodie|jacket|pants|shirt|tshirt|coat|dress|skirt)', query_lower)
                if product_match:
                    product = product_match.group(1)
                    tool_results["inventory"] = await v3_tools.get_inventory_status(product)

            # 4. Build tool context for the AI (explicit Shopify data — AI must use this verbatim)
            tool_context = ""
            if tool_results:
                if "order_status" in tool_results:
                    order = tool_results["order_status"]
                    if order.get("success"):
                        order_block = _build_order_context(order)
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
                        tool_context += f"Customer's orders: {', '.join(order_list)}\n"

                if "shipping_status" in tool_results:
                    tracking = tool_results["shipping_status"]
                    if tracking.get("success"):
                        tool_context += f"Package status: {tracking.get('status')}\n"

                if "inventory" in tool_results:
                    inv = tool_results["inventory"]
                    if inv.get("success"):
                        tool_context += f"{inv.get('message', 'Available')}\n"

            # 4. Return/Exchange Action Layer
            action_context = ""
            if return_actions.should_check_return_eligibility(query):
                logger.info(f"[ReturnActions] Return intent detected for query: {query[:50]}...")
                action_result = await return_actions.handle_return_intent(
                    query=query,
                    customer_info=customer_info,
                    existing_tool_results=tool_results
                )
                action_context = action_result.get("action_context", "")
                logger.info(f"[ReturnActions] Action context result: {action_context[:200] if action_context else 'EMPTY'}...")
                # Store for debugging/logging
                tool_results["return_action"] = action_result
            else:
                logger.info(f"[ReturnActions] No return intent detected")

            # 5. Response Generation
            system_prompt = self._construct_v3_prompt(customer_info, rag_context, sizing_context, tool_context, action_context, brand_name=_brand_name)

            # Defensive check - ensure OpenAI client is initialized
            if not self.openai_client:
                logger.error("OpenAI client is not initialized - API key may be missing")
                return self._get_fallback_response("OpenAI client not initialized")

            try:
                response = self.openai_client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"Customer: {query}"}
                    ],
                    temperature=0.1,
                    response_format={"type": "json_object"}
                )
            except TypeError as e:
                # Mistral API may not support response_format parameter
                logger.warning(f"response_format not supported, retrying without it: {e}")
                response = self.openai_client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"Customer: {query}"}
                    ],
                    temperature=0.1
                )
            except Exception as api_error:
                logger.error(f"Mistral API call failed: {api_error}")
                return self._get_fallback_response(f"API error: {str(api_error)}")

            raw_content = response.choices[0].message.content
            if not raw_content:
                logger.error("Empty response from API")
                return self._get_fallback_response("Empty API response")

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
                return self._get_fallback_response(f"JSON parse error: {str(e)}")

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

            # 6. Signature Enforcement - Make it natural, not robotic
            name = customer_info.get("name", "there").split()[0]
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

            # Only add greeting if not already present and doesn't start with the name
            if reply and not reply.lower().startswith("hi") and not reply.lower().startswith("hey") and not reply.lower().startswith("thanks"):
                structured["reply_body"] = f"Hey {name},\n\n{reply}"
            elif reply and not (name.lower() in reply.lower()[:20]):
                # Name mentioned in first 20 chars, skip greeting
                structured["reply_body"] = f"Hey {name},\n\n{reply}"

            # Add casual sign-off instead of formal
            if "Luna" not in structured["reply_body"]:
                structured["reply_body"] += f"\n\n— Luna\n{_brand_name}"

            return structured

        except Exception as e:
            import traceback
            logger.error(f"V3 Agent Error: {e}")
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return self._get_fallback_response(str(e))

    def _construct_v3_prompt(self, customer_info: Dict[str, Any], rag_context: str, sizing_context: str, tool_context: str = "", action_context: str = "", brand_name: str = "our store") -> str:
        order_critical = (
            "\n⚠ LIVE DATA FROM SHOPIFY — USE ONLY THESE DETAILS:\n"
            "• Reference ONLY the product names, quantities, and totals listed below.\n"
            "• Do NOT invent or assume any product names, prices, or details not listed here.\n"
        ) if tool_context.strip() else "\n(No order data fetched — if the customer asks about an order, ask for their order number.)\n"
        return f"""
        You are Luna, a friendly customer support agent for {brand_name}. You sound like a real person texting a friend - casual, warm, and helpful. NOT a corporate bot.

        RULES:
        - Write like you're texting - short sentences, easy words
        - Never use bullet points or numbered lists
        - Never use words like "algorithm", "system", "deterministic", "variant"
        - Keep messages short - 3-4 sentences max
        - Always sound human and friendly
        - NEVER refer to products not listed in ORDER INFO below
        - NEVER say "let me check", "I'll look into it", "give me a moment", or anything that implies you will do something later — respond fully RIGHT NOW based only on what is in ORDER INFO
        - If ORDER INFO shows a lookup failure, apologize you can't see the order and ask the customer for their order confirmation email or contact details so someone can follow up

        FORMATTING RULES:
        - NEVER use em dashes (—) or en dashes (–) anywhere in your response
        - NEVER use hyphens to join or separate clauses in a sentence
        - Use a comma or start a new sentence instead of a dash
        - WRONG: "I'd love to help—could you share your order number?"
        - RIGHT: "I'd love to help! Could you share your order number?"
        - WRONG: "Your order shipped—check your email for tracking."
        - RIGHT: "Your order has shipped. Check your email for the tracking number."

        KNOWLEDGE BASE:
        {rag_context}

        SIZING:
        {sizing_context}

        ORDER INFO:{order_critical}
        {tool_context}

        RETURN/EXCHANGE STATUS:
        {action_context}

        CUSTOMER:
        Name: {customer_info.get('name')}
        Email: {customer_info.get('email')}
        History: {customer_info.get('history', 'New customer')}

        ACTION RULES (IMPORTANT - DO NOT AUTO-CONFIRM):
        1. For refunds, cancellations, or address changes - NEVER say it's done
        2. Instead say: "I've prepared your request and sent it to our team for confirmation. You'll receive an update shortly!"
        3. NEVER use words like "processed", "approved", "completed", "done"
        4. Always say the request is "being reviewed" or "sent for confirmation"
        5. If not eligible - be honest and offer alternatives

        RESPONSE (JSON only):
        {{
            "intent": "what they want (refund_request|cancellation_request|address_change|order_status_inquiry|shipping_inquiry|sizing_inquiry|product_inquiry|general_inquiry)",
            "sentiment": "positive|neutral|negative",
            "risk_level": "low|medium|high",
            "escalate": false,
            "action_detected": "refund|cancel_order|change_address|none",
            "confidence_score": 80,
            "reply_body": "your friendly response - NEVER confirm actions are done, only say they're being reviewed",
            "suggested_actions": []
        }}

        Include "confidence_score" as an integer 0-100 reflecting how certain you are the reply fully resolves the issue.
        95-100: definitive answer. 80-94: quite sure. 60-79: mostly sure. Below 60: escalate.
        """

    def _get_fallback_response(self, error: str) -> Dict[str, Any]:
        logger.error(f"Using fallback response due to error: {error}")
        return {
            "intent": "general_inquiry",
            "sentiment": "neutral",
            "risk_level": "medium",
            "confidence_score": 40,
            "escalate": True,
            "escalation_reason": f"System error: {error}",
            "reply_body": "Hey there!\n\nThanks for reaching out. I'm having a bit of trouble processing your message right now, but I've flagged this for my team to take a look.\n\nSomeone will get back to you shortly!\n\n— Luna",
            "status": "escalated"
        }

    async def generate_channel_appropriate_response(self, query: str, customer_info: Dict[str, Any], channel: str, tenant_id: Optional[str] = None, store_id: Optional[str] = None) -> Dict[str, Any]:
        return await self.process_customer_query(query, customer_info, tenant_id=tenant_id, store_id=store_id)

customer_success_agent = CustomerSuccessAgent()
