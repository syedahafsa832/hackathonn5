import asyncio
import logging
import os
import re
from typing import Dict, Any, Optional
from datetime import datetime, timezone
import uuid

from src.services.supabase_service import supabase_service
from src.agent.customer_success_agent import customer_success_agent
from src.lib.supabase_client import supabase_select, supabase_update

# SaaS Multi-Tenant Action Detection (optional - fails gracefully if not set up)
try:
    from src.services.actions_service import actions_service
    ACTIONS_SERVICE_AVAILABLE = True
except ImportError:
    ACTIONS_SERVICE_AVAILABLE = False

logger = logging.getLogger(__name__)

class UnifiedMessageProcessor:
    """Process incoming messages from all channels through the FTE agent directly using Supabase."""

    def __init__(self):
        self.running = False

    async def start(self):
        logger.info("Starting Unified Message Processor (Supabase Mode)...")
        self.running = True
        # Keep-alive loop — actual processing happens when process_message() is called
        while self.running:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[PROCESSOR] Unexpected error in keep-alive loop: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def stop(self):
        logger.info("Stopping Unified Message Processor...")
        self.running = False

    async def process_message(self, topic: str, message: Dict[str, Any]):
        """
        Process incoming message with AI operational modes and human takeover logic.
        UNIFIED PIPELINE: Works for BOTH email and webform channels.
        """
        try:
            # ========== STAGE 1: EXTRACT & VALIDATE ==========
            channel = message.get('channel', 'web_form')
            content = message.get('content', '')
            customer_email = message.get('customer_email', '').strip().lower()
            customer_name = message.get('customer_name', message.get('name', 'Unknown'))
            subject = message.get('subject', 'New Support Request')
            store_id = message.get('store_id', '00000000-0000-0000-0000-000000000000')
            gmail_thread_id = message.get('gmail_thread_id')
            gmail_message_id = message.get('gmail_message_id')
            email_category = message.get('email_category', 'unknown')
            sender_type = message.get('sender_type', 'unknown')
            auto_reply_enabled = message.get('auto_reply_enabled', True)

            logger.info(f"[PROCESSOR] ========== NEW MESSAGE ==========")
            logger.info(f"[PROCESSOR] Channel: {channel}")
            logger.info(f"[PROCESSOR] Customer: {customer_email}")
            logger.info(f"[PROCESSOR] Subject: {subject}")
            logger.info(f"[PROCESSOR] Content: {content[:100]}...")

            if not customer_email:
                logger.warning("[PROCESSOR] Message missing customer email, cannot process.")
                return {"ticket_id": None, "status": "error", "error": "Missing email"}

            # Filter automated emails
            if self._is_automated(customer_email, content):
                logger.info(f"[PROCESSOR] Skipping automated email from {customer_email}")
                return {"ticket_id": None, "status": "skipped", "reason": "automated"}

            # ========== STAGE 1.5: EMAIL THREAD DEDUP ==========
            # If this email belongs to an existing Gmail thread, append to existing ticket
            if gmail_thread_id and channel == "email":
                try:
                    existing = supabase_select("tickets", {"gmail_thread_id": f"eq.{gmail_thread_id}"})
                    if existing:
                        existing_ticket = existing[0]
                        ticket_id = existing_ticket.get("id")
                        logger.info(f"[PROCESSOR] Thread match found — appending to ticket {ticket_id}")
                        # Reopen if closed, update timestamp
                        updates = {"updated_at": datetime.now(timezone.utc).isoformat()}
                        if existing_ticket.get("status") in ("closed", "resolved"):
                            updates["status"] = "open"
                        supabase_update("tickets", {"id": f"eq.{ticket_id}"}, updates)
                        return {"ticket_id": ticket_id, "status": "thread_appended"}
                except Exception as thread_err:
                    logger.warning(f"[PROCESSOR] Thread check failed (continuing): {thread_err}")

            # ========== STAGE 1.6: ORDER DETECTION ==========
            detected_order_id = None
            order_match = re.search(r'#?(\d{4,6})', content)
            if order_match:
                detected_order_id = order_match.group(1)
                logger.info(f"[PROCESSOR] Detected order ID: {detected_order_id}")

            # ========== STAGE 1.7: SENTIMENT + AUTO-TAG ==========
            customer_sentiment = self._detect_email_sentiment(content, subject)
            ticket_tags = self._auto_tag_ticket(content, subject)
            logger.info(f"[PROCESSOR] Sentiment: {customer_sentiment}, Tags: {ticket_tags}")

            # ========== STAGE 1.8: CREATE TICKET IMMEDIATELY (visible in dashboard now) ==========
            early_ticket_id = None
            try:
                early_ticket = await supabase_service.create_ticket({
                    "store_id": store_id,
                    "customer_email": customer_email,
                    "customer_name": customer_name,
                    "subject": subject,
                    "message": content,
                    "messages": [{"from": customer_email, "body": content,
                                  "received_at": datetime.now(timezone.utc).isoformat(), "direction": "inbound"}],
                    "channel": channel,
                    "status": "processing",
                    "gmail_thread_id": gmail_thread_id,
                    "gmail_message_id": gmail_message_id,
                    "detected_order_id": detected_order_id,
                    "customer_sentiment": customer_sentiment,
                    "tags": ticket_tags,
                    "email_category": email_category,
                    "sender_type": sender_type,
                })
                early_ticket_id = early_ticket.get("id") if early_ticket else None
                logger.info(f"[PROCESSOR] ✓ Ticket created immediately: {early_ticket_id} (status=processing)")
            except Exception as early_err:
                logger.warning(f"[PROCESSOR] Early ticket creation failed (non-blocking): {early_err}")

            # ========== STAGE 2: SYSTEM SETTINGS ==========
            settings = await supabase_service.get_system_settings(store_id)
            ai_mode = settings.get('ai_mode', 'active')
            confidence_threshold = settings.get('confidence_threshold', 0.65)
            logger.info(f"[PROCESSOR] AI Mode: {ai_mode}, Confidence Threshold: {confidence_threshold}")

            # ========== STAGE 2.5: TENANT LOOKUP (Multi-Tenant SaaS) ==========
            # Try to find tenant by support email or from message metadata
            tenant_id = message.get("tenant_id")
            if not tenant_id:
                try:
                    # 1. Best source: the brand's own tenant_id (email-triggered tickets always have store_id)
                    if not tenant_id and store_id and store_id != "00000000-0000-0000-0000-000000000000":
                        try:
                            brand_rows = supabase_select("brands", {"id": f"eq.{store_id}"})
                            if brand_rows and brand_rows[0].get("tenant_id"):
                                tenant_id = brand_rows[0]["tenant_id"]
                                logger.info(f"[PROCESSOR] Tenant found via brand: {tenant_id}")
                        except Exception:
                            pass

                    # 2. Try to match by the "to" address in the message
                    if not tenant_id:
                        to_email = message.get("to_email", "").lower().strip()
                        if to_email:
                            tenants = supabase_select("tenants", {"support_email": f"eq.{to_email}"})
                            if tenants:
                                tenant_id = tenants[0].get("id")
                                logger.info(f"[PROCESSOR] Tenant found by to_email: {tenant_id}")

                    # 3. Try SUPPORT_EMAIL_ADDRESS env var
                    if not tenant_id:
                        support_email = os.getenv("SUPPORT_EMAIL_ADDRESS", "").lower()
                        if support_email:
                            tenants = supabase_select("tenants", {"support_email": f"eq.{support_email}"})
                            if tenants:
                                tenant_id = tenants[0].get("id")
                                logger.info(f"[PROCESSOR] Tenant found by env support email: {tenant_id}")

                    # 4. Last resort: first active tenant (single-tenant dev/staging only)
                    # WARNING: in multi-tenant production this will pick the wrong tenant
                    # if steps 1-3 failed, which means store_id has no tenant_id set — run migration 019
                    if not tenant_id:
                        tenants = supabase_select("tenants", {"is_active": "eq.true"})
                        if tenants:
                            tenant_id = tenants[0].get("id")
                            logger.warning(f"[PROCESSOR] Using default tenant fallback: {tenant_id} — brand {store_id} has no tenant_id set, run migration 019")

                except Exception as tenant_error:
                    logger.warning(f"[PROCESSOR] Tenant lookup failed: {tenant_error}")

            # ========== STAGE 3: RESOLVE CUSTOMER ==========
            customer = await supabase_service.get_or_create_customer(
                email=customer_email,
                store_id=store_id,
                name=customer_name
            )
            logger.info(f"[PROCESSOR] Customer resolved: {customer.get('id', 'unknown')}")

            # ========== STAGE 4: MANUAL MODE CHECK ==========
            if ai_mode == "manual":
                logger.info(f"[PROCESSOR] AI Mode is MANUAL. Updating ticket to requires_human.")
                if early_ticket_id:
                    supabase_update("tickets", {"id": f"eq.{early_ticket_id}"},
                                    {"status": "requires_human"})
                    return {"ticket_id": early_ticket_id, "status": "requires_human"}
                ticket = await supabase_service.create_ticket({
                    "store_id": store_id,
                    "customer_name": customer_name,
                    "customer_email": customer_email,
                    "subject": subject,
                    "message": content,
                    "messages": [{"from": customer_email, "body": content,
                                  "received_at": datetime.now(timezone.utc).isoformat(), "direction": "inbound"}],
                    "status": "requires_human",
                    "channel": channel,
                    "email_category": email_category,
                    "sender_type": sender_type,
                })
                return {"ticket_id": ticket.get("id"), "status": "requires_human"}

            # ========== STAGE 5: AI ANALYSIS ==========
            logger.info(f"[PROCESSOR] Generating AI response (tenant_id={tenant_id})...")
            ai_result = await customer_success_agent.generate_channel_appropriate_response(
                query=content, customer_info=customer, channel=channel, tenant_id=tenant_id, store_id=store_id,
                ticket_id=early_ticket_id,
            )

            confidence = ai_result.get("confidence_score", 0) / 100.0
            intent = ai_result.get("intent", "unknown")
            risk_level = ai_result.get("risk_level", "medium")
            reply_body = ai_result.get("reply_body", "")

            logger.info(f"[PROCESSOR] AI Result - Intent: {intent}, Confidence: {confidence:.0%}, Risk: {risk_level}")
            logger.info(f"[PROCESSOR] AI Reply Preview: {reply_body[:100]}..." if reply_body else "[PROCESSOR] No reply generated")

            # ========== STAGE 6: PREPARE TICKET ==========
            ticket_payload = {
                "store_id": store_id,
                "customer_name": customer_name,
                "customer_email": customer_email,
                "subject": subject,
                "message": content,
                "messages": [{
                    "from": customer_email,
                    "body": content,
                    "received_at": datetime.now(timezone.utc).isoformat(),
                    "direction": "inbound",
                }],
                "channel": channel,
                "intent": intent,
                "sentiment": ai_result.get("sentiment"),
                "risk_level": risk_level,
                "confidence_score": ai_result.get("confidence_score"),
                "escalate": ai_result.get("escalate", False),
                "escalation_reason": ai_result.get("escalation_reason"),
                "detected_order_id": detected_order_id,
                "gmail_thread_id": gmail_thread_id,
                "gmail_message_id": gmail_message_id,
                "email_category": email_category,
                "sender_type": sender_type,
                "customer_sentiment": customer_sentiment,
                "tags": ticket_tags,
            }

            # ========== STAGE 7: HUMAN OVERRIDE CHECK (THREAD-SPECIFIC ONLY) ==========
            is_overridden = False
            # IMPORTANT: Only check override for EMAIL channel replies in same thread
            # Webform submissions are ALWAYS new conversations, never blocked by override
            if channel == "email":
                is_overridden = await self._check_thread_override(customer_email, subject)

            if is_overridden:
                logger.info(f"[PROCESSOR] Human override active for email thread: {subject}")

            # ========== STAGE 8: DECISION LOGIC ==========
            should_auto_reply = False

            if ai_mode in ("paused", "supervised"):
                logger.info(f"[PROCESSOR] AI Mode {ai_mode} - storing draft only")
                ticket_payload["ai_draft"] = reply_body
                ticket_payload["status"] = "ai_suggested"

            elif ai_mode in ("active", "autopilot"):
                if is_overridden:
                    logger.info(f"[PROCESSOR] Human takeover active - suppressing reply")
                    ticket_payload["status"] = "human_managing"
                    ticket_payload["ai_draft"] = reply_body  # Store as draft anyway

                elif confidence >= confidence_threshold and not ticket_payload["escalate"] and risk_level == "low":
                    # AUTO-REPLY: High confidence, low risk, no escalation
                    should_auto_reply = True
                    ticket_payload["ai_reply"] = reply_body
                    ticket_payload["status"] = "auto_resolved"
                    logger.info(f"[PROCESSOR] ✓ AUTO-REPLY APPROVED - Confidence: {confidence:.0%}")

                elif confidence >= 0.5 and risk_level == "low" and not ticket_payload["escalate"]:
                    # MEDIUM CONFIDENCE + LOW RISK only: send but flag for review
                    should_auto_reply = True
                    ticket_payload["ai_reply"] = reply_body
                    ticket_payload["status"] = "auto_resolved_review"
                    logger.info(f"[PROCESSOR] ✓ AUTO-REPLY (needs review) - Confidence: {confidence:.0%}")

                else:
                    # MEDIUM/HIGH RISK or AI-flagged escalation → human queue for the ACTION
                    # but the acknowledgment reply ("we'll review your request") is safe to send now.
                    ticket_payload["status"] = "escalated"
                    if reply_body and confidence >= 0.5:
                        # Send the acknowledgment; the financial action still needs approval.
                        should_auto_reply = True
                        ticket_payload["ai_reply"] = reply_body
                        logger.info(f"[PROCESSOR] ✓ Sending acknowledgment (escalated) - Confidence: {confidence:.0%}, Risk: {risk_level}")
                    else:
                        ticket_payload["ai_draft"] = reply_body
                        logger.info(f"[PROCESSOR] Escalating (no send) - Confidence: {confidence:.0%}, Risk: {risk_level}")

            # ========== STAGE 9: UPDATE TICKET with AI results (was created at Stage 1.8) ==========
            logger.info(f"[PROCESSOR] Finalising ticket with status: {ticket_payload.get('status')}")
            if early_ticket_id:
                update_fields = {k: v for k, v in ticket_payload.items()
                                 if k not in ("store_id", "customer_email", "customer_name",
                                              "subject", "message", "channel", "gmail_thread_id",
                                              "email_category", "sender_type", "customer_sentiment", "tags")}
                supabase_update("tickets", {"id": f"eq.{early_ticket_id}"}, update_fields)
                ticket_id = early_ticket_id
                logger.info(f"[PROCESSOR] ✓ Ticket updated: {ticket_id} → {ticket_payload.get('status')}")
            else:
                ticket = await supabase_service.create_ticket(ticket_payload)
                ticket_id = ticket.get('id') if ticket else None
                logger.info(f"[PROCESSOR] ✓ Ticket created (fallback): {ticket_id}")

            # ========== STAGE 9.5: ACTION DETECTION (Multi-Tenant SaaS) ==========
            # Detect actions (refund, cancel, address change) and create in queue
            # NOTE: tenant_id is already resolved from Stage 2.5 — do not overwrite with message.get()

            try:
                if ACTIONS_SERVICE_AVAILABLE and tenant_id:
                    action_result = await actions_service.detect_and_create(
                        tenant_id=tenant_id,
                        customer_email=customer_email,
                        customer_name=customer_name,
                        message=content,
                        ai_analysis={"reasoning": ai_result.get("intent", ""), "confidence": confidence},
                        brand_id=store_id,
                        ticket_id=ticket_id,
                    )
                    if action_result and action_result.get("success"):
                        logger.info(f"[Stage 9.5] Action detection completed for ticket {ticket_id}: {action_result.get('action_id')} ({action_result.get('action_type')})")
                    else:
                        logger.info(f"[Stage 9.5] Action detection completed for ticket {ticket_id}: no actionable request detected")
                else:
                    logger.warning(f"[Stage 9.5] Skipping action detection: ACTIONS_SERVICE_AVAILABLE={ACTIONS_SERVICE_AVAILABLE}, tenant_id={tenant_id}")
                    # Inline fallback using AI intent detector
                    if tenant_id:
                        try:
                            from src.services.intent_detector import intent_detector as _idet
                            from src.lib.supabase_client import supabase_insert as _sb_insert
                            _intent = await _idet.detect(content)
                            if _intent.has_action:
                                _atype_map = {'cancel': 'cancel_order', 'refund': 'refund', 'address_change': 'change_address', 'reship': 'reship'}
                                _atype = _atype_map.get(_intent.action_type)
                                if _atype and _intent.order_id:  # Never create without order_id
                                    _sb_insert("actions", {
                                        "tenant_id": tenant_id,
                                        "action_type": _atype,
                                        "customer_email": customer_email,
                                        "customer_name": customer_name,
                                        "order_id": _intent.order_id,
                                        "original_message": content[:500],
                                        "status": "pending",
                                        "confidence": _intent.confidence,
                                        "ai_reasoning": f"Inline detection: {_intent.action_type} (source={_intent.source})",
                                        "ticket_id": ticket_id,
                                    })
                                    logger.info(f"[Stage 9.5 fallback] Created {_atype} action via intent_detector")
                        except Exception as _fe:
                            logger.warning(f"[Stage 9.5 fallback] Intent detection error: {_fe}")
            except Exception as e:
                logger.error(f"[Stage 9.5] Action detection failed: {e}", exc_info=True)

            # ========== STAGE 10: SEND EMAIL RESPONSE ==========
            email_actually_sent = False
            if should_auto_reply and reply_body and auto_reply_enabled:
                logger.info(f"[PROCESSOR] Sending email to {customer_email}...")
                await self._send_email_with_logging(customer_email, subject, ai_result, ticket_id, store_id=store_id)
                email_actually_sent = True
            else:
                logger.info(f"[PROCESSOR] Email NOT sent - should_auto_reply={should_auto_reply}, has_reply={bool(reply_body)}, auto_reply_enabled={auto_reply_enabled}")

            # Always append AI reply to messages so conversation replay is complete,
            # whether or not the email was actually sent.
            # direction="outbound" = sent; direction="draft" = generated but not emailed.
            if reply_body and ticket_id:
                try:
                    ticket_rows = supabase_select("tickets", {"id": f"eq.{ticket_id}"})
                    settings_rows = supabase_select("system_settings", {"store_id": f"eq.{store_id}"})
                    if not settings_rows:
                        settings_rows = supabase_select("system_settings", {"store_id": "eq.00000000-0000-0000-0000-000000000000"})
                    max_replies = 2
                    if settings_rows and settings_rows[0].get("max_auto_replies") is not None:
                        max_replies = settings_rows[0]["max_auto_replies"]
                    current_count = 0
                    existing_messages = []
                    if ticket_rows:
                        current_count = ticket_rows[0].get("auto_reply_count") or 0
                        existing_messages = list(ticket_rows[0].get("messages") or [])
                    msg_direction = "outbound" if email_actually_sent else "draft"
                    existing_messages.append({
                        "from": "AI Agent",
                        "body": reply_body,
                        "sent_at": datetime.now(timezone.utc).isoformat(),
                        "direction": msg_direction,
                    })
                    update = {"messages": existing_messages}
                    if email_actually_sent:
                        new_count = current_count + 1
                        update["auto_reply_count"] = new_count
                        update["loop_risk"] = new_count >= max_replies
                        logger.info(f"[PROCESSOR] auto_reply_count={new_count}, loop_risk={new_count >= max_replies}")
                    supabase_update("tickets", {"id": f"eq.{ticket_id}"}, update)
                except Exception as loop_err:
                    logger.warning(f"[PROCESSOR] messages append failed (non-blocking): {loop_err}")

            # ========== STAGE 11: RETURN RESULT ==========
            result = {
                "ticket_id": ticket_id,
                "status": ticket_payload.get("status"),
                "email_sent": should_auto_reply and bool(reply_body) and auto_reply_enabled
            }
            logger.info(f"[PROCESSOR] ========== COMPLETE: {result} ==========")
            return result

        except Exception as e:
            logger.error(f"[PROCESSOR] ERROR: {str(e)}", exc_info=True)
            return {"ticket_id": None, "status": "error", "error": str(e)}

    async def _check_thread_override(self, customer_email: str, subject: str) -> bool:
        """Check if there's an active human override for this specific email thread."""
        try:
            overrides = supabase_select("conversation_overrides", {"active": "eq.true"})
            if not overrides:
                return False

            for ov in overrides:
                convo_id = ov.get("conversation_id")
                if not convo_id:
                    continue

                ov_ticket = await supabase_service.get_ticket_by_id(convo_id)
                if not ov_ticket:
                    continue

                # Must match BOTH email AND subject to be considered same thread
                if ov_ticket.get("customer_email") == customer_email:
                    ov_subject = (ov_ticket.get("subject") or "").lower().strip()
                    current_subject = subject.lower().strip()

                    # Exact match or "Re:" thread match
                    if (ov_subject == current_subject or
                        ov_subject.replace("re:", "").strip() == current_subject.replace("re:", "").strip()):
                        return True
            return False
        except Exception as e:
            logger.warning(f"[PROCESSOR] Override check failed: {e}")
            return False  # Fail open - don't block on error

    def _parse_confidence(self, text: str) -> int:
        """Extract CONFIDENCE: XX from AI response text. Returns 0-100 int."""
        m = re.search(r'CONFIDENCE:\s*(\d+)', text, re.IGNORECASE)
        if m:
            return max(0, min(100, int(m.group(1))))
        return 0

    def _parse_staged_actions(self, text: str) -> list:
        """Extract [STAGE_ACTION: type=X, ...] tags from AI response text."""
        pattern = r'\[STAGE_ACTION:\s*([^\]]+)\]'
        actions = []
        for match in re.finditer(pattern, text):
            raw = match.group(1)
            parts = dict(p.strip().split('=', 1) for p in raw.split(',') if '=' in p)
            if parts.get('type'):
                actions.append(parts)
        return actions

    def _detect_email_sentiment(self, content: str, subject: str = "") -> str:
        """Keyword-based sentiment: angry/frustrated/positive/neutral. No LLM needed."""
        text = (content + " " + subject).lower()
        angry = ["scam", "terrible", "worst", "fraud", "lawsuit", "never again", "furious",
                 "disgusting", "outrageous", "!!!", "demand", "unacceptable ridiculous"]
        frustrated = ["still waiting", "never received", "ridiculous", "not okay",
                      "very disappointed", "not happy", "where is my", "still no", "week ago",
                      "3 days", "no response", "no one"]
        positive = ["thank", "love it", "amazing", "great service", "wonderful", "perfect",
                    "appreciate", "happy with", "fantastic"]
        if any(s in text for s in angry):
            return "angry"
        if any(s in text for s in frustrated):
            return "frustrated"
        if any(s in text for s in positive):
            return "positive"
        return "neutral"

    def _auto_tag_ticket(self, content: str, subject: str = "") -> list:
        """Keyword-based auto-categorization. Returns up to 3 tags."""
        text = (content + " " + subject).lower()
        tag_rules = {
            "refund": ["refund", "money back", "charge back", "return my money"],
            "cancel": ["cancel", "cancellation", "don't want", "stop the order"],
            "shipping": ["shipping", "delivery", "tracking", "shipment", "not arrived",
                         "where is my", "still waiting", "delayed", "lost package"],
            "exchange": ["exchange", "swap", "different size", "wrong item", "wrong color"],
            "damaged": ["damaged", "broken", "defective", "arrived broken", "not working"],
            "complaint": ["terrible", "worst", "unacceptable", "disappointed", "bad experience"],
            "question": ["how do i", "can you", "is it possible", "do you", "what is", "how long"],
            "compliment": ["thank you", "love it", "amazing", "great", "wonderful"],
        }
        tags = [tag for tag, keywords in tag_rules.items() if any(kw in text for kw in keywords)]
        return tags[:3]

    def _is_automated(self, email: str, content: str) -> bool:
        """Filter automated/marketing/outreach emails to prevent AI-to-AI loops."""
        sender = email.lower()
        body = content.lower()
        auto_sender_kw = [
            'no-reply', 'noreply', 'notifications', 'mailer-daemon',
            'newsletter', 'marketing', 'unsubscribe', 'apollo.io',
            'outreach.io', 'salesloft.com', 'lemlist.com', 'donotreply',
            'do-not-reply', 'automated@', 'auto@',
        ]
        auto_body_phrases = [
            'this is an automated', 'this is an automatic', 'auto-reply',
            'automatic reply', 'out of the office', 'do not reply to this',
            'please do not reply', 'this email was sent automatically',
            'unsubscribe', 'you are receiving this',
        ]
        if any(kw in sender for kw in auto_sender_kw):
            return True
        if any(phrase in body for phrase in auto_body_phrases):
            return True
        return False

    async def _send_email_with_logging(self, email: str, subject: str, ai_result: Dict[str, Any], ticket_id: str, store_id: str = None):
        """Send email reply — uses brand Gmail if store_id has one, else falls back to global."""
        try:
            logger.info(f"[EMAIL] ========== SENDING EMAIL ==========")
            logger.info(f"[EMAIL] To: {email}, Ticket: {ticket_id}, store_id: {store_id}")

            reply_body = ai_result.get("reply_body", "")
            reply_subject = ai_result.get("reply_subject", f"Re: {subject}")

            if not reply_body:
                logger.error(f"[EMAIL] FAILED - No reply body to send")
                return

            result = None
            default_store = "00000000-0000-0000-0000-000000000000"

            # Use brand Gmail if the ticket belongs to a real brand
            if store_id and store_id != default_store:
                try:
                    from src.services.brand_gmail_service import brand_gmail_service
                    from src.lib.supabase_client import supabase_select
                    brands = supabase_select("brands", {"id": f"eq.{store_id}", "gmail_connected": "is.true"})
                    if brands:
                        brand = brands[0]
                        logger.info(f"[EMAIL] Sending via brand Gmail: {brand.get('gmail_email')}")
                        result = await brand_gmail_service.send_email(brand, email, reply_subject, reply_body)
                        if result.get("success"):
                            result = {"status": "sent", "id": result.get("id")}
                        else:
                            logger.warning(f"[EMAIL] Brand Gmail send failed: {result.get('error')} — falling back to global")
                            result = None
                except Exception as brand_err:
                    logger.warning(f"[EMAIL] Brand Gmail error: {brand_err} — falling back to global")
                    result = None

            # No brand Gmail connected — skip sending
            if result is None:
                logger.warning(f"[EMAIL] No brand Gmail connected for store {store_id} — reply NOT sent. Connect Gmail in Brands settings.")
                return

            if result.get('status') == 'sent':
                logger.info(f"[EMAIL] ✓ SUCCESS - Email sent to {email}, ID: {result.get('id')}")
                try:
                    supabase_update("tickets", {"id": f"eq.{ticket_id}"}, {
                        "email_sent": True,
                        "email_sent_at": datetime.now(timezone.utc).isoformat()
                    })
                except Exception as update_err:
                    logger.warning(f"[EMAIL] Could not update ticket email_sent flag: {update_err}")
                # Set first_response_at if this is the first reply
                try:
                    t_rows = supabase_select("tickets", {"id": f"eq.{ticket_id}"})
                    if t_rows and not t_rows[0].get("first_response_at"):
                        supabase_update("tickets", {"id": f"eq.{ticket_id}"}, {
                            "first_response_at": datetime.now(timezone.utc).isoformat()
                        })
                except Exception:
                    pass
            else:
                logger.error(f"[EMAIL] FAILED - {result.get('error', 'Unknown error')}")

        except ImportError as e:
            logger.error(f"[EMAIL] FAILED - Gmail handler import error: {e}")
        except Exception as e:
            logger.error(f"[EMAIL] FAILED - Exception: {e}", exc_info=True)

    async def send_email_response(self, email: str, subject: str, ai_result: Dict[str, Any]):
        """Legacy method - redirects to new logging version."""
        await self._send_email_with_logging(email, subject, ai_result, "legacy")


# Global instance
message_processor = UnifiedMessageProcessor()
