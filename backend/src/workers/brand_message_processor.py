"""
Brand Message Processor
=======================
Processes incoming messages with brand-level multi-tenant isolation.
Uses the new organization/brand hierarchy for proper data separation.
"""

import asyncio
import logging
import os
from typing import Dict, Any, Optional
from datetime import datetime, timezone
import uuid

from src.agent.customer_success_agent import customer_success_agent
from src.services.brand_knowledge_service import brand_knowledge_service
from src.lib.supabase_client import supabase_select, supabase_insert, supabase_update

logger = logging.getLogger(__name__)


class BrandMessageProcessor:
    """
    Process incoming messages with brand-level isolation.

    Features:
    - Brand lookup by support email
    - Per-brand AI settings
    - Brand-specific RAG context
    - Proper ticket and action creation with brand_id
    """

    def __init__(self):
        self.running = False

    async def start(self):
        logger.info("Starting Brand Message Processor...")
        self.running = True

    async def stop(self):
        logger.info("Stopping Brand Message Processor...")
        self.running = False

    async def process_message(self, topic: str, message: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process incoming message with brand isolation.

        Args:
            topic: Message source topic
            message: Message data containing:
                - channel: email, web_form, whatsapp
                - customer_email: Customer's email
                - customer_name: Customer's name
                - content: Message content
                - subject: Optional subject line
                - brand_id: Optional brand ID (if known)
                - to_email: Optional recipient email (for brand lookup)

        Returns:
            Result dict with ticket_id, status, email_sent
        """
        try:
            # ========== STAGE 1: EXTRACT & VALIDATE ==========
            channel = message.get('channel', 'web_form')
            content = message.get('content', '').strip()
            customer_email = message.get('customer_email', '').strip().lower()
            customer_name = message.get('customer_name', 'Customer')
            subject = message.get('subject', 'New Support Request')

            logger.info(f"[BRAND-PROCESSOR] ========== NEW MESSAGE ==========")
            logger.info(f"[BRAND-PROCESSOR] Channel: {channel}")
            logger.info(f"[BRAND-PROCESSOR] Customer: {customer_email}")
            logger.info(f"[BRAND-PROCESSOR] Subject: {subject}")

            if not customer_email or not content:
                logger.warning("[BRAND-PROCESSOR] Missing required fields")
                return {"ticket_id": None, "status": "error", "error": "Missing email or content"}

            # Filter automated/system emails
            if self._is_automated(customer_email, content):
                logger.info(f"[BRAND-PROCESSOR] Skipping automated email")
                return {"ticket_id": None, "status": "skipped", "reason": "automated"}

            # ========== STAGE 2: BRAND LOOKUP ==========
            brand = await self._find_brand(message)

            if not brand:
                logger.warning("[BRAND-PROCESSOR] No brand found - cannot process")
                return {"ticket_id": None, "status": "error", "error": "Brand not found"}

            brand_id = brand["id"]
            brand_name = brand["name"]
            logger.info(f"[BRAND-PROCESSOR] Brand: {brand_name} ({brand_id})")

            # ========== STAGE 3: CHECK AI SETTINGS ==========
            ai_enabled = brand.get("ai_enabled", True)
            ai_auto_respond = brand.get("ai_auto_respond", False)
            ai_confidence_threshold = float(brand.get("ai_confidence_threshold", 0.75))

            if not ai_enabled:
                logger.info(f"[BRAND-PROCESSOR] AI disabled for brand - creating manual ticket")
                ticket = await self._create_ticket(
                    brand_id=brand_id,
                    customer_email=customer_email,
                    customer_name=customer_name,
                    subject=subject,
                    message=content,
                    channel=channel,
                    status="open"
                )
                return {"ticket_id": ticket.get("id"), "status": "manual"}

            # ========== STAGE 4: GET RAG CONTEXT ==========
            logger.info(f"[BRAND-PROCESSOR] Retrieving brand knowledge...")
            rag_context = await brand_knowledge_service.get_brand_context(
                brand_id=brand_id,
                query=content,
                top_k=5
            )
            logger.info(f"[BRAND-PROCESSOR] RAG context: {len(rag_context)} chars")

            # ========== STAGE 5: AI ANALYSIS ==========
            logger.info(f"[BRAND-PROCESSOR] Generating AI response...")

            # Build customer info for agent
            customer_info = {
                "email": customer_email,
                "name": customer_name,
                "brand_context": rag_context
            }

            ai_result = await customer_success_agent.generate_channel_appropriate_response(
                query=content,
                customer_info=customer_info,
                channel=channel,
                tenant_id=brand_id  # Use brand_id for RAG in legacy agent
            )

            confidence = ai_result.get("confidence_score", 0) / 100.0
            intent = ai_result.get("intent", "unknown")
            sentiment = ai_result.get("sentiment", "neutral")
            reply_body = ai_result.get("reply_body", "")
            escalate = ai_result.get("escalate", False)
            escalation_reason = ai_result.get("escalation_reason")

            logger.info(f"[BRAND-PROCESSOR] AI: Intent={intent}, Confidence={confidence:.0%}, Sentiment={sentiment}")

            # ========== STAGE 6: CREATE TICKET ==========
            ticket_status = self._determine_ticket_status(
                ai_enabled=ai_enabled,
                ai_auto_respond=ai_auto_respond,
                confidence=confidence,
                confidence_threshold=ai_confidence_threshold,
                escalate=escalate,
                has_reply=bool(reply_body)
            )

            ticket = await self._create_ticket(
                brand_id=brand_id,
                customer_email=customer_email,
                customer_name=customer_name,
                subject=subject,
                message=content,
                channel=channel,
                status=ticket_status,
                ai_response=reply_body if ticket_status in ["ai_responded", "pending"] else None,
                ai_confidence=confidence,
                ai_sentiment=sentiment,
                ai_intent=intent,
                ai_reasoning=ai_result.get("reasoning")
            )

            ticket_id = ticket.get("id")
            logger.info(f"[BRAND-PROCESSOR] Ticket created: {ticket_id} (status: {ticket_status})")

            # ========== STAGE 7: ACTION DETECTION ==========
            action_result = await self._detect_and_create_action(
                brand_id=brand_id,
                ticket_id=ticket_id,
                customer_email=customer_email,
                customer_name=customer_name,
                message=content,
                ai_confidence=confidence,
                ai_intent=intent
            )

            if action_result:
                logger.info(f"[BRAND-PROCESSOR] Action created: {action_result.get('id')} ({action_result.get('action_type')})")

            # ========== STAGE 8: SEND RESPONSE ==========
            email_sent = False

            if ai_auto_respond and ticket_status == "ai_responded" and reply_body:
                logger.info(f"[BRAND-PROCESSOR] Auto-sending response to {customer_email}")
                email_sent = await self._send_response(
                    brand=brand,
                    ticket_id=ticket_id,
                    customer_email=customer_email,
                    subject=subject,
                    response=reply_body
                )

                if email_sent:
                    await self._update_ticket(ticket_id, {
                        "response_sent": True,
                        "response_sent_at": datetime.now(timezone.utc).isoformat(),
                        "response_method": "email"
                    })

            # ========== STAGE 9: LOG CONVERSATION ==========
            await self._log_conversation(
                brand_id=brand_id,
                ticket_id=ticket_id,
                user_message=content,
                ai_response=reply_body,
                model="mistral-large"
            )

            # ========== STAGE 10: RETURN RESULT ==========
            result = {
                "ticket_id": ticket_id,
                "status": ticket_status,
                "email_sent": email_sent,
                "action_created": action_result.get("id") if action_result else None
            }

            logger.info(f"[BRAND-PROCESSOR] ========== COMPLETE: {result} ==========")
            return result

        except Exception as e:
            logger.error(f"[BRAND-PROCESSOR] ERROR: {str(e)}", exc_info=True)
            return {"ticket_id": None, "status": "error", "error": str(e)}

    # ==================== Helper Methods ====================

    async def _find_brand(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Find the brand for this message"""
        # 1. Direct brand_id
        if message.get("brand_id"):
            brands = supabase_select("brands", {
                "id": f"eq.{message['brand_id']}",
                "is_active": "eq.true"
            })
            if brands:
                return brands[0]

        # 2. By recipient email (to_email)
        to_email = message.get("to_email", "").lower().strip()
        if to_email:
            brands = supabase_select("brands", {
                "support_email": f"eq.{to_email}",
                "is_active": "eq.true"
            })
            if brands:
                return brands[0]

        # 3. By environment variable
        support_email = os.getenv("SUPPORT_EMAIL_ADDRESS", "").lower()
        if support_email:
            brands = supabase_select("brands", {
                "support_email": f"eq.{support_email}",
                "is_active": "eq.true"
            })
            if brands:
                return brands[0]

        # 4. Fallback to first active brand (single-brand setups)
        brands = supabase_select("brands", {
            "is_active": "eq.true",
            "order": "created_at.asc",
            "limit": "1"
        })
        if brands:
            return brands[0]

        return None

    def _is_automated(self, email: str, content: str) -> bool:
        """Check if email is automated/system"""
        automated_patterns = [
            "noreply@", "no-reply@", "mailer-daemon@",
            "postmaster@", "bounce@", "notification@"
        ]
        auto_subjects = [
            "out of office", "automatic reply", "auto-reply",
            "delivery failure", "undeliverable"
        ]

        email_lower = email.lower()
        content_lower = content.lower()

        for pattern in automated_patterns:
            if pattern in email_lower:
                return True

        for subject in auto_subjects:
            if subject in content_lower:
                return True

        return False

    def _determine_ticket_status(
        self,
        ai_enabled: bool,
        ai_auto_respond: bool,
        confidence: float,
        confidence_threshold: float,
        escalate: bool,
        has_reply: bool
    ) -> str:
        """Determine appropriate ticket status"""
        if not ai_enabled:
            return "open"

        if escalate:
            return "escalated"

        if not has_reply:
            return "open"

        if ai_auto_respond and confidence >= confidence_threshold:
            return "ai_responded"

        if confidence >= 0.5:
            return "pending"  # Needs human review

        return "open"

    async def _create_ticket(
        self,
        brand_id: str,
        customer_email: str,
        customer_name: str,
        subject: str,
        message: str,
        channel: str,
        status: str,
        ai_response: Optional[str] = None,
        ai_confidence: Optional[float] = None,
        ai_sentiment: Optional[str] = None,
        ai_intent: Optional[str] = None,
        ai_reasoning: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a ticket in the database"""
        ticket_data = {
            "brand_id": brand_id,
            "customer_email": customer_email,
            "customer_name": customer_name,
            "subject": subject,
            "message": message,
            "channel": channel,
            "status": status,
            "priority": "normal"
        }

        if ai_response:
            ticket_data["ai_response"] = ai_response
            ticket_data["ai_responded_at"] = datetime.now(timezone.utc).isoformat()

        if ai_confidence is not None:
            ticket_data["ai_confidence"] = ai_confidence

        if ai_sentiment:
            ticket_data["ai_sentiment"] = ai_sentiment

        if ai_intent:
            ticket_data["ai_intent"] = ai_intent

        if ai_reasoning:
            ticket_data["ai_reasoning"] = ai_reasoning

        return supabase_insert("tickets", ticket_data)

    async def _update_ticket(self, ticket_id: str, updates: Dict[str, Any]) -> None:
        """Update a ticket"""
        supabase_update("tickets", {"id": f"eq.{ticket_id}"}, updates)

    async def _detect_and_create_action(
        self,
        brand_id: str,
        ticket_id: str,
        customer_email: str,
        customer_name: str,
        message: str,
        ai_confidence: float,
        ai_intent: str
    ) -> Optional[Dict[str, Any]]:
        """Detect actionable requests and create actions"""
        import re

        message_lower = message.lower()

        # Action detection patterns
        patterns = {
            "refund": [
                r'\b(refund|money back|return|reimburse)\b',
                r'\b(get my money|want a refund|full refund)\b'
            ],
            "cancel_order": [
                r'\b(cancel|cancellation)\s*(my|the|this)?\s*(order|purchase)\b',
                r'\b(don\'?t want|no longer want)\s*(the|this)?\s*(order|item)\b'
            ],
            "change_address": [
                r'\b(change|update|modify)\s*(my|the|shipping|delivery)?\s*address\b',
                r'\b(ship|send)\s*(to|it to)\s*a?\s*(different|new|another)\s*address\b'
            ]
        }

        detected_type = None
        for action_type, action_patterns in patterns.items():
            for pattern in action_patterns:
                if re.search(pattern, message_lower):
                    detected_type = action_type
                    break
            if detected_type:
                break

        if not detected_type:
            return None

        # Extract order info
        order_match = re.search(r'#?(\d{4,})', message)
        order_number = order_match.group(1) if order_match else None

        # Determine risk level
        risk_level = "low"
        if detected_type == "refund":
            risk_level = "medium"

        # Create action
        action_data = {
            "brand_id": brand_id,
            "ticket_id": ticket_id,
            "action_type": detected_type,
            "status": "pending",
            "customer_email": customer_email,
            "customer_name": customer_name,
            "order_number": order_number,
            "original_message": message[:1000],
            "ai_confidence": ai_confidence,
            "ai_reasoning": f"Detected intent: {ai_intent}",
            "risk_level": risk_level,
            "requires_approval": True
        }

        action = supabase_insert("actions", action_data)

        # Log action creation
        supabase_insert("action_logs", {
            "action_id": action["id"],
            "brand_id": brand_id,
            "event_type": "created",
            "details": {"source": "auto_detection", "intent": ai_intent}
        })

        return action

    async def _send_response(
        self,
        brand: Dict[str, Any],
        ticket_id: str,
        customer_email: str,
        subject: str,
        response: str
    ) -> bool:
        """Send email response to customer"""
        try:
            from production.channels.gmail_handler import gmail_handler

            # Format subject for reply
            reply_subject = subject if subject.startswith("Re:") else f"Re: {subject}"

            await gmail_handler.send_reply(
                to_email=customer_email,
                subject=reply_subject,
                body=response
            )

            logger.info(f"[BRAND-PROCESSOR] Email sent to {customer_email}")
            return True

        except Exception as e:
            logger.error(f"[BRAND-PROCESSOR] Failed to send email: {e}")
            return False

    async def _log_conversation(
        self,
        brand_id: str,
        ticket_id: str,
        user_message: str,
        ai_response: str,
        model: str
    ) -> None:
        """Log conversation for history"""
        try:
            # Log user message
            supabase_insert("ai_conversations", {
                "brand_id": brand_id,
                "ticket_id": ticket_id,
                "role": "user",
                "content": user_message,
                "model": model
            })

            # Log AI response
            if ai_response:
                supabase_insert("ai_conversations", {
                    "brand_id": brand_id,
                    "ticket_id": ticket_id,
                    "role": "assistant",
                    "content": ai_response,
                    "model": model
                })

        except Exception as e:
            logger.warning(f"[BRAND-PROCESSOR] Failed to log conversation: {e}")


# Global instance
brand_message_processor = BrandMessageProcessor()
