import os
import json
from typing import Dict, Any, Optional
import logging
from openai import OpenAI
from datetime import datetime
import uuid

from ..services.knowledge_base_service import knowledge_base_service
# from ..services.database import get_db, db_session # Removed
# from ..services.customer_service import get_customer_by_id # Removed
# from ..services.conversation_service import get_conversations_by_customer # Removed
# from ..services.message_service import get_messages_by_conversation # Removed
from ..services.sentiment_analyzer import sentiment_analyzer
# from ..services.ticket_feedback_service import search_successful_qa_pairs # Removed

# Temporary placeholders for tools that need refactoring
async def search_knowledge_base(query: str, top_k: int = 3):
    return "Knowledge base results would appear here."

async def get_customer_history(customer_id: uuid.UUID):
    return {"id": str(customer_id), "name": "Unknown"}

logger = logging.getLogger(__name__)

class CustomerSuccessAgent:
    def __init__(self):
        api_key = os.getenv("MISTRAL_API_KEY")
        if not api_key:
            logger.error("MISTRAL_API_KEY not set in environment variables")
            raise ValueError("MISTRAL_API_KEY environment variable is required")
        
        self.openai_client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("MISTRAL_API_BASE_URL", "https://api.mistral.ai/v1")
        )
        self.model = os.getenv("MISTRAL_MODEL", "mistral-large-latest")

    async def process_customer_query(self, query: str, customer_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a customer query and generate a structured JSON response
        """
        try:
            # Search knowledge base for relevant information
            kb_results = await search_knowledge_base(query=query, top_k=3)

            # Construct the system prompt with requirements for structured JSON
            system_prompt = self._construct_system_prompt(customer_info, kb_results)

            # Construct the user message
            user_message = f"Customer Query: {query}"

            # Call AI API
            response = self.openai_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                temperature=0.1,  # Lower temperature for strict JSON compliance
                response_format={"type": "json_object"} if "mistral" not in self.model.lower() else None # Mistral supports JSON via prompting
            )

            # Extract and parse the response
            raw_content = response.choices[0].message.content
            try:
                structured_response = json.loads(raw_content)
                
                # Validation & Default values
                required_fields = ['intent', 'sentiment', 'risk_level', 'confidence_score', 'escalate', 'escalation_reason', 'reply_subject', 'reply_body']
                for field in required_fields:
                    if field not in structured_response:
                        structured_response[field] = None # Or provide defaults
                
                # Apply escalation logic (Step 5)
                if (structured_response.get('risk_level') == 'high' or 
                    structured_response.get('confidence_score', 0) < 75 or 
                    structured_response.get('escalate') is True):
                    structured_response['status'] = 'escalated'
                else:
                    structured_response['status'] = 'auto_resolved'

                # Clean up formatting (Step 8)
                name = customer_info.get('name', 'Customer')
                body_content = structured_response.get('reply_body', '')
                if not body_content.startswith(f"Hi {name}"):
                    structured_response['reply_body'] = f"Hi {name},\n\n{body_content}"

                return structured_response

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse AI JSON response: {raw_content}. Error: {e}")
                return self._get_fallback_escalation_response("AI response parsing failed")

        except Exception as e:
            logger.error(f"Error processing customer query: {str(e)}")
            return self._get_fallback_escalation_response(str(e))

    def _construct_system_prompt(self, customer_info: Dict[str, Any], kb_results: Any) -> str:
        return f"""
        You are a senior Customer Success AI assistant. You MUST respond ONLY with a structured JSON object.

        Customer Name: {customer_info.get('name', 'Unknown')}
        Customer Context: {customer_info}
        Knowledge Base Info: {kb_results}

        REQUIREMENTS:
        1. Start the reply with "Hi [Name],"
        2. Address the customer's specific message accurately.
        3. Be professional and concise. No emoji spam.
        4. No generic repetitive greetings like "Dear Valued Customer".
        5. If the issue is complex or outside the knowledge base, set escalate to true.

        JSON SCHEMA:
        {{
            "intent": "string (e.g., technical_support, billing, sales)",
            "sentiment": "string (positive, neutral, negative)",
            "risk_level": "string (low, medium, high)",
            "confidence_score": 0-100,
            "escalate": boolean,
            "escalation_reason": "string or null",
            "reply_subject": "Professional email subject line",
            "reply_body": "Detailed and personalized response body"
        }}
        """

    def _get_fallback_escalation_response(self, error_msg: str) -> Dict[str, Any]:
        return {
            "intent": "error_fallback",
            "sentiment": "neutral",
            "risk_level": "high",
            "confidence_score": 0,
            "escalate": True,
            "escalation_reason": f"System Error: {error_msg}",
            "reply_subject": "Re: Your Support Request",
            "reply_body": "I apologize, but I'm having trouble processing your request. One of our human agents will look into this immediately.",
            "status": "escalated"
        }

    async def generate_channel_appropriate_response(self, query: str, customer_info: Dict[str, Any], channel: str) -> Dict[str, Any]:
        """
        Generate a response appropriate for the specific channel, returning structured data
        """
        return await self.process_customer_query(query, customer_info)

# Global instance
customer_success_agent = CustomerSuccessAgent()
