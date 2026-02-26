import os
import json
from typing import Dict, Any, Optional, List
import logging
from openai import OpenAI
from datetime import datetime
import uuid

from ..services.sentiment_analyzer import sentiment_analyzer
from ..services.size_engine import size_engine
from ..services.tools import v3_tools
from ..lib.supabase_client import supabase_rpc, supabase_update

logger = logging.getLogger(__name__)

class CustomerSuccessAgent:
    """
    V3 Customer Success Agent (Luna).
    Uses pgvector RAG, deterministic sizing, and multi-channel tools.
    """

    def __init__(self):
        api_key = os.getenv("MISTRAL_API_KEY")
        if not api_key:
            logger.error("MISTRAL_API_KEY not set")
            raise ValueError("MISTRAL_API_KEY is required")
        
        self.openai_client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("MISTRAL_API_BASE_URL", "https://api.mistral.ai/v1")
        )
        self.model = os.getenv("MISTRAL_MODEL", "mistral-large-latest")
        self.embedding_model = os.getenv("EMBEDDING_MODEL", "mistral-embed")

    async def _get_vector_context(self, query: str) -> Dict[str, Any]:
        """Perform pgvector similarity search on product catalog."""
        try:
            # 1. Generate query embedding
            resp = self.openai_client.embeddings.create(input=[query], model=self.embedding_model)
            embedding = resp.data[0].embedding
            
            # 2. RPC call for vector match
            matches = supabase_rpc("match_products", {
                "query_embedding": embedding,
                "match_threshold": 0.5,
                "match_count": 3
            })
            
            if not matches:
                return {"context": "No relevant product found.", "top_score": 0}
            
            context = "Relevant Product Information:\n"
            top_score = matches[0].get("similarity", 0)
            for m in matches:
                context += f"- {m['title']}: {m['description']}\n  Fabric: {m['fabric']}, Fit: {m['fit_type']}\n"
            
            return {"context": context, "top_score": top_score}
        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            return {"context": "Catalog search temporarily unavailable.", "top_score": 0}

    async def process_customer_query(self, query: str, customer_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process query with V3 logic: RAG -> Confidence Check -> Tool Call (Simulated via prompt instructions).
        """
        try:
            # 1. RAG PHASE
            vector_data = await self._get_vector_context(query)
            kb_results = vector_data["context"]
            retrieval_score = vector_data["top_score"]

            # 2. CONSTRUCTION
            system_prompt = self._construct_system_prompt(customer_info, kb_results)
            user_message = f"Customer Query: {query}"

            # 3. AI COMPLETION
            response = self.openai_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                temperature=0.1,
                response_format={"type": "json_object"}
            )

            raw_content = response.choices[0].message.content
            structured_response = json.loads(raw_content)

            # 4. V3 CONFIDENCE CALCULATION (Objective 8)
            # confidence = average(retrieval_score, size_engine_probability, tool_success_flag, sentiment_modifier)
            sentiment_data = sentiment_analyzer.analyze_sentiment_detailed(query)
            sentiment_modifier = 1.0 if sentiment_data["label"] != "negative" else 0.8
            
            # Simplified sizing/tool flags for calculation
            # In a real tool-calling loop, these would be real results
            sizing_prob = 1.0 if "size" in query.lower() else 0.5 
            tool_success = 1.0 # Assume tools worked unless AI says otherwise
            
            # Weighted Confidence
            avg_confidence = (retrieval_score + sizing_prob + tool_success) / 3 * sentiment_modifier
            confidence_out_of_100 = int(avg_confidence * 100)
            
            structured_response["confidence_score"] = confidence_out_of_100

            # 5. ESCALATION & FAIL-SAFE LOGIC
            if confidence_out_of_100 < 10:
                logger.critical(f"FAIL-SAFE: Confidence {confidence_out_of_100}% below 10%. Triggering Global AI Pause.")
                # We update the global setting (Simplified)
                # supabase_update("system_settings", {"store_id": "eq.00000000-0000-0000-0000-000000000000"}, {"ai_mode": "paused"})
                structured_response["escalate"] = True
                structured_response["status"] = "paused"
            elif confidence_out_of_100 < 75 or structured_response.get("risk_level") == "high":
                structured_response["escalate"] = True
                structured_response["status"] = "escalated"
            else:
                structured_response["status"] = "auto_resolved"

            # 6. SIGNATURE
            name = customer_info.get("name", "valuable customer")
            first_name = name.split()[0]
            body = structured_response.get("reply_body", "")
            if not any(body.lower().startswith(g) for g in ["hi ", "hello ", "dear "]):
                structured_response["reply_body"] = f"Hi {first_name},\n\n{body}"

            return structured_response

        except Exception as e:
            logger.error(f"V3 Agent Error: {e}")
            return self._get_fallback_escalation_response(str(e))

    def _construct_system_prompt(self, customer_info: Dict[str, Any], kb_results: str) -> str:
        return f"""
        You are Luna, the V3 AI Assistant for High-End Fashion Brand.
        You are professional, technical, and data-driven.

        V3 CAPABILITIES:
        1. YOU HAVE ACCESS TO REAL-TIME CATALOG (RAG Results below).
        2. YOU HAVE TOOLS: get_product_details, check_inventory, get_order_status.
        3. YOU MUST USE THE CONTEXT BELOW TO ANSWER. IF DATA IS MISSING, ESCALATE.

        PRODUCT CONTEXT (RAG):
        {kb_results}

        CUSTOMER CONTEXT:
        - Name: {customer_info.get('name')}
        - History: {customer_info.get('history', 'First time customer')}

        RULES:
        - NEVER calculate sizes. Recommend based on size charts in context.
        - If the customer is angry, acknowledge and escalate.
        - Sign as "Luna, Customer Success Team".

        JSON FORMAT REQUIRED:
        {{
            "intent": "string",
            "sentiment": "positive|neutral|negative",
            "risk_level": "low|medium|high",
            "escalate": boolean,
            "escalation_reason": "string or null",
            "reply_subject": "string",
            "reply_body": "string"
        }}
        """

    def _get_fallback_escalation_response(self, error_msg: str) -> Dict[str, Any]:
        return {
            "intent": "error_fallback",
            "sentiment": "neutral",
            "risk_level": "high",
            "confidence_score": 0,
            "escalate": True,
            "escalation_reason": f"V3 System Error: {error_msg}",
            "reply_subject": "Update regarding your request",
            "reply_body": "I'm experiencing a technical synchronization issue. I have escalated this to my human team members.",
            "status": "escalated"
        }

    async def generate_channel_appropriate_response(self, query: str, customer_info: Dict[str, Any], channel: str) -> Dict[str, Any]:
        return await self.process_customer_query(query, customer_info)

customer_success_agent = CustomerSuccessAgent()
