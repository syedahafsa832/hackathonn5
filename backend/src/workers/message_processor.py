import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone
import uuid

from src.services.supabase_service import supabase_service
from src.agent.customer_success_agent import customer_success_agent

logger = logging.getLogger(__name__)

class UnifiedMessageProcessor:
    """Process incoming messages from all channels through the FTE agent directly using Supabase."""

    def __init__(self):
        self.running = False

    async def start(self):
        logger.info("Starting Unified Message Processor (Supabase Mode)...")
        self.running = True

    async def stop(self):
        logger.info("Stopping Unified Message Processor...")
        self.running = False

    async def process_message(self, topic: str, message: Dict[str, Any]):
        """
        Process incoming message: Resolve customer -> AI Analysis -> Save to Supabase -> Send Response
        """
        try:
            # 1. Extract details
            channel = message.get('channel', 'web_form')
            content = message.get('content', '')
            customer_email = message.get('customer_email', '').strip().lower()
            customer_name = message.get('customer_name', message.get('name', 'Unknown'))
            subject = message.get('subject', 'New Support Request')

            if not customer_email:
                logger.warning("Message missing customer email, cannot process reliably.")
                return

            # RE-CHECK: Final safeguard against automated emails (don't store, don't reply)
            automated_keywords = ['no-reply', 'noreply', 'notifications', 'mailer-daemon', 'linkedin.com', 'skool.com']
            if any(kw in customer_email for kw in automated_keywords):
                logger.info(f"Safeguard: Dropping automated message from {customer_email}")
                return

            # 2. Resolve or Create Customer in Supabase
            customer = await supabase_service.get_or_create_customer(
                email=customer_email,
                name=customer_name
            )
            
            # 3. Get AI Analysis (Structured JSON)
            ai_result = await customer_success_agent.generate_channel_appropriate_response(
                query=content,
                customer_info=customer,
                channel=channel
            )

            # 4. Prepare Ticket Data (as per Step 3 Schema)
            ticket_payload = {
                "customer_name": customer.get("name"),
                "customer_email": customer_email,
                "subject": subject,
                "message": content,
                "ai_reply": ai_result.get("reply_body"),
                "intent": ai_result.get("intent"),
                "sentiment": ai_result.get("sentiment"),
                "risk_level": ai_result.get("risk_level"),
                "confidence_score": ai_result.get("confidence_score"),
                "escalate": ai_result.get("escalate", False),
                "escalation_reason": ai_result.get("escalation_reason"),
                "status": ai_result.get("status", "open")
            }

            # 5. Store in Supabase (Step 6)
            try:
                ticket = await supabase_service.create_ticket(ticket_payload)
                logger.info(f"Ticket stored in Supabase: {ticket.get('id')}")
            except Exception as e:
                logger.error(f"Failed to store ticket in Supabase: {e}")
                # Fallback: We still want to reply to the user if possible, but the record is lost
            
            # 6. Send Response via appropriate channel (Step 6 & 8)
            if channel == "email":
                await self.send_email_response(customer_email, subject, ai_result)
            elif channel == "web_form":
                # For web form, the caller (FastAPI) usually returns the response directly
                # but we can also trigger an email notification here
                await self.send_email_response(customer_email, f"Re: {subject}", ai_result)

            logger.info("Successfully processed message and saved to Supabase.")

        except Exception as e:
            logger.error(f"Error in process_message: {str(e)}")

    async def send_email_response(self, email: str, subject: str, ai_result: Dict[str, Any]):
        """Helper to send email responses (Gmail integration)."""
        try:
            # Import here to avoid circular dependencies
            from production.channels.gmail_handler import gmail_handler
            
            await gmail_handler.send_reply(
                to_email=email,
                subject=ai_result.get("reply_subject", subject),
                body=ai_result.get("reply_body")
            )
            logger.info(f"Email reply sent to {email}")
        except Exception as e:
            logger.error(f"Failed to send email reply: {e}")

# Global instance
message_processor = UnifiedMessageProcessor()
