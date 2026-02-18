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
from src.services.kafka_client import kafka_client_service
from src.services.whatsapp_handler import WhatsAppHandler
from src.services.message_service import create_message, get_messages_by_conversation
from src.services.customer_service import get_customer_by_identifier, get_or_create_customer, get_customer_by_id
from src.services.conversation_service import get_conversations_by_customer, create_conversation, get_conversation_by_id
from src.agent.customer_success_agent import customer_success_agent


logger = logging.getLogger(__name__)


class UnifiedMessageProcessor:
    """Process incoming messages from all channels through the FTE agent."""

    def __init__(self):
        # Initialize channel handlers and services
        self.kafka_client = kafka_client_service
        self.whatsapp_handler = WhatsAppHandler()
        self.running = False

    async def start(self):
        """Start Kafka consumer on fte.tickets.incoming and begin processing loop."""
        logger.info("Starting Unified Message Processor...")

        # Start the Kafka consumer for the incoming tickets topic
        self.kafka_client.start_consumer('tickets_incoming', 'fte-message-processor-group')

        self.running = True
        logger.info("Unified Message Processor started successfully")

        # Start the main processing loop
        await self._processing_loop()

    async def stop(self):
        """Stop the message processor."""
        logger.info("Stopping Unified Message Processor...")
        self.running = False
        self.kafka_client.shutdown()
        logger.info("Unified Message Processor stopped")

    async def _processing_loop(self):
        """Main processing loop that consumes messages from Kafka."""
        logger.info("Entering main processing loop...")

        # Listen to topics
        if self.kafka_client:
            logger.info("Listening for messages on specified topics...")
            topics = ['tickets_incoming', 'whatsapp_inbound']
            # Start consumer for the specified topics
            self.kafka_client.start_consumer(topics, 'fte-message-processor-group')

            while self.running:
                try:
                    # Consume messages from the specified topics
                    for topic, message_data in self.kafka_client.listen_to_topic(topics):
                        if not self.running:
                            break # Exit if processor is stopping

                        logger.info(f"Incoming message from topic: {topic}")
                        await self.process_message(topic, message_data)

                        # Small delay to prevent busy waiting
                        await asyncio.sleep(0.1)

                except Exception as e:
                    logger.error(f"Error in processing loop: {str(e)}")
                    await asyncio.sleep(5)  # Wait before retrying if an error occurs outside message processing

    async def process_message(self, topic: str, message: Dict[str, Any]):
        """
        Process incoming message with the following steps:
        1. Extract channel
        2. Resolve customer (cross-channel identification)
        3. Get/create conversation
        4. Store incoming message
        5. Load conversation history
        6. Run agent with context
        7. Store agent response
        8. Publish metrics
        """
        try:
            logger.info(f"Processing message from topic: {topic}")

            # 1. Extract channel and message details
            channel = message.get('channel', 'web_form')
            customer_id = message.get('customer_id')
            conversation_id = message.get('conversation_id')
            content = message.get('content', '')
            customer_email = message.get('customer_email', '')
            customer_phone = message.get('customer_phone', '')

            logger.info(f"Processing message details: channel={channel}, customer_id={customer_id}, email={customer_email}")

            # 2. Resolve customer (cross-channel identification)
            resolved_customer_id = await self.resolve_customer({
                'email': customer_email,
                'phone': customer_phone,
                'provided_customer_id': customer_id
            })

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
            resolved_conversation_id = await self.get_or_create_conversation(
                resolved_customer_id, channel, message
            )

            # 4. Store incoming message
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

            # 5. Load conversation history
            conversation_history = await self._load_conversation_history(resolved_conversation_id)

            # 6. Run agent with context to generate response
            agent_response = await customer_success_agent.generate_channel_appropriate_response(
                content,
                resolved_customer_id,
                resolved_conversation_id,
                channel
            )

            # 7. Store agent response
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

            # 7.5 Send response via channel
            if channel == "whatsapp":
                recipient = customer_phone or customer_email # phone is preferred
                await self.whatsapp_handler.send_response_message(recipient, agent_response)
                logger.info(f"Sent WhatsApp response to {recipient}")
            elif channel == "email":
                # For email channel, the response is already formatted as an email
                # In a real implementation, we would send the email using an email service
                # For now, we'll store it similar to web form
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
                        email_notification_result = await self.store_email_response({
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
                    from src.services.conversation_service import update_conversation_status
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

            # 9. Publish metrics
            message_timestamp = message.get('timestamp')
            if message_timestamp:
                # Parse timestamp and ensure it's timezone-aware
                if isinstance(message_timestamp, str):
                    msg_time = datetime.fromisoformat(message_timestamp)
                    if msg_time.tzinfo is None:
                        msg_time = msg_time.replace(tzinfo=timezone.utc)
                else:
                    msg_time = message_timestamp
                    if msg_time.tzinfo is None:
                        msg_time = msg_time.replace(tzinfo=timezone.utc)
                response_time = (datetime.now(timezone.utc) - msg_time).total_seconds()
            else:
                response_time = 0

            await self._publish_metrics({
                'customer_id': str(resolved_customer_id),
                'conversation_id': str(resolved_conversation_id),
                'channel': channel,
                'ticket_id': ticket_id,  # Include ticket_id in metrics
                'response_time': response_time,
                'processed_at': datetime.now(timezone.utc).isoformat()
            })

            logger.info(f"Successfully processed message for customer {resolved_customer_id}")

        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")
            # Log error to DLQ
            await self._log_error_to_dlq(message, str(e))

            # Try to send error response to customer
            await self._send_error_response(message, str(e))

    async def store_web_response(self, response_data: Dict[str, Any]):
        """
        Store web form response and trigger notification (placeholder)
        """
        # In a real implementation, this might push to a websocket or send a push notification.
        # For now, it's already stored in the DB as a message.
        # We could also publish to a specific webform_outbound topic.
        await self.kafka_client.send_to_topic('webform_outbound', response_data)
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
        """
        Match customer across channels by email, phone, or provided customer ID.
        """
        try:
            provided_customer_id = message.get('provided_customer_id')
            email = message.get('email', '').strip().lower()
            phone = message.get('phone', '').strip()

            # If customer ID is provided, use it directly
            if provided_customer_id:
                try:
                    customer_uuid = uuid.UUID(provided_customer_id)
                    logger.info(f"Attempting to resolve customer by ID: {customer_uuid}")
                    # Verify customer exists
                    async with db_session() as db:
                        from src.services.customer_service import get_customer_by_id
                        customer = await get_customer_by_id(db, customer_uuid)
                        if customer:
                            logger.info(f"Successfully resolved customer by ID: {customer_uuid}")
                            return customer_uuid
                        else:
                            logger.warning(f"Customer with ID {customer_uuid} not found in database")
                except ValueError:
                    logger.warning(f"Invalid UUID provided: {provided_customer_id}")

            # Try to find by email
            if email:
                async with db_session() as db:
                    customer = await get_customer_by_identifier(db, email, 'email')
                    if customer:
                        logger.info(f"Resolved customer by email: {email}")
                        return customer.id

            # Try to find by phone
            if phone:
                async with db_session() as db:
                    customer = await get_customer_by_identifier(db, phone, 'phone')
                    if customer:
                        logger.info(f"Resolved customer by phone: {phone}")
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

                logger.info(f"Created new customer: {new_customer.id}")
                return new_customer.id

        except Exception as e:
            logger.error(f"Error resolving customer: {str(e)}")
            return None

    async def get_or_create_conversation(self, customer_id: uuid.UUID, channel: str, message: Dict[str, Any]) -> uuid.UUID:
        """
        Get active conversation or create new one based on message context.
        """
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

                        logger.info(f"Found existing conversation: {conversation.id}")
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
                                updated_conversation = await update_conversation_status(
                                    db=reopen_db,
                                    conversation_id=conversation.id,
                                    status="open"
                                )
                                logger.info(f"Reopened conversation: {conversation.id} due to new message")
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

                logger.info(f"Created new conversation: {new_conversation.id}")
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
                logger.info(f"Created fallback conversation: {fallback_conversation.id}")
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
                        'sentiment': float(msg.sentiment_score) if msg.sentiment_score else 0.0
                    })

                logger.info(f"Loaded {len(history)} messages for conversation {conversation_id}")
                return history

        except Exception as e:
            logger.error(f"Error loading conversation history: {str(e)}")
            return []

    async def _publish_metrics(self, metrics_data: Dict[str, Any]):
        """Publish processing metrics to the metrics topic."""
        try:
            await self.kafka_client.send_to_topic('metrics', metrics_data)
            logger.info("Published metrics to Kafka")
        except Exception as e:
            logger.error(f"Error publishing metrics: {str(e)}")

    async def _log_error_to_dlq(self, original_message: Dict[str, Any], error: str):
        """Log error to dead letter queue for manual processing."""
        try:
            dlq_message = {
                'original_message': original_message,
                'error': error,
                'failed_at': datetime.now(timezone.utc).isoformat(),
                'retry_count': original_message.get('retry_count', 0) + 1
            }

            await self.kafka_client.send_to_topic('dlq', dlq_message)
            logger.warning("Error logged to DLQ")
        except Exception as e:
            logger.error(f"Error logging to DLQ: {str(e)}")

    async def _send_error_response(self, message: Dict[str, Any], error: str):
        """Send error response back to customer."""
        try:
            # Create a generic error response
            error_response = {
                'ticket_id': message.get('ticket_id'),
                'channel': message.get('channel', 'web_form'),
                'content': 'We encountered an issue processing your request. Our team has been notified and will address this shortly.',
                'is_error_response': True
            }

            # Send to appropriate response topic based on original channel
            channel_topic_map = {
                'whatsapp': 'whatsapp_outbound',
                'web_form': 'webform_outbound',  # Assumed new topic or just internal storage
                'email': 'webform_outbound'  # Using same topic for simplicity
            }

            response_topic = channel_topic_map.get(message.get('channel', 'web_form'), 'webform_outbound')
            await self.kafka_client.send_to_topic(response_topic, error_response)

            logger.info("Error response sent to customer")
        except Exception as e:
            logger.error(f"Error sending error response: {str(e)}")


async def main():
    """Main function to run the message processor."""
    processor = UnifiedMessageProcessor()

    try:
        await processor.start()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        await processor.stop()


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Run the processor
    asyncio.run(main())
