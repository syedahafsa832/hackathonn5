import os
from typing import Dict, Any, Optional
import logging
from openai import OpenAI
from datetime import datetime
import uuid

from ..services.knowledge_base_service import knowledge_base_service
from ..services.database import get_db, db_session
from ..services.customer_service import get_customer_by_id
from ..services.conversation_service import get_conversations_by_customer
from ..services.message_service import get_messages_by_conversation
from ..services.sentiment_analyzer import sentiment_analyzer
from ..services.ticket_feedback_service import search_successful_qa_pairs
from .tools import (
    search_knowledge_base,
    create_ticket,
    get_customer_history,
    escalate_to_human,
    send_response
)

logger = logging.getLogger(__name__)

class CustomerSuccessAgent:
    def __init__(self):
        # Get Mistral API credentials from environment
        api_key = os.getenv("MISTRAL_API_KEY")
        if not api_key:
            logger.error("MISTRAL_API_KEY not set in environment variables")
            raise ValueError("MISTRAL_API_KEY environment variable is required")
        
        self.openai_client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("MISTRAL_API_BASE_URL", "https://api.mistral.ai/v1")
        )
        self.model = os.getenv("MISTRAL_MODEL", "mistral-large-latest")

    async def process_customer_query(self, query: str, customer_id: uuid.UUID, conversation_id: uuid.UUID) -> str:
        """
        Process a customer query and generate an appropriate response
        """
        try:
            # Analyze sentiment of the query
            sentiment_score = sentiment_analyzer.analyze_sentiment(query)

            # Check for escalation triggers
            should_escalate = await self.check_escalation_triggers(query, sentiment_score)

            if should_escalate:
                # Trigger escalation to human agent
                await escalate_to_human(
                    customer_id=customer_id,
                    conversation_id=conversation_id,
                    reason="Escalation trigger detected"
                )

                return "I understand this is an important matter. Let me connect you with a human agent who can assist you further."

            # Search for successful Q&A pairs that match this query (learning from past success)
            successful_qa_context = ""
            async with db_session() as db:
                try:
                    similar_qa_pairs = await search_successful_qa_pairs(db, query, limit=3)

                    if similar_qa_pairs:
                        successful_qa_context = "Previous successful responses to similar questions:\n"
                        for qa in similar_qa_pairs:
                            successful_qa_context += f"Q: {qa.original_question}\n"
                            successful_qa_context += f"A: {qa.ai_response}\n"
                            successful_qa_context += f"Customer Rating: {qa.customer_rating}/5\n"
                            successful_qa_context += "---\n"
                except Exception as e:
                    logger.warning(f"Error searching for successful Q&A pairs: {e}")

            # Search knowledge base for relevant information
            kb_results = await search_knowledge_base(query=query, top_k=3)

            # Get customer history
            customer_info = await get_customer_history(customer_id=customer_id)

            # Construct the system prompt with learning context
            system_prompt = self._construct_system_prompt(customer_info, kb_results, successful_qa_context)

            # Construct the user message
            user_message = self._construct_user_message(query, customer_info)

            # Call OpenAI API
            response = self.openai_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                temperature=0.3,  # Lower temperature for more consistent, professional responses
                max_tokens=1000
            )

            # Extract and return the response
            agent_response = response.choices[0].message.content

            # Log the interaction
            logger.info(f"Processed query for customer {customer_id}. Response length: {len(agent_response)}")

            return agent_response

        except Exception as e:
            logger.error(f"Error processing customer query: {str(e)}")
            return "I apologize, but I'm experiencing technical difficulties. Please try again later or contact support directly."

    def _construct_system_prompt(self, customer_info: Dict[str, Any], kb_results: list, successful_qa_context: str = "") -> str:
        """
        Construct the system prompt with customer context, knowledge base results, and successful Q&A pairs
        """
        system_prompt = f"""
        You are a helpful Customer Success AI assistant.

        Customer Context:
        {customer_info}

        Previous successful responses to similar questions:
        {successful_qa_context if successful_qa_context else "No previous successful responses found for similar questions."}

        Knowledge Base:
        {kb_results}

        Guidelines:
        1. Be professional, empathetic, and helpful
        2. Provide accurate information based on the knowledge base
        3. If similar questions have been successfully answered before, learn from those responses but adapt to this specific customer's question
        4. Keep responses concise and clear
        5. If unsure, acknowledge limitations
        6. Never discuss pricing, legal matters, or refunds - escalate instead
        7. Do not share internal system details

        Respond naturally to help the customer with their question.
        """
        return system_prompt

    def _construct_user_message(self, query: str, customer_info: Dict[str, Any]) -> str:
        """
        Construct the user message with query and context
        """
        return f"""
        Customer Query: {query}

        Please provide a helpful and accurate response based on the knowledge base and customer context provided in the system message.
        """

    async def check_escalation_triggers(self, query: str, sentiment_score: float) -> bool:
        """
        Check if the query should be escalated based on triggers
        """
        # Keywords that trigger escalation
        escalation_keywords = [
            'pricing', 'price', 'cost', 'payment', 'refund', 'legal',
            'lawyer', 'complaint', 'manager', 'supervisor', 'ceo', 'executive',
            'lawsuit', 'contract', 'agreement', 'billing dispute', 'chargeback'
        ]

        # Check for escalation keywords
        query_lower = query.lower()
        for keyword in escalation_keywords:
            if keyword in query_lower:
                logger.info(f"Escalation triggered by keyword: {keyword}")
                return True

        # Check sentiment - if very negative, escalate
        if sentiment_score < -0.5:  # Very negative sentiment
            logger.info(f"Escalation triggered by negative sentiment: {sentiment_score}")
            return True

        # Check for profanity or aggressive language
        if self._contains_profanity(query):
            logger.info("Escalation triggered by potentially inappropriate language")
            return True

        return False

    def _contains_profanity(self, text: str) -> bool:
        """
        Simple profanity check - in production, use a more robust solution
        """
        profanity_list = [
            'fuck', 'shit', 'damn', 'bitch', 'asshole', 'bastard', 'cunt',
            'dick', 'piss', 'damn', 'bloody', 'bollocks', 'arsehole'
        ]

        text_lower = text.lower()
        for word in profanity_list:
            if word in text_lower:
                return True

        return False

    async def generate_channel_appropriate_response(self, query: str, customer_id: uuid.UUID,
                                                 conversation_id: uuid.UUID, channel: str) -> str:
        """
        Generate a response appropriate for the specific channel
        """
        base_response = await self.process_customer_query(query, customer_id, conversation_id)

        if channel == "whatsapp":
            # Format for WhatsApp - concise, conversational. Under 300 characters.
            formatted_response = base_response[:300] if len(base_response) > 300 else base_response
        elif channel == "email":
            # Return raw response - Gmail handler will format it with proper email template
            formatted_response = base_response
        else:  # web_form
            # Default format for web form - semi-formal, helpful.
            formatted_response = base_response

        return formatted_response

# Global instance
customer_success_agent = CustomerSuccessAgent()

# Convenience function for external use
async def process_customer_query(query: str, customer_id: uuid.UUID, conversation_id: uuid.UUID) -> str:
    """
    Process a customer query and return the response
    """
    return await customer_success_agent.process_customer_query(query, customer_id, conversation_id)
