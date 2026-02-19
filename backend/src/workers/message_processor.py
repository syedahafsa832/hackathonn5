#!/usr/bin/env python3
"""
Centralized Message Processor for Customer Success AI Agent
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

            # 2. Resolve customer
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

            # 5. Run agent with context to generate response
            agent_response = await customer_success_agent.generate_channel_appropriate_response(
                content,
                resolved_customer_id,
                resolved_conversation_id,
                channel
            )

            # 6. Store agent response
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


            # 7. Send response via channel
            if channel == "whatsapp":
                recipient = customer_phone or customer_email
                await self.whatsapp_handler.send_response_message(recipient, agent_response)
                logger.info(f"Sent WhatsApp response to {recipient}")
                
                # Proactive Email: If we have an email for the customer, send a copy there too
                async with db_session() as db:
                    customer = await get_customer_by_id(db, resolved_customer_id)
                    if customer and customer.email:
                        await self.store_email_response({
                            "conversation_id": str(resolved_conversation_id),
                            "content": agent_response,
                            "customer_id": str(resolved_customer_id),
                            "recipient_email": customer.email,
                            "subject": "Update on your WhatsApp Inquiry"
                        })
            elif channel == "email":
                # Ensure we have the latest customer email if missing in payload
                target_email = customer_email
                if not target_email:
                    async with db_session() as db:
                        customer = await get_customer_by_id(db, resolved_customer_id)
                        if customer: target_email = customer.email

                if target_email:
                    await self.store_email_response({
                        "conversation_id": str(resolved_conversation_id),
                        "content": agent_response,
                        "customer_id": str(resolved_customer_id),
                        "recipient_email": target_email,
                        "subject": message.get('subject', 'Response to your inquiry')
                    })
                    logger.info(f"Email response sent to {target_email} via Gmail")
                else:
                    logger.warning(f"No email address found for customer {resolved_customer_id} to send response")

            elif channel == "web_form":
                # For web form, send email notification to the customer if email is available
                async with db_session() as db:
                    customer = await get_customer_by_id(db, resolved_customer_id)
                    if customer and customer.email:
                        await self.store_email_response({
                            "conversation_id": str(resolved_conversation_id),
                            "content": agent_response,
                            "customer_id": str(resolved_customer_id),
                            "recipient_email": customer.email,
                            "subject": f"Re: {message.get('subject', 'Your Support Request')}"
                        })
                        logger.info(f"Web form response email sent to customer: {customer.email}")

                await self.store_web_response({
                    "conversation_id": str(resolved_conversation_id),
                    "content": agent_response,
                    "customer_id": str(resolved_customer_id)
                })

            logger.info(f"Successfully processed message for customer {resolved_customer_id}")

        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")

    async def store_web_response(self, response_data: Dict[str, Any]):
        logger.info(f"Web response stored for conversation {response_data.get('conversation_id')}")
        return {"status": "success", "channel": "web_form"}

    async def store_email_response(self, response_data: Dict[str, Any]):
        from production.channels.gmail_handler import gmail_handler
        try:
            content = response_data.get('content', '')
            recipient_email = response_data.get('recipient_email', '')
            subject = response_data.get('subject', 'Response to your inquiry')

            formatted_response = gmail_handler.format_email_response(
                ai_response=content,
                customer_email=recipient_email,
                original_subject=subject
            )

            result = await gmail_handler.send_reply(
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
        try:
            provided_customer_id = message.get('provided_customer_id')
            email = message.get('email', '').strip().lower()
            phone = message.get('phone', '').strip()

            if provided_customer_id:
                try:
                    customer_uuid = uuid.UUID(provided_customer_id)
                    async with db_session() as db:
                        customer = await get_customer_by_id(db, customer_uuid)
                        if customer:
                            return customer_uuid
                except ValueError: pass

            if email:
                async with db_session() as db:
                    customer = await get_customer_by_identifier(db, email, 'email')
                    if customer: return customer.id

            if phone:
                async with db_session() as db:
                    customer = await get_customer_by_identifier(db, phone, 'phone')
                    if customer: return customer.id

            identifier = email or phone or f"anonymous_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
            name = message.get('name', f"Customer {identifier.split('@')[0] if '@' in identifier else identifier}")

            async with db_session() as db:
                new_customer = await get_or_create_customer(
                    db=db, email=email, name=name, company=message.get('company'), phone=phone
                )
                await db.commit()
                await db.refresh(new_customer)
                return new_customer.id
        except Exception as e:
            logger.error(f"Error resolving customer: {str(e)}")
            return uuid.uuid4()

    async def get_or_create_conversation(self, customer_id: uuid.UUID, channel: str, message: Dict[str, Any]) -> uuid.UUID:
        try:
            async with db_session() as db:
                conversations = await get_conversations_by_customer(db, customer_id)
                for conversation in conversations:
                    if (conversation.status in ['open', 'in_progress'] and
                        conversation.initial_channel == channel):
                        return conversation.id

                new_conversation = await create_conversation(db=db, customer_id=customer_id, initial_channel=channel)
                await db.commit()
                await db.refresh(new_conversation)
                return new_conversation.id
        except Exception as e:
            logger.error(f"Error getting/creating conversation: {str(e)}")
            return uuid.uuid4()

# Global instance
message_processor = UnifiedMessageProcessor()
