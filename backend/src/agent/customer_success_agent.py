import os
import json
import re
import logging
from typing import Dict, Any, Optional, List
from openai import OpenAI

from ..services.rag_engine import rag_engine
from ..services.sentiment_analyzer import sentiment_analyzer
from ..services.size_engine import size_engine
from ..services.tools import v3_tools
from ..lib.supabase_client import supabase_rpc, supabase_update

logger = logging.getLogger(__name__)

class CustomerSuccessAgent:
    """
    V3 Customer Success Agent (Luna) for Aurelio & Finch.
    Uses pgvector RAG, deterministic sizing, and live Shopify/AfterShip tools.
    """

    def __init__(self):
        self.model = os.getenv("MISTRAL_MODEL", "mistral-large-latest")
        api_key = os.getenv("MISTRAL_API_KEY")
        if not api_key:
            raise ValueError("MISTRAL_API_KEY is required for V3 Agent")
            
        self.openai_client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("MISTRAL_API_BASE_URL", "https://api.mistral.ai/v1")
        )

    async def process_customer_query(self, query: str, customer_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        V3 Orchestration:
        1. RAG Retrieval (Policies, Brand, Product Info)
        2. Sizing Check (if applicable)
        3. Tool Calls (Order/Shipping/Inventory) - REAL TIME
        4. Structured Response Generation
        5. Confidence & Escalation Enforcement
        """
        try:
            # 1. RAG Retrieval
            rag_context = await rag_engine.get_relevant_context(query)

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

            # Check for order status inquiry
            if any(kw in query_lower for kw in ["order", "shipped", "tracking", "delivered", "when will", "what did i order"]):
                # Try to extract order number from query
                order_match = re.search(r'#?(\d{3,6})', query)
                if order_match:
                    order_id = order_match.group(1)
                    tool_results["order_status"] = await v3_tools.get_order_status(order_id)

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

            # 4. Build tool context for the AI (natural language, not raw JSON)
            tool_context = ""
            if tool_results:
                if "order_status" in tool_results:
                    order = tool_results["order_status"]
                    if order.get("success"):
                        tool_context += f"\n- Their order #{order.get('order_number')} is {order.get('status')}"
                        if order.get("items"):
                            items = ", ".join([i.get("title", "item") for i in order.get("items", [])])
                            tool_context += f" with {items}"
                        if order.get("tracking_number"):
                            tool_context += f". Tracking: {order.get('tracking_number')}"

                if "orders_by_email" in tool_results:
                    orders = tool_results["orders_by_email"]
                    if orders.get("success") and orders.get("orders"):
                        order_list = [f"#{o.get('order_number')} ({o.get('status')})" for o in orders.get("orders", [])]
                        tool_context += f"\n- Their orders: {', '.join(order_list)}"

                if "shipping_status" in tool_results:
                    tracking = tool_results["shipping_status"]
                    if tracking.get("success"):
                        tool_context += f"\n- Package status: {tracking.get('status')}"

                if "inventory" in tool_results:
                    inv = tool_results["inventory"]
                    if inv.get("success"):
                        tool_context += f"\n- {inv.get('message', 'Available')}"

            # 5. Response Generation
            system_prompt = self._construct_v3_prompt(customer_info, rag_context, sizing_context, tool_context)
            
            response = self.openai_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Customer: {query}"}
                ],
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            
            raw_content = response.choices[0].message.content
            structured = json.loads(raw_content)

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

            # Only add greeting if not already present and doesn't start with the name
            if reply and not reply.lower().startswith("hi") and not reply.lower().startswith("hey") and not reply.lower().startswith("thanks"):
                structured["reply_body"] = f"Hey {name},\n\n{reply}"
            elif reply and not (name.lower() in reply.lower()[:20]):
                # Name mentioned in first 20 chars, skip greeting
                structured["reply_body"] = f"Hey {name},\n\n{reply}"

            # Add casual sign-off instead of formal
            if "Luna" not in structured["reply_body"]:
                structured["reply_body"] += "\n\n— Luna\nAurelio & Finch"

            return structured

        except Exception as e:
            logger.error(f"V3 Agent Error: {e}")
            return self._get_fallback_response(str(e))

    def _construct_v3_prompt(self, customer_info: Dict[str, Any], rag_context: str, sizing_context: str, tool_context: str = "") -> str:
        return f"""
        You are Luna, the Senior Brand Stylist for Aurelio & Finch—a luxury streetwear house. You are NOT "Customer Success" or a "Support Bot." You are a human-like expert who helps clients build their wardrobes.

        TONE & VOICE:
        - Confident & Expert: Use phrases like "I recommend," "In my experience," and "We've designed this for..."
        - Concise & Fluid: NEVER use bullet points, numbered lists, or "1. 2. 3." logic. Write in natural, flowing paragraphs as if texting or emailing a VIP client.
        - No Jargon: Never use technical terms like "deterministic," "variant," "sizing engine," "inventory," "algorithm," or "system."
        - Style-First: Focus on the look and feel of the garment, not measurements or technical details.

        INTERACTION RULES:
        - Acknowledge the Fear: If a customer mentions a concern (like "looking sloppy," "too big," or "fading"), address it immediately with empathy.
        - The "We" Mentality: Always speak as part of the brand. "We designed this for..." instead of "The product has..."
        - The Soft Close: Always end with a helpful question that moves toward a sale, like "Should I check if we still have your size in the black?" or "Which piece caught your eye?"

        FORMATTING:
        - Use *italics* sparingly for emphasis on key style points
        - Use **bold** only for specific size recommendations when truly warranted
        - Keep it conversational—imagine you're texting a friend who knows fashion

        KNOWLEDGE BASE:
        {rag_context}

        SIZING GUIDANCE:
        {sizing_context}

        LIVE ORDER/SHIPPING DATA:
        {tool_context}

        CUSTOMER:
        - Name: {customer_info.get('name')}
        - History: {customer_info.get('history', 'New Client')}

        OPERATIONAL RULES:
        1. USE LIVE DATA: When you have real order/tracking data, use it naturally in conversation.
        2. If order shows items, mention them casually: "I see your order included the Premium Hoodie—that's a great pick."
        3. If tracking shows status, tell them naturally: "Your package is out for delivery today."
        4. For sizing questions, give a confident recommendation with warmth. If you need their measurements, ask naturally: "What height and weight should I work with for you?"
        5. ESCALATE only if they're frustrated or you've tried twice and can't help.

        OUTPUT JSON ONLY:
        {{
            "intent": "string",
            "sentiment": "positive|neutral|negative",
            "risk_level": "low|medium|high",
            "escalate": boolean,
            "reply_body": "string",
            "suggested_actions": ["check_inventory", "get_order_status", "escalate"]
        }}
        """

    def _get_fallback_response(self, error: str) -> Dict[str, Any]:
        return {
            "intent": "system_error",
            "sentiment": "neutral",
            "risk_level": "high",
            "confidence_score": 0,
            "escalate": True,
            "reply_body": "I am currently experiencing a synchronization delay with our luxury logistics network. I've escalated your request to our senior concierge team.",
            "status": "escalated"
        }

    async def generate_channel_appropriate_response(self, query: str, customer_info: Dict[str, Any], channel: str) -> Dict[str, Any]:
        return await self.process_customer_query(query, customer_info)

customer_success_agent = CustomerSuccessAgent()
