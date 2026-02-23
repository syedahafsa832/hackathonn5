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
        Process incoming message with AI operational modes and human takeover logic.
        """
        try:
            # 1. Extract details
            channel = message.get('channel', 'web_form')
            content = message.get('content', '')
            customer_email = message.get('customer_email', '').strip().lower()
            customer_name = message.get('customer_name', message.get('name', 'Unknown'))
            subject = message.get('subject', 'New Support Request')
            store_id = message.get('store_id', '00000000-0000-0000-0000-000000000000') # Default for now

            if not customer_email:
                logger.warning("Message missing customer email, cannot process reliably.")
                return

            # safeguard logic (simplified for brevity, keep existing automated_keywords check)
            if self._is_automated(customer_email, content):
                return

            # 2. Check System Settings (Operational Mode)
            settings = await supabase_service.get_system_settings(store_id)
            ai_mode = settings.get('ai_mode', 'active')
            confidence_threshold = settings.get('confidence_threshold', 0.75)

            # 3. Resolve Customer
            customer = await supabase_service.get_or_create_customer(
                email=customer_email,
                store_id=store_id,
                name=customer_name
            )

            # 4. Handle Operational Modes
            if ai_mode == "manual":
                logger.info(f"AI Mode: Manual. Creating human-only ticket for {customer_email}")
                await supabase_service.create_ticket({
                    "store_id": store_id, "customer_name": customer_name, "customer_email": customer_email,
                    "subject": subject, "message": content, "status": "requires_human"
                })
                return

            # 5. Get AI Analysis
            ai_result = await customer_success_agent.generate_channel_appropriate_response(
                query=content, customer_info=customer, channel=channel
            )
            confidence = ai_result.get("confidence_score", 0) / 100.0

            # 6. Prepare Ticket Initial State
            ticket_payload = {
                "store_id": store_id, "customer_name": customer_name, "customer_email": customer_email,
                "subject": subject, "message": content, "intent": ai_result.get("intent"),
                "sentiment": ai_result.get("sentiment"), "risk_level": ai_result.get("risk_level"),
                "confidence_score": ai_result.get("confidence_score"),
                "escalate": ai_result.get("escalate", False),
                "escalation_reason": ai_result.get("escalation_reason")
            }

            # 7. Check for Human Takeover
            # In a real app, we use a thread ID, but here we can check if any override exists for this customer/subject
            # We'll simulate by checking if there's an active override for THIS specific ticket ID (after we know it) or a previous one.
            # For this MVP, we perform a lookup on active overrides for the same customer email.
            overrides = supabase_select("conversation_overrides", {
                "active": "eq.true"
            })
            # Check if any active override references a ticket with same customer email
            # (In a more robust system, we'd use a thread_id join)
            is_overridden = False
            for ov in overrides:
                convo_id = ov.get("conversation_id")
                # Look up ticket to see if it's the same customer
                ov_ticket = await supabase_service.get_ticket_by_id(convo_id)
                if ov_ticket and ov_ticket.get("customer_email") == customer_email:
                    is_overridden = True
                    break

            # 8. Fail-Safe Mechanism (Anomaly Detection)
            # If AI confidence is extremely low (<10%), trigger an emergency pause
            if confidence < 0.10:
                logger.warning(f"Fail-Safe Triggered: Extremely low confidence ({confidence}). Pausing AI Mode.")
                supabase_update("system_settings", {"store_id": f"eq.{store_id}"}, {"ai_mode": "paused"})
                await supabase_service.log_audit(store_id, "fail_safe_pause", "system", {"trigger": "low_confidence", "score": confidence})
                ai_mode = "paused" # Force state change for this run

            # 9. Decision Logic
            should_auto_reply = False
            if ai_mode == "paused":
                logger.info("AI Mode: Paused. Storing draft but NOT sending.")
                ticket_payload["ai_draft"] = ai_result.get("reply_body")
                ticket_payload["status"] = "ai_suggested"
            elif ai_mode == "active":
                if is_overridden:
                    logger.info("Human Takeover active. Suppressing AI reply.")
                    ticket_payload["status"] = "human_managing"
                elif confidence >= confidence_threshold and not ticket_payload["escalate"]:
                    should_auto_reply = True
                    ticket_payload["ai_reply"] = ai_result.get("reply_body")
                    ticket_payload["status"] = "auto_resolved"
                else:
                    logger.info(f"Low confidence ({confidence}) or escalation requested. Routing to human.")
                    ticket_payload["status"] = "escalated"

            # 10. Store Ticket
            ticket = await supabase_service.create_ticket(ticket_payload)
            
            # 11. Send Response if allowed
            if should_auto_reply:
                await self.send_email_response(customer_email, subject, ai_result)

        except Exception as e:
            logger.error(f"Error in process_message: {str(e)}")

    def _is_automated(self, email: str, content: str) -> bool:
        """Helper for automated email filtering."""
        automated_keywords = ['no-reply', 'noreply', 'notifications', 'mailer-daemon']
        return any(kw in email for kw in automated_keywords)

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
