import os
import json
from typing import Dict, Any, Optional
import logging
from openai import OpenAI
from datetime import datetime
import uuid

from ..services.sentiment_analyzer import sentiment_analyzer


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
        
        # Load Knowledge Base
        self.kb_data = self._load_knowledge_base()

    def _load_knowledge_base(self) -> Dict[str, Any]:
        """Load company and boss info from knowledge_base.json."""
        try:
            kb_path = os.path.join(os.getcwd(), "backend", "knowledge_base.json")
            if not os.path.exists(kb_path):
                # Fallback for different CWDs
                kb_path = os.path.join(os.getcwd(), "knowledge_base.json")
            
            if os.path.exists(kb_path):
                with open(kb_path, "r") as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"Failed to load knowledge base: {e}")
            return {}

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
            api_kwargs = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                "temperature": 0.1,
            }
            
            # Use JSON mode if supported
            if "mistral" in self.model.lower() or "gpt" in self.model.lower():
                api_kwargs["response_format"] = {"type": "json_object"}

            response = self.openai_client.chat.completions.create(**api_kwargs)

            # Extract and parse the response
            raw_content = response.choices[0].message.content
            try:
                structured_response = json.loads(raw_content)
                
                # Validation & Default values
                required_fields = ['intent', 'sentiment', 'risk_level', 'confidence_score', 'escalate', 'escalation_reason', 'reply_subject', 'reply_body']
                for field in required_fields:
                    if field not in structured_response:
                        structured_response[field] = None # Or provide defaults
                
                # Apply escalation logic
                if (structured_response.get('risk_level') == 'high' or 
                    structured_response.get('confidence_score', 0) < 75 or 
                    structured_response.get('escalate') is True):
                    structured_response['status'] = 'escalated'
                else:
                    structured_response['status'] = 'auto_resolved'

                # Clean up formatting (Step 8) - Prevent double greetings
                name = customer_info.get('name', 'Customer')
                first_name = name.split()[0] if name else "Customer"
                body_content = structured_response.get('reply_body', '')
                
                # If the body already starts with a greeting, don't add another one
                if not any(body_content.lower().startswith(g) for g in ['hi ', 'hello ', 'dear ']):
                    structured_response['reply_body'] = f"Hi {first_name},\n\n{body_content}"

                return structured_response

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse AI JSON response: {raw_content}. Error: {e}")
                return self._get_fallback_escalation_response("AI response parsing failed")

        except Exception as e:
            logger.error(f"Error processing customer query: {str(e)}")
            return self._get_fallback_escalation_response(str(e))

    def _construct_system_prompt(self, customer_info: Dict[str, Any], kb_results: Any) -> str:
        kb_str = json.dumps(self.kb_data, indent=2)
        return f"""
        You are Luna, the Customer Success AI assistant for Syeda Hafsa.
        Syeda Hafsa is your boss. She is a N8N Certified Developer and Agentic AI expert.

        YOUR PERSONA:
        - Name: Luna
        - Role: Professional and intelligent assistant to Syeda Hafsa.
        - Tone: Professional, helpful, concise, and focused on automation ROI.

        BOSS BACKGROUND & KNOWLEDGE:
        {kb_str}

        PRIVACY & DATA RULES:
        1. DO NOT dump all details about Syeda Hafsa to everyone.
        2. Only provide specific project details or info if the user explicitly asks about them.
        3. If a user asks who you are or who Syeda Hafsa is, give a professional introduction based on the knowledge above.
        4. Focus on how Syeda Hafsa's AI systems can help the customer's specific problem (e.g., manual work reduction).

        CUSTOMER DATA:
        - Name: {customer_info.get('name', 'Unknown')}
        - Context: {customer_info}
        - Search Results: {kb_results}

        REQUIREMENTS:
        1. Start the reply with "Hi [First Name],"
        2. Address the customer's specific message accurately using the knowledge base.
        3. Sign the email as "Luna" from the Customer Success Team.
        4. If the issue is complex or outside the knowledge base, set escalate to true.

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
            "reply_body": "I apologize, but I'm having trouble processing your request. I have escalated this to my team, and one of us will look into this immediately.\n\nBest regards,\nLuna",
            "status": "escalated"
        }

    async def generate_channel_appropriate_response(self, query: str, customer_info: Dict[str, Any], channel: str) -> Dict[str, Any]:
        """
        Generate a response appropriate for the specific channel, returning structured data
        """
        return await self.process_customer_query(query, customer_info)

# Global instance
customer_success_agent = CustomerSuccessAgent()
