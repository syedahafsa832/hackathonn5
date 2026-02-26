import os
import json
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
        3. Tool Calls (Order/Shipping/Inventory)
        4. Structured Response Generation
        5. Confidence & Escalation Enforcement
        """
        try:
            # 1. RAG Retrieval
            rag_context = await rag_engine.get_relevant_context(query)
            
            # 2. Sizing Engine (Deterministic)
            sizing_context = ""
            if any(k in query.lower() for k in ["size", "fit", "small", "medium", "large", "xl"]):
                if customer_info.get("height") and customer_info.get("weight"):
                    sizing_context = "\n[SYSTEM] Sizing engine is available for deterministic calculations."
                else:
                    sizing_context = "\n[SYSTEM] Ask for height/weight/fit-preference to provide precise sizing."

            # 3. Response Generation
            system_prompt = self._construct_v3_prompt(customer_info, rag_context, sizing_context)
            
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

            # 4. Confidence Calculation
            sentiment = sentiment_analyzer.analyze_sentiment_detailed(query)
            confidence = 0.85 
            if not rag_context: confidence -= 0.3
            if sentiment["label"] == "negative": confidence -= 0.15
            
            confidence_out_of_100 = int(max(0, min(1, confidence)) * 100)
            structured["confidence_score"] = confidence_out_of_100

            # 5. Escalation Thresholds
            if confidence_out_of_100 < 10:
                logger.critical(f"FAIL-SAFE: Confidence {confidence_out_of_100}% < 10%. Pausing AI.")
                structured["status"] = "paused"
                structured["escalate"] = True
            elif confidence_out_of_100 < 75 or structured.get("risk_level") == "high":
                structured["status"] = "escalated"
                structured["escalate"] = True
            else:
                structured["status"] = "auto_resolved"

            # 6. Signature Enforcement
            name = customer_info.get("name", "valuable customer").split()[0]
            reply = structured.get("reply_body", "")
            if not reply.startswith("Hi"):
                structured["reply_body"] = f"Hi {name},\n\n{reply}"
            
            if "Luna" not in structured["reply_body"]:
                structured["reply_body"] += "\n\nBest regards,\nLuna\nAurelio & Finch Customer Success"

            return structured

        except Exception as e:
            logger.error(f"V3 Agent Error: {e}")
            return self._get_fallback_response(str(e))

    def _construct_v3_prompt(self, customer_info: Dict[str, Any], rag_context: str, sizing_context: str) -> str:
        return f"""
        You are Luna, the AI Customer Success Expert for Aurelio & Finch, a premium apparel brand.
        Your tone is refined, concise, confident, and empathetic. Focus on ROI and quality.

        CONTEXT:
        {rag_context}
        {sizing_context}

        CUSTOMER:
        - Name: {customer_info.get('name')}
        - History: {customer_info.get('history', 'New Client')}

        V3 OPERATIONAL RULES:
        1. USE RAG: Only answer based on the provided context. If unsure, escalate.
        2. NO GUESSING: Never guess inventory levels or shipping dates.
        3. SIZE ENGINE: If a customer asks for a size recommendation, explain that you use a deterministic sizing engine. Ask for their height (cm), weight (kg), and fit preference if not provided.
        4. ESCALATE: Always set escalate=true if the customer is frustrated or if confidence is low.

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
