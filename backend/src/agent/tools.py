import os
from typing import Dict, Any, List
import uuid
from datetime import datetime
import logging

from ..services.database import get_db, db_session
from ..services.knowledge_base_service import knowledge_base_service
from ..services.customer_service import get_customer_by_id
from ..services.conversation_service import get_conversations_by_customer
from ..services.message_service import get_messages_by_conversation
from ..services.ticket_service import create_ticket as create_ticket_service
# Kafka service removed for direct flow

logger = logging.getLogger(__name__)

async def search_knowledge_base(query: str, top_k: int = 3) -> List[Dict[str, Any]]:
    """
    Search the knowledge base for relevant articles using vector similarity
    """
    try:
        async with db_session() as db:
            articles = await knowledge_base_service.search_similar(
                db=db,
                query=query,
                top_k=top_k
            )

            # Format results
            results = []
            for article in articles:
                results.append({
                    "title": article.title,
                    "content": article.content[:500] + "..." if len(article.content) > 500 else article.content,
                    "category": article.category,
                    "similarity_score": 0.9  # Placeholder - actual implementation would use vector distance
                })

            return results

    except Exception as e:
        logger.error(f"Error searching knowledge base: {str(e)}")
        return []


async def create_ticket(customer_id: str, source_channel: str, subject: str,
                      category: str, priority: str, description: str) -> Dict[str, Any]:
    """
    Create a new support ticket
    """
    try:
        async with db_session() as db:
            ticket = await create_ticket_service(
                db=db,
                customer_id=uuid.UUID(customer_id),
                source_channel=source_channel,
                subject=subject,
                category=category,
                priority=priority,
                description=description
            )

            # Kafka integration removed for direct internal flow
            logger.info(f"Ticket {ticket.id} created and will be handled via direct internal flow")

            return {
                "id": str(ticket.id),
                "status": "created",
                "message": f"Ticket {str(ticket.id)} created successfully"
            }

    except Exception as e:
        logger.error(f"Error creating ticket: {str(e)}")
        return {
            "error": "Failed to create ticket",
            "message": str(e)
        }


async def get_customer_history(customer_id: str) -> Dict[str, Any]:
    """
    Get customer history including past conversations and tickets
    """
    try:
        # Handle both string and UUID object inputs
        if isinstance(customer_id, uuid.UUID):
            customer_uuid = customer_id
        else:
            customer_uuid = uuid.UUID(customer_id)

        async with db_session() as db:
            # Get customer details
            customer = await get_customer_by_id(db, customer_uuid)

            if not customer:
                return {"error": f"Customer with ID {customer_id} not found"}

            # Get customer's conversations
            conversations = await get_conversations_by_customer(db, customer_uuid)

            # Build history
            history = {
                "customer_id": str(customer.id),
                "email": customer.email,
                "name": customer.name,
                "company": customer.company,
                "created_at": customer.created_at.isoformat() if customer.created_at else None,
                "total_conversations": len(conversations),
                "conversations": []
            }

            # Get messages for each conversation
            for conv in conversations:
                messages = await get_messages_by_conversation(db, conv.id)

                conversation_summary = {
                    "id": str(conv.id),
                    "initial_channel": conv.initial_channel,
                    "status": conv.status,
                    "created_at": conv.created_at.isoformat() if conv.created_at else None,
                    "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
                    "message_count": len(messages),
                    "recent_messages": [
                        {
                            "direction": msg.direction,
                            "content_preview": msg.content[:100] + "..." if len(msg.content) > 100 else msg.content,
                            "created_at": msg.created_at.isoformat() if msg.created_at else None,
                            "sentiment_score": float(msg.sentiment_score) if msg.sentiment_score else None
                        }
                        for msg in messages[-3:]  # Last 3 messages
                    ]
                }
                history["conversations"].append(conversation_summary)

            return history

    except Exception as e:
        logger.error(f"Error getting customer history: {str(e)}")
        return {"error": f"Failed to retrieve customer history: {str(e)}"}


async def escalate_to_human(customer_id: str, conversation_id: str, reason: str) -> Dict[str, Any]:
    """
    Escalate a conversation to a human agent
    """
    try:
        customer_uuid = uuid.UUID(customer_id)
        conversation_uuid = uuid.UUID(conversation_id)

        async with db_session() as db:
            # Import here to avoid circular imports
            from ..services.ticket_service import escalate_ticket
            from ..services.conversation_service import escalate_conversation

            # Escalate any associated tickets
            # In a real implementation, we would find tickets associated with this conversation
            # For now, we'll just publish an escalation event

            # Escalate the conversation itself
            await escalate_conversation(db, conversation_uuid)

            # Kafka escalation logic removed for direct flow
            logger.info(f"Escalating customer {str(customer_id)} in conversation {str(conversation_id)} for reason: {reason}")

            return {
                "status": "escalated",
                "customer_id": customer_id,
                "conversation_id": conversation_id,
                "reason": reason,
                "message": "Issue escalated to human agent"
            }

    except Exception as e:
        logger.error(f"Error escalating to human: {str(e)}")
        return {
            "error": "Failed to escalate to human agent",
            "message": str(e)
        }


async def send_response(response_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Send a response to the appropriate channel
    """
    try:
        # This function coordinates sending responses to different channels
        # It uses the channel handlers (whatsapp_handler, etc.)

        channel = response_data.get("channel", "web_form")
        recipient = response_data.get("recipient")
        content = response_data.get("content", "")

        # In a real implementation, we would route to the appropriate channel handler
        # For now, we'll just log the response

        logger.info(f"Response prepared for {channel} to {recipient}: {content[:100]}...")

        # Kafka response logic removed for direct flow
        # In a monolith, responses are typically handled by the calling service (UnifiedMessageProcessor)
        logger.info(f"Agent tool 'send_response' prepared response for {channel} to {recipient}")

        return {
            "status": "response_prepared",
            "channel": channel,
            "recipient": recipient,
            "message": "Response prepared for sending"
        }

    except Exception as e:
        logger.error(f"Error preparing response: {str(e)}")
        return {
            "error": "Failed to prepare response",
            "message": str(e)
        }


# Additional helper functions that might be useful for the agent

async def get_channel_specific_formatting(channel: str, content: str) -> str:
    """
    Apply channel-specific formatting to content
    """
    if channel == "whatsapp":
        # WhatsApp formatting, max 300 characters
        if len(content) > 300:
            return content[:297] + "..."
        return content
    else:
        # Default formatting for web form
        return content


async def detect_sentiment(text: str) -> float:
    """
    Detect sentiment in text
    """
    from ..services.sentiment_analyzer import sentiment_analyzer

    return sentiment_analyzer.analyze_sentiment(text)
