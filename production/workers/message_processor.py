#!/usr/bin/env python3
"""
Centralized Message Processor for Customer Success AI Agent

This service processes incoming messages from all channels through the FTE agent.
It handles customer identification across channels, conversation management,
and orchestrates the response generation process.
"""

import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone
import uuid

from src.services.database import get_db, db_session
from src.services.whatsapp_handler import WhatsAppHandler
from src.services.message_service import create_message, get_messages_by_conversation
from src.services.customer_service import get_customer_by_identifier, get_or_create_customer, get_customer_by_id
from src.services.conversation_service import (
    get_conversations_by_customer, 
    create_conversation, 
    get_conversation_by_id,
    update_conversation_status
)
from src.agent.customer_success_agent import customer_success_agent



logger = logging.getLogger(__name__)


class UnifiedMessageProcessor:
    """Process incoming messages from all channels through the FTE agent directly without Kafka."""

    def __init__(self):
        # Initialize channel handlers and services
        self.whatsapp_handler = WhatsAppHandler()
        self.running = False

    async def start(self):
        """No Kafka consumer to start in direct mode."""
        logger.info("Starting Unified Message Processor in Direct Mode...")
        self.running = True
        logger.info("Unified Message Processor started successfully")

    async def stop(self):
        """Stop the message processor."""
        logger.info("Stopping Unified Message Processor...")
        self.running = False
        logger.info("Unified Message Processor stopped")

    async def process_message(self, topic: str, message: Dict[str, Any]):
        """
        Process incoming message directly.
        Original 'topic' parameter kept for interface compatibility.
        """
        try:
            logger.info(f"Processing direct message. Source reference: {topic}")

            # 1. Extract channel and message details
            channel = message.get('channel', 'web_form')
            customer_id = message.get('customer_id')
            conversation_id = message.get('conversation_id')
            content = message.get('content', '')
            customer_email = message.get('customer_email', '')
            customer_phone = message.get('customer_phone', '')

            logger.info(f"Processing message details: channel={channel}, customer_id={customer_id}, email={customer_email}")

            # 2. Resolve customer (cross-channel identification)
            try:
                resolved_customer_id = await self.resolve_customer({
                    'email': customer_email,
                    'phone': customer_phone,
                    'provided_customer_id': customer_id
                })
            except Exception as e:
                logger.error(f"Database error during customer resolution: {e}")
                resolved_customer_id = uuid.uuid4() # Fallback to temporary ID

            if not resolved_customer_id:
                logger.error(f"Could not resolve customer from message: {message}")
                return

            # 2.5 Check if customer is following up on a resolved ticket and reopen if needed
            ticket_id = message.get('ticket_id')
            if ticket_id:
                try:
                    async with db_session() as db:
                        from src.services.ticket_service import get_ticket_by_id, reopen_ticket
                        ticket = await get_ticket_by_id(db, uuid.UUID(ticket_id))
                        if ticket and ticket.status in ['closed', 'resolved']:
                            # Customer is following up on a resolved ticket, reopen it
                            reopend_ticket = await reopen_ticket(
                                db=db,
                                ticket_id=uuid.UUID(ticket_id),
                                reopen_reason="Customer sent follow-up message to resolved ticket"
                            )
                            logger.info(f"Reopened ticket {ticket_id} due to customer follow-up message")
                except Exception as e:
                    logger.error(f"Error checking/reopening ticket: {str(e)}")
                    # Continue processing even if ticket check fails

            # 3. Get or create conversation
            try:
                resolved_conversation_id = await self.get_or_create_conversation(
                    resolved_customer_id, channel, message
                )
            except Exception as e:
                logger.error(f"Database error during conversation retrieval: {e}")
                resolved_conversation_id = uuid.uuid4() # Fallback ID

            # 4. Store incoming message
            try:
                async with db_session() as db:
                    incoming_message = await create_message(
                        db=db,
                        conversation_id=resolved_conversation_id,
                        channel=channel,
                        direction="inbound",
                        sender_identifier=customer_email or customer_phone or str(resolved_customer_id),
                        content=content
                    )
                    logger.info(f"Stored incoming message: {incoming_message.id}")
            except Exception as e:
                logger.error(f"Failed to store incoming message: {e}")

            # 5. Load conversation history
            try:
                conversation_history = await self._load_conversation_history(resolved_conversation_id)
            except Exception as e:
                logger.error(f"Failed to load conversation history: {e}")
                conversation_history = []

            # 6. Run agent with context to generate response
            # Mistral-based agent call (CRITICAL: Runs even if DB fails)
            agent_response = await customer_success_agent.generate_channel_appropriate_response(
                content,
                resolved_customer_id,
                resolved_conversation_id,
                channel
            )

            # 7. Store agent response
            try:
                async with db_session() as db:
                    response_message = await create_message(
                        db=db,
                        conversation_id=resolved_conversation_id,
                        channel=channel,
                        direction="outbound",
                        sender_identifier="ai-agent",
                        content=agent_response
                    )
                    logger.info(f"Stored response message: {response_message.id}")
            except Exception as e:
                logger.error(f"Failed to store agent response: {e}")


            # 7.5 Send response via channel
            if channel == "whatsapp":
                recipient = customer_phone or customer_email # phone is preferred
                await self.whatsapp_handler.send_response_message(recipient, agent_response)
                logger.info(f"Sent WhatsApp response to {recipient}")
            elif channel == "email":
                # For email channel, the response is already formatted as an email
                # In a direct implementation, we call store_email_response (SMTP)
                await self.store_email_response({
                    "conversation_id": str(resolved_conversation_id),
                    "content": agent_response,
                    "customer_id": str(resolved_customer_id),
                    "recipient_email": customer_email,
                    "subject": message.get('subject', 'Response to your inquiry')
                })
                logger.info("Email response sent via Gmail")
            elif channel == "web_form":
                # For web form, send email notification to the customer with the AI response
                # First, get customer details to retrieve their email
                async with db_session() as db:
                    customer = await get_customer_by_id(db, resolved_customer_id)
                    if customer and customer.email:
                        # Send email notification to customer
                        await self.store_email_response({
                            "conversation_id": str(resolved_conversation_id),
                            "content": agent_response,
                            "customer_id": str(resolved_customer_id),
                            "recipient_email": customer.email,
                            "subject": f"Re: {message.get('subject', 'Your Support Request')}"
                        })
                        logger.info(f"Web form response email sent to customer: {customer.email}")
                    else:
                        logger.warning(f"No email found for customer {resolved_customer_id}, skipping email notification")

                # Also store the response for web access
                await self.store_web_response({
                    "conversation_id": str(resolved_conversation_id),
                    "content": agent_response,
                    "customer_id": str(resolved_customer_id)
                })
                logger.info("Web form response stored and email notification sent")

            # 8. Handle ticket status for the response
            ticket_id = message.get('ticket_id')

            # If there's an existing ticket, update its status
            if ticket_id:
                try:
                    async with db_session() as db:
                        from src.services.ticket_service import update_ticket_status
                        # Update existing ticket status to 'closed' after successful response
                        await update_ticket_status(
                            db=db,
                            ticket_id=uuid.UUID(ticket_id),
                            status="closed",
                            resolution_notes="Automatically closed after AI response was sent to customer"
                        )
                        logger.info(f"Ticket {ticket_id} updated to 'closed' status after AI response")
                except Exception as e:
                    logger.error(f"Error updating existing ticket status: {str(e)}")
                    # Continue processing even if ticket update fails
            else:
                # For channels that don't have an existing ticket (like direct email/WhatsApp), create one now
                try:
                    async with db_session() as db:
                        from src.services.ticket_service import create_ticket, update_ticket_status
                        from src.services.conversation_service import get_conversation_by_id

                        # Get conversation details to create ticket
                        conversation = await get_conversation_by_id(db, resolved_conversation_id)

                        # Create a new ticket for this conversation
                        new_ticket = await create_ticket(
                            db=db,
                            customer_id=resolved_customer_id,
                            source_channel=channel,
                            subject=message.get('subject', f"Support Request via {channel}")[:255],
                            category=message.get('category', 'general'),
                            priority=message.get('priority', 'medium'),
                            description=content[:5000]  # Truncate if too long
                        )

                        # Associate the ticket with the conversation
                        conversation.ticket_id = new_ticket.id
                        await db.flush()

                        # Now close the newly created ticket since response was sent
                        await update_ticket_status(
                            db=db,
                            ticket_id=new_ticket.id,
                            status="closed",
                            resolution_notes="Automatically created and closed after AI response was sent to customer"
                        )

                        logger.info(f"Created and closed new ticket {new_ticket.id} for {channel} message after AI response")

                except Exception as e:
                    logger.error(f"Error creating ticket for {channel} channel: {str(e)}")
                    # Continue processing even if ticket creation fails

            # 8.5 Update conversation status to indicate it's been handled
            try:
                async with db_session() as db:
                    # Update conversation status to 'closed' after successful response
                    updated_conversation = await update_conversation_status(
                        db=db,
                        conversation_id=resolved_conversation_id,
                        status="closed"
                    )
                    logger.info(f"Conversation {resolved_conversation_id} updated to 'closed' status after AI response")
            except Exception as e:
                logger.error(f"Error updating conversation status: {str(e)}")
                # Continue processing even if conversation update fails

            logger.info(f"Successfully processed direct message for customer {resolved_customer_id}")

        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")
            # Ensure we don't throw blocking errors that kill the process channel
            await self._send_error_response(message, str(e))


    async def store_web_response(self, response_data: Dict[str, Any]):
        """
        Store web form response. Kafka dependency removed.
        """
        # In a real implementation, this might push to a websocket or send a push notification.
        # For now, it's already stored in the DB as a message.
        logger.info(f"Web response stored for conversation {response_data.get('conversation_id')}")
        return {"status": "success", "channel": "web_form"}

    async def store_email_response(self, response_data: Dict[str, Any]):
        """
        Send email response to customer via SMTP.
        """
        # Import the Gmail handler
        from production.channels.gmail_handler import gmail_handler

        try:
            # Extract response details
            content = response_data.get('content', '')
            recipient_email = response_data.get('recipient_email', '')
            subject = response_data.get('subject', 'Response to your inquiry')

            # Format the response for email
            formatted_response = gmail_handler.format_email_response(
                ai_response=content,
                customer_email=recipient_email,
                original_subject=subject
            )

            # Send the email
            result = await gmail_handler.send_response_email(
                to_email=formatted_response['to_email'],
                subject=formatted_response['subject'],
                body=formatted_response['body']
            )

            if result['status'] == 'sent':
                logger.info(f"Email response sent successfully to {recipient_email}")
                return {"status": "success", "channel": "email", "to_email": recipient_email}
            else:
                logger.error(f"Failed to send email response: {result.get('error')}")
                return {"status": "error", "channel": "email", "error": result.get('error')}

        except Exception as e:
            logger.error(f"Error sending email response: {str(e)}")
            return {"status": "error", "channel": "email", "error": str(e)}

    async def resolve_customer(self, message: Dict[str, Any]) -> Optional[uuid.UUID]:
        """Match customer across channels."""
        try:
            provided_customer_id = message.get('provided_customer_id')
            email = message.get('email', '').strip().lower()
            phone = message.get('phone', '').strip()

            # If customer ID is provided, use it directly
            if provided_customer_id:
                try:
                    customer_uuid = uuid.UUID(provided_customer_id)
                    # Verify customer exists
                    async with db_session() as db:
                        from src.services.customer_service import get_customer_by_id
                        customer = await get_customer_by_id(db, customer_uuid)
                        if customer:
                            return customer_uuid
                except ValueError:
                    pass

            # Try to find by email
            if email:
                async with db_session() as db:
                    customer = await get_customer_by_identifier(db, email, 'email')
                    if customer:
                        return customer.id

            # Try to find by phone
            if phone:
                async with db_session() as db:
                    customer = await get_customer_by_identifier(db, phone, 'phone')
                    if customer:
                        return customer.id

            # Create new customer if not found
            identifier = email or phone or f"anonymous_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
            name = message.get('name', f"Customer {identifier.split('@')[0] if '@' in identifier else identifier}")

            async with db_session() as db:
                new_customer = await get_or_create_customer(
                    db=db,
                    email=email,
                    name=name,
                    company=message.get('company'),
                    phone=phone
                )
                
                # CRITICAL: Commit the transaction to persist the customer
                await db.commit()
                await db.refresh(new_customer)

                return new_customer.id

        except Exception as e:
            logger.error(f"Error resolving customer: {str(e)}")
            # Handle Railway/DB connection errors gracefully
            if "Name or service not known" in str(e) or "Connection refused" in str(e):
                logger.warning("Database seems unreachable. Falling back to temporary anonymous context.")
                return uuid.uuid4() # Fallback ID for processing to continue
            return None


    async def get_or_create_conversation(self, customer_id: uuid.UUID, channel: str, message: Dict[str, Any]) -> uuid.UUID:
        """Get or create conversation context."""
        try:
            # Check if there's an active conversation for this customer
            async with db_session() as db:
                conversations = await get_conversations_by_customer(db, customer_id)

                # Look for an active conversation in the same channel
                for conversation in conversations:
                    if (conversation.status in ['open', 'in_progress'] and
                        conversation.initial_channel == channel and
                        # Check if conversation is recent (last 24 hours)
                        (datetime.now(timezone.utc) - conversation.updated_at).seconds < 86400):

                        return conversation.id

                # Look for a recently closed conversation in the same channel and reopen it
                for conversation in conversations:
                    if (conversation.status in ['closed', 'resolved'] and
                        conversation.initial_channel == channel):
                        # Check if conversation was closed recently (last 7 days)
                        time_since_close = datetime.now(timezone.utc) - conversation.updated_at
                        if time_since_close.total_seconds() <= 7 * 24 * 3600:  # 7 days in seconds

                            # Reopen the conversation
                            async with db_session() as reopen_db:
                                await update_conversation_status(
                                    db=reopen_db,
                                    conversation_id=conversation.id,
                                    status="open"
                                )
                                await reopen_db.commit()
                                return conversation.id

                # Create new conversation
                new_conversation = await create_conversation(
                    db=db,
                    customer_id=customer_id,
                    initial_channel=channel
                )
                
                # CRITICAL: Commit the transaction to persist the conversation
                await db.commit()
                await db.refresh(new_conversation)

                return new_conversation.id

        except Exception as e:
            logger.error(f"Error getting/creating conversation: {str(e)}")

            # Create a new conversation as fallback
            async with db_session() as db:
                fallback_conversation = await create_conversation(
                    db=db,
                    customer_id=customer_id,
                    initial_channel=channel
                )
                await db.commit()
                await db.refresh(fallback_conversation)
                return fallback_conversation.id

    async def _load_conversation_history(self, conversation_id: uuid.UUID) -> list:
        """Load conversation history for context."""
        try:
            async with db_session() as db:
                messages = await get_messages_by_conversation(db, conversation_id)

                # Format messages for context
                history = []
                for msg in messages:
                    history.append({
                        'direction': msg.direction,
                        'channel': msg.channel,
                        'content': msg.content,
                        'timestamp': msg.created_at.isoformat() if msg.created_at else None,
                    })

                logger.info(f"Loaded {len(history)} messages for conversation {conversation_id}")
                return history

        except Exception as e:
            logger.error(f"Error loading conversation history: {str(e)}")
            return []

    async def _send_error_response(self, message: Dict[str, Any], error: str):
        """Send error response (logs only in direct mode for now)."""
        logger.error(f"Critical error in direct processing: {error}")


# Global instance for easy access across the monolith components
message_processor = UnifiedMessageProcessor()

async def main():
    """Main function to run the message processor."""
    await message_processor.start()


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.INFO)

    # Run the processor
    asyncio.run(main())
