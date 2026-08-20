import asyncio
import logging
import os
import re
import time
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
import uuid

from src.services.supabase_service import supabase_service
from src.agent.customer_success_agent import customer_success_agent
from src.lib.supabase_client import supabase_select, supabase_update
# _log_conversation lives on brand_message_processor.py's singleton (BrandMessageProcessor
# is stateless aside from a `running` flag - the method reads no self state), reused here
# rather than duplicated. This module (message_processor.py, wired to the real EmailPoller
# in main.py) is the actual live email path - brand_message_processor.py itself has no
# other caller in production, so ai_conversations was never being written for real email
# traffic despite the write logic existing there.
from src.workers.brand_message_processor import brand_message_processor

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
        t_start = time.monotonic()
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
            # If this email belongs to an existing Gmail thread, reuse that
            # ticket (append the message) instead of creating a new one.
            # Previously this returned early with no AI response at all for
            # every same-thread customer reply — a real conversation-memory
            # gap (and a support gap on its own): the customer's follow-up
            # was silently appended and never answered. Now it continues
            # into the same pipeline as a new ticket, just with an existing
            # early_ticket_id/early_ticket instead of a freshly created one.
            early_ticket_id = None
            early_ticket = None
            is_thread_continuation = False
            if gmail_thread_id and channel == "email":
                try:
                    existing = supabase_select("tickets", {"gmail_thread_id": f"eq.{gmail_thread_id}"})
                    if existing:
                        existing_ticket = existing[0]
                        early_ticket_id = existing_ticket.get("id")
                        is_thread_continuation = True
                        current_msgs = existing_ticket.get("messages") or []
                        current_msgs.append({
                            "from": customer_email, "body": content,
                            "received_at": datetime.now(timezone.utc).isoformat(), "direction": "inbound",
                        })
                        updates = {"messages": current_msgs, "updated_at": datetime.now(timezone.utc).isoformat()}
                        if existing_ticket.get("status") in ("closed", "resolved"):
                            updates["status"] = "open"
                        supabase_update("tickets", {"id": f"eq.{early_ticket_id}"}, updates)
                        early_ticket = {**existing_ticket, **updates}
                        logger.info(f"[PROCESSOR] Thread match found — appended message, continuing with ticket {early_ticket_id}")
                except Exception as thread_err:
                    logger.warning(f"[PROCESSOR] Thread check failed (treating as new ticket): {thread_err}")

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
            # Skipped entirely for a thread continuation — early_ticket(_id)
            # already points at the existing, just-updated ticket.
            if not is_thread_continuation:
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
            logger.info(f"[TIMING] Ticket intake: {time.monotonic() - t_start:.2f}s")

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

            # ========== STAGE 2.55: AI ENTITLEMENT (trial/subscription gate) ==========
            # Runs as early as possible once the tenant is known — before the
            # daily/AI-quota checks below, before customer resolution, before
            # any RAG/LLM/Shopify-tool work. An expired trial with no active
            # paid plan (or a lapsed paid plan) must never reach any of that:
            # check_limit(tenant_id, "ai_replies") below is a *quota* gate and
            # would otherwise silently let an expired trial through on the
            # "free" plan's own leftover daily allowance (the actual bug this
            # closes) — see plan_service.check_ai_entitlement's docstring.
            # The ticket itself (already created in STAGE 1.8) is left intact
            # and untouched here — only escalated to a human, never deleted —
            # so existing conversations/data are unaffected.
            from src.services import plan_service
            entitlement = plan_service.check_ai_entitlement(tenant_id)
            if not entitlement["allowed"]:
                logger.info(
                    f"[PROCESSOR] AI entitlement denied for tenant {tenant_id} "
                    f"(plan={entitlement['plan']} reason={entitlement['reason']}) — routing to human, no AI call made"
                )
                if early_ticket_id:
                    escalation_reason = (
                        "Your free trial has ended. Upgrade to a paid plan to resume AI-powered replies."
                        if entitlement["reason"] == "trial_expired"
                        else "No active AI plan on this account. Upgrade to enable AI-powered replies."
                    )
                    supabase_update("tickets", {"id": f"eq.{early_ticket_id}"}, {
                        "status": "requires_human",
                        "escalation_reason": escalation_reason,
                    })
                return {
                    "ticket_id": early_ticket_id,
                    "status": "trial_expired",
                    "entitlement": entitlement,
                }

            # ========== STAGE 2.6: PLAN / TRIAL / DAILY LIMIT ==========
            plan_service.record_email_processed(tenant_id)
            limit_check = plan_service.can_process_ticket(tenant_id)
            if not limit_check["allowed"]:
                logger.info(
                    f"[PROCESSOR] Daily ticket limit reached for tenant {tenant_id} "
                    f"(plan={limit_check['plan']} used={limit_check['used']}/{limit_check['limit']}) — queuing without AI reply"
                )
                if early_ticket_id:
                    supabase_update("tickets", {"id": f"eq.{early_ticket_id}"}, {
                        "status": "requires_human",
                        "escalation_reason": "Daily ticket limit reached. Upgrade to Pro for unlimited AI support.",
                    })
                    return {
                        "ticket_id": early_ticket_id,
                        "status": "daily_limit_reached",
                        "limit_info": limit_check,
                    }
            elif not is_thread_continuation:
                # A thread continuation reuses an already-counted ticket -
                # counting it again here would inflate daily/plan ticket
                # totals for what's really the same conversation.
                plan_service.record_ticket_created(tenant_id)

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

            # ========== STAGE 4.5: AI-REPLY QUOTA (trial-abuse protection) ==========
            # Checked before the LLM call, not after — the point is to not spend
            # the API call at all once a tenant is over its AI-reply cap. For
            # trial tenants this is now a lifetime total (25 across the whole
            # trial), not a daily allowance — see plan_service.PLAN_LIMITS.
            ai_limit_check = plan_service.check_limit(tenant_id, "ai_replies")
            if not ai_limit_check["allowed"]:
                logger.info(
                    f"[PROCESSOR] AI-reply limit reached for tenant {tenant_id} "
                    f"(plan={ai_limit_check['plan']} used={ai_limit_check['used']}/{ai_limit_check['limit']}) — routing to human"
                )
                if early_ticket_id:
                    is_trial_quota = ai_limit_check.get("reason") == "trial_limit_reached"
                    escalation_reason = (
                        "You've used all 25 AI replies included in your free trial. Upgrade to continue automating support."
                        if is_trial_quota
                        else "AI reply limit reached for your plan. Upgrade to continue automating support with unlimited replies."
                    )
                    supabase_update("tickets", {"id": f"eq.{early_ticket_id}"}, {
                        "status": "requires_human",
                        "escalation_reason": escalation_reason,
                    })
                    return {
                        "ticket_id": early_ticket_id,
                        "status": "ai_reply_limit_reached",
                        "limit_info": ai_limit_check,
                    }

            # ========== STAGE 5: AI ANALYSIS ==========
            # customer_info["history"] - _construct_v3_prompt() defaults this to
            # the literal string "New customer" whenever it's absent, which is
            # what it always was here: get_or_create_customer() returns a
            # customers-table row (id/email/name/phone) with no message
            # content at all. Now includes both the current ticket's own
            # messages AND up to _HISTORY_MAX_PRIOR_TICKETS earlier tickets
            # from this same customer email within this same store/brand
            # (never across brands - see _build_customer_history).
            _history = await self._build_customer_history(
                customer_email, store_id, early_ticket_id, (early_ticket or {}).get("messages"),
            )
            if _history:
                customer["history"] = _history

            t_ai_start = time.monotonic()
            logger.info(f"[PROCESSOR] Generating AI response (tenant_id={tenant_id})...")
            ai_result = await customer_success_agent.generate_channel_appropriate_response(
                query=content, customer_info=customer, channel=channel, tenant_id=tenant_id, store_id=store_id,
                ticket_id=early_ticket_id,
            )
            logger.info(f"[TIMING] AI generation (RAG+LLM): {time.monotonic() - t_ai_start:.2f}s")
            # ai_reply_generated is only set on the real model-generated path —
            # every fallback path (provider outage, empty response, JSON parse
            # error) returns the same canned "having trouble" reply_body
            # without it, so reply_body alone can't detect a real generation.
            # A failed AI call must never consume trial/plan quota.
            if ai_result.get("reply_body") and ai_result.get("ai_reply_generated"):
                plan_service.record_ai_reply_event(
                    tenant_id,
                    brand_id=store_id,
                    # message_processor.py's channel values are 'email' (Gmail
                    # poller + quarantine release) or 'web_form' — 'email' is
                    # renamed to 'gmail' to match the vocabulary used
                    # everywhere else (ai_reply_events, the dashboard's
                    # per-channel breakdown, v2_chat_widget.py's 'chat_widget').
                    channel="gmail" if channel == "email" else channel,
                    ticket_id=early_ticket_id,
                    customer_identifier=customer_email,
                    model_used=ai_result.get("model_used"),
                )

            confidence = ai_result.get("confidence_score", 0) / 100.0
            intent = ai_result.get("intent", "unknown")
            risk_level = ai_result.get("risk_level", "medium")
            reply_body = ai_result.get("reply_body", "")

            # ai_conversations persistence — real per-message token/latency usage.
            # early_ticket_id may be None on the very first message of a brand-new
            # ticket (created later in STAGE 6/9 below); in that case there's no
            # ticket_id yet to attach this row to, so it's skipped rather than
            # logged with a dangling reference — the same information (model/
            # usage) is still available via logs for that one message.
            if early_ticket_id:
                await brand_message_processor._log_conversation(
                    brand_id=store_id,
                    ticket_id=early_ticket_id,
                    user_message=content,
                    ai_response=reply_body,
                    model=ai_result.get("model_used") or "unknown",
                    usage=ai_result.get("ai_usage"),
                )

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
            routing = self._decide_ticket_routing(
                ai_mode=ai_mode,
                is_overridden=is_overridden,
                confidence=confidence,
                confidence_threshold=confidence_threshold,
                ai_flagged_escalate=ticket_payload["escalate"],
                risk_level=risk_level,
                reply_body=reply_body,
            )
            should_auto_reply = routing["should_auto_reply"]
            if routing["status"] is not None:
                ticket_payload["status"] = routing["status"]
            if routing.get("ai_reply") is not None:
                ticket_payload["ai_reply"] = routing["ai_reply"]
            if routing.get("ai_draft") is not None:
                ticket_payload["ai_draft"] = routing["ai_draft"]
            logger.info(f"[PROCESSOR] Routing decision: {routing['log_message']}")

            # ========== STAGE 9: UPDATE TICKET with AI results (was created at Stage 1.8) ==========
            logger.info(f"[PROCESSOR] Finalising ticket with status: {ticket_payload.get('status')}")
            if early_ticket_id:
                # "messages" is deliberately excluded here too: ticket_payload
                # rebuilds it (STAGE 6) as a fresh single-item array containing
                # only the current inbound message. For a thread continuation,
                # STAGE 1.5 already appended this message onto the ticket's
                # real accumulated history - overwriting it here would wipe
                # that history back down to one message on every single reply,
                # breaking both current-ticket history and any thread
                # continuation the moment it's ever read back.
                update_fields = {k: v for k, v in ticket_payload.items()
                                 if k not in ("store_id", "customer_email", "customer_name",
                                              "subject", "message", "channel", "gmail_thread_id",
                                              "email_category", "sender_type", "customer_sentiment",
                                              "tags", "messages")}
                update_fields["updated_at"] = datetime.now(timezone.utc).isoformat()
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
                t_send_start = time.monotonic()
                await self._send_email_with_logging(customer_email, subject, ai_result, ticket_id, store_id=store_id)
                logger.info(f"[TIMING] Email send: {time.monotonic() - t_send_start:.2f}s")
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
                    now_iso = datetime.now(timezone.utc).isoformat()
                    msg_direction = "outbound" if email_actually_sent else "draft"
                    existing_messages.append({
                        "from": "AI Agent",
                        "body": reply_body,
                        "sent_at": now_iso,
                        "direction": msg_direction,
                        "role": "assistant",
                    })
                    update = {"messages": existing_messages, "updated_at": now_iso}
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
            logger.info(f"[TIMING] Total pipeline: {time.monotonic() - t_start:.2f}s")
            return result

        except Exception as e:
            logger.error(f"[PROCESSOR] ERROR: {str(e)}", exc_info=True)
            logger.info(f"[TIMING] Total pipeline (errored): {time.monotonic() - t_start:.2f}s")
            return {"ticket_id": None, "status": "error", "error": str(e)}

    def _decide_ticket_routing(
        self, ai_mode: str, is_overridden: bool, confidence: float,
        confidence_threshold: float, ai_flagged_escalate: bool,
        risk_level: str, reply_body: str,
    ) -> Dict[str, Any]:
        """Pure decision logic for STAGE 8 (unsupported/ambiguous/failed/sensitive
        requests must escalate, not auto-reply as if resolved). Extracted from
        process_message() so the escalation rules can be unit tested directly
        without mocking Supabase/plan_service/the AI agent. Behavior is
        unchanged from the inline version this replaced."""
        if ai_mode in ("paused", "supervised"):
            return {
                "should_auto_reply": False, "status": "ai_suggested",
                "ai_draft": reply_body,
                "log_message": f"AI Mode {ai_mode} - storing draft only",
            }

        if ai_mode in ("active", "autopilot"):
            if is_overridden:
                return {
                    "should_auto_reply": False, "status": "human_managing",
                    "ai_draft": reply_body,
                    "log_message": "Human takeover active - suppressing reply",
                }

            if confidence >= confidence_threshold and not ai_flagged_escalate and risk_level == "low":
                return {
                    "should_auto_reply": True, "status": "auto_resolved",
                    "ai_reply": reply_body,
                    "log_message": f"AUTO-REPLY APPROVED - Confidence: {confidence:.0%}",
                }

            if confidence >= 0.5 and risk_level == "low" and not ai_flagged_escalate:
                return {
                    "should_auto_reply": True, "status": "auto_resolved_review",
                    "ai_reply": reply_body,
                    "log_message": f"AUTO-REPLY (needs review) - Confidence: {confidence:.0%}",
                }

            # MEDIUM/HIGH RISK or AI-flagged escalation -> human queue for the ACTION,
            # but the acknowledgment reply ("we'll review your request") is safe to send now.
            if reply_body and confidence >= 0.5:
                return {
                    "should_auto_reply": True, "status": "escalated",
                    "ai_reply": reply_body,
                    "log_message": f"Sending acknowledgment (escalated) - Confidence: {confidence:.0%}, Risk: {risk_level}",
                }
            return {
                "should_auto_reply": False, "status": "escalated",
                "ai_draft": reply_body,
                "log_message": f"Escalating (no send) - Confidence: {confidence:.0%}, Risk: {risk_level}",
            }

        # Unknown/unsupported ai_mode value - no branch above matched, so nothing
        # in ticket_payload gets set here; process_message's own default ("status"
        # from ticket_payload dict init) is preserved rather than guessing.
        return {"should_auto_reply": False, "status": None, "log_message": f"Unrecognized ai_mode={ai_mode!r} - no routing rule matched"}

    # Bounds for _build_customer_history — deliberately small and fixed
    # (not configurable per-tenant) so history stays a compact hint, never a
    # transcript dump. Tune here, not per-call, if these ever need changing.
    _HISTORY_MAX_PRIOR_TICKETS = 3
    _HISTORY_MAX_MESSAGES_PER_TICKET = 4
    _HISTORY_MAX_CHARS_PER_MESSAGE = 150
    _HISTORY_MAX_TOTAL_CHARS = 1500

    def _format_history_messages(self, messages: list, limit: int) -> List[str]:
        return [
            f"{'Customer' if m.get('direction') == 'inbound' else 'Support'}: {(m.get('body') or '')[:self._HISTORY_MAX_CHARS_PER_MESSAGE]}"
            for m in (messages or [])[-limit:]
        ]

    async def _build_customer_history(
        self, customer_email: str, store_id: str, current_ticket_id: Optional[str], current_ticket_messages: list,
    ) -> Optional[str]:
        """Compact, bounded conversation history for the prompt: the current
        ticket's own messages, plus up to _HISTORY_MAX_PRIOR_TICKETS earlier
        tickets from the SAME customer email AND SAME store/brand - tenant
        isolation is structural here (store_id is a hard filter in the
        query itself, not a post-filter), never cross-brand even when the
        same email exists under a different brand. Returns None when there
        is genuinely nothing to report (fresh customer, first message),
        so the prompt's existing "New customer" default stays honest
        rather than claiming false familiarity - never says "I remember"
        just because a customer row exists with no real message content.
        Uses the existing tickets/supabase_select architecture; no new
        memory store."""
        sections = []

        current_lines = self._format_history_messages(current_ticket_messages, 6)
        if current_lines:
            sections.append("Current conversation:\n" + "\n".join(current_lines))

        if customer_email and store_id:
            try:
                prior = supabase_select("tickets", {
                    "customer_email": f"eq.{customer_email}",
                    "store_id": f"eq.{store_id}",
                    "order": "updated_at.desc",
                    "limit": str(self._HISTORY_MAX_PRIOR_TICKETS + 1),  # +1: current ticket may be in this page
                })
                prior = [t for t in (prior or []) if t.get("id") != current_ticket_id][:self._HISTORY_MAX_PRIOR_TICKETS]
                for t in prior:
                    lines = self._format_history_messages(t.get("messages"), self._HISTORY_MAX_MESSAGES_PER_TICKET)
                    if not lines:
                        continue
                    subject = t.get("subject") or "prior conversation"
                    sections.append(f"Previous conversation ({subject}):\n" + "\n".join(lines))
            except Exception as e:
                logger.warning(f"[PROCESSOR] Prior-ticket history lookup failed (non-blocking): {e}")

        if not sections:
            return None

        history = "\n\n".join(sections)
        if len(history) > self._HISTORY_MAX_TOTAL_CHARS:
            history = history[:self._HISTORY_MAX_TOTAL_CHARS] + "..."
        return history

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


