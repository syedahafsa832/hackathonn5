#!/usr/bin/env python3
"""
Email Polling Service
=====================
Polls every connected brand's Gmail inbox (per-brand OAuth).
Falls back to the global Gmail handler if no brands have Gmail connected.
"""
import asyncio
import logging
import os
import re
from typing import Dict, Any, List
from datetime import datetime, timezone, timedelta
from src.lib.supabase_client import supabase_select, supabase_update
from src.services.email_filter_service import email_filter_service
from src.services.email_guardian_service import email_guardian_service

logger = logging.getLogger(__name__)

AUTOMATED_KEYWORDS = [
    'no-reply', 'noreply', 'notifications', 'mailer-daemon',
    'accounts.google.com', 'linkedin.com', 'railway.app',
    'skool.com', 'apify.com', 'qdrant.io', 'openai.ai', 'openai.com',
    'facebookmail.com', 'twitter.com', 'github.com', 'florafauna.ai', 'neon.tech',
    'newsletter', 'marketing', 'digest', 'updates', 'community', 'pinterest',
    # Social / notification platforms
    'instagram.com', 'mail.instagram.com', 'facebook.com', 'tiktok.com',
    'youtube.com', 'snapchat.com', 'discord.com', 'slack.com',
    # Billing / SaaS auto-emails
    'stripe.com', 'paypal.com', 'shopify.com', 'mailchimp.com', 'sendgrid.net',
    # Cloud / infrastructure notifications
    'google.com', 'googleapis.com', 'aws.amazon.com', 'azure.com', 'microsoft.com',
    # Sales automation / outreach tools (AI-to-AI loop sources)
    'apollo.io', 'outreach.io', 'salesloft.com', 'lemlist.com', 'reply.io',
    'klenty.com', 'woodpecker.co', 'yesware.com', 'mailshake.com', 'gmass.co',
    'hubspot.com', 'salesforce.com', 'mixmax.com', 'boomerangapp.com',
]
AUTOMATED_PREFIXES = [
    'hello@', 'info@', 'news@', 'newsletter@', 'community@', 'marketing@', 'digest@',
    'donotreply@', 'do-not-reply@', 'noreply@', 'no-reply@', 'notifications@',
    'support@apollo', 'outreach@', 'sales@', 'team@apollo', 'hello@apollo',
]
MARKETING_INDICATORS = [
    'unsubscribe', 'manage preferences', 'view in browser',
    'privacy policy', 'opt out', 'sent this email to',
    'subscription', 'click here to',
]
AUTO_REPLY_PHRASES = [
    'this is an automated', 'this is an automatic', 'auto-reply', 'automatic reply',
    'out of the office', 'i am out of office', 'i am currently out', 'i will be out',
    'do not reply to this email', 'please do not reply', 'this email was sent automatically',
    "you're receiving this because", "you received this email because",
    'this message was sent by an automated system',
]


def _is_automated(sender_email: str, body: str, headers: dict = None) -> bool:
    s = sender_email.lower()
    b = body.lower()
    if any(kw in s for kw in AUTOMATED_KEYWORDS):
        return True
    if any(s.startswith(p) for p in AUTOMATED_PREFIXES):
        return True
    if any(ind in b for ind in MARKETING_INDICATORS):
        return True
    if any(phrase in b for phrase in AUTO_REPLY_PHRASES):
        return True
    if "customer success ai agent" in b:
        return True
    # Check RFC auto-reply headers if provided
    if headers:
        h = {k.lower(): v.lower() for k, v in headers.items()}
        auto_submitted = h.get("auto-submitted", "")
        if auto_submitted and auto_submitted != "no":
            return True
        if h.get("x-autoreply") or h.get("x-autorespond"):
            return True
        precedence = h.get("precedence", "")
        if precedence in ("bulk", "list", "auto-reply", "junk"):
            return True
        if h.get("list-unsubscribe") or h.get("list-id"):
            return True
    return False


class EmailPoller:
    def __init__(self, poll_interval: int = None, processor=None):
        self.poll_interval = poll_interval or int(os.getenv("EMAIL_POLL_INTERVAL", "15"))
        self.running = False
        self.processor = processor
        self._csat_loop_counter = 0
        self._csat_every_n = max(1, 1800 // self.poll_interval)  # ~30 min

    async def start(self):
        import logging as _logging
        if not _logging.root.handlers:
            _logging.basicConfig(
                level=_logging.INFO,
                format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
            )
        logger.info(f"Starting Email Poller with {self.poll_interval}s interval...")
        self.running = True
        await self._polling_loop()

    async def stop(self):
        self.running = False

    async def _polling_loop(self):
        while self.running:
            try:
                await self._poll_all_inboxes()
            except Exception as e:
                logger.error(f"Email polling error: {e}")
                await asyncio.sleep(5)

            self._csat_loop_counter += 1
            if self._csat_loop_counter >= self._csat_every_n:
                self._csat_loop_counter = 0
                try:
                    await self._send_csat_surveys()
                except Exception as e:
                    logger.error(f"[CSAT] Survey send failed: {e}")

            await asyncio.sleep(self.poll_interval)

    # ── Main dispatch ──────────────────────────────────────────────────────

    async def _poll_all_inboxes(self):
        """
        Poll every brand that has Gmail connected.
        Falls back to the single global Gmail handler if none are set up.
        """
        try:
            from src.services.brand_gmail_service import brand_gmail_service
            brands = brand_gmail_service.get_connected_brands()
        except Exception as e:
            logger.error(f"Could not load connected brands: {e}")
            brands = []

        if brands:
            # Build set of ALL brand Gmail addresses once per cycle.
            # Any email whose sender matches one of these is an AI outbound reply
            # that was delivered back into another brand's inbox — skip it to
            # prevent cross-brand AI-to-AI loops.
            all_brand_emails = frozenset(
                b.get("gmail_email", "").lower()
                for b in brands
                if b.get("gmail_email")
            )
            for brand in brands:
                try:
                    await self._poll_brand_inbox(brand, all_brand_emails)
                except Exception as e:
                    logger.error(f"[Poller] Brand {brand.get('id')} poll failed: {e}")
        else:
            # No brands with Gmail connected — use legacy global handler
            await self._poll_global_inbox()

    # ── Per-brand polling ──────────────────────────────────────────────────

    async def _poll_brand_inbox(self, brand: dict, all_brand_emails: frozenset = frozenset()):
        try:
            from src.services.brand_gmail_service import brand_gmail_service

            brand_id = brand["id"]
            support_email = (brand.get("support_email") or "").lower()

            # Determine last_polled_at; fall back to 24h ago if NULL
            last_polled_at = brand.get("last_polled_at")
            if last_polled_at:
                since_dt = datetime.fromisoformat(last_polled_at.replace("Z", "+00:00"))
            else:
                since_dt = datetime.now(timezone.utc) - timedelta(hours=24)
                logger.info(f"[Poller] Brand {brand.get('name')}: last_polled_at is NULL, falling back to 24h ago ({since_dt.isoformat()})")

            # Gmail search expects a date (YYYY/MM/DD) after which to search, not a Unix timestamp.
            since_str = since_dt.strftime('%Y/%m/%d')
            gmail_query = f"in:inbox after:{since_str}"
            logger.info(f"[POLL] Brand {brand.get('id')} ({brand.get('gmail_email')}): last_polled_at = {last_polled_at}")
            logger.info(f"[POLL] Gmail query: {gmail_query}")

            try:
                emails = await brand_gmail_service.get_new_emails(brand, max_results=50, since_dt=since_dt)
            except Exception as fetch_err:
                logger.error(f"[Poller] Gmail fetch failed for brand '{brand.get('name')}': {fetch_err} — skipping last_polled_at update to retry next cycle")
                return
            logger.info(f"[POLL] Messages found: {len(emails)}")

            for email in emails:
                sender = email["sender_email"].lower()
                thread_id = email.get("thread_id")
                gmail_msg_id = email.get("id")

                # Skip emails whose sender is ANY connected brand Gmail address.
                # The AI sends replies FROM brand addresses; when those replies land in
                # another brand's inbox the old per-brand check missed them, causing
                # cross-brand infinite Re: loops (brand A replies → lands in brand B's
                # inbox → brand B replies → lands in brand A's inbox → ...).
                if sender in all_brand_emails:
                    logger.info(f"[Poller] Skipping brand-owned address email from {sender} — cross-brand loop prevention")
                    continue

                # Skip deep reply chains (Re: Re: Re: ≥ 3) — second line of loop defence
                subject = email.get("subject", "")
                re_count = subject.lower().count("re:")
                if re_count >= 3:
                    logger.info(f"[Poller] Skipping deep reply chain (Re: count={re_count}): {subject[:60]}")
                    continue

                # Skip if this exact Gmail message was already stored (survives restarts)
                if gmail_msg_id:
                    try:
                        already_seen = supabase_select("tickets", {"gmail_message_id": f"eq.{gmail_msg_id}"})
                        if already_seen:
                            logger.debug(f"[Poller] Skipping already-processed message {gmail_msg_id}")
                            continue
                    except Exception:
                        pass  # column may not exist yet — safe to continue

                # ── Filter evaluation (runs before any ticket or AI work) ──
                email["brand_support_email"] = support_email
                filter_result = email_filter_service.evaluate(email, brand_id)
                email_filter_service.log_decision(brand_id, sender, thread_id, filter_result)

                if filter_result.decision == "blocked":
                    logger.info(
                        f"[Poller] Blocked: {sender} → reason={filter_result.reason} "
                        f"(brand: {brand['name']})"
                    )
                    continue

                # ── Guardian evaluation (Layers 4–5: AI intent + confidence gate) ──
                guardian_result = email_guardian_service.evaluate(email, brand_id)
                email_guardian_service.log_guardian_decision(brand_id, sender, thread_id, guardian_result)

                if guardian_result.decision in ("blocked", "quarantined"):
                    logger.info(
                        f"[Poller] Guardian {guardian_result.decision}: {sender} "
                        f"reason={guardian_result.reason} classification={guardian_result.classification}"
                    )
                    continue

                auto_reply_enabled = guardian_result.auto_reply_enabled

                # ── Thread match: append to existing ticket ──────────────
                existing_ticket = None
                if thread_id:
                    try:
                        results = supabase_select("tickets", {"gmail_thread_id": f"eq.{thread_id}"})
                        if results:
                            existing_ticket = results[0]
                            logger.info(f"[Poller] Thread match: appending to ticket {existing_ticket['id']}")
                    except Exception as te:
                        logger.warning(f"[Poller] Thread lookup failed: {te}")

                if existing_ticket:
                    # Stop processing threads already flagged as loop risk
                    if existing_ticket.get("loop_risk"):
                        logger.info(
                            f"[Poller] Loop-risk thread {thread_id} — suppressing further processing"
                        )
                        continue

                    current_msgs = existing_ticket.get("messages") or []
                    current_msgs.append({
                        "from":        email.get("sender_email"),
                        "body":        email.get("body"),
                        "received_at": datetime.now(timezone.utc).isoformat(),
                        "direction":   "inbound",
                    })
                    supabase_update("tickets", {"id": f"eq.{existing_ticket['id']}"}, {
                        "messages":   current_msgs,
                        "status":     "open",
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    })
                    continue

                # ── New ticket ───────────────────────────────────────────
                payload = {
                    "channel":            "email",
                    "content":            email["body"],
                    "customer_email":     email["sender_email"],
                    "customer_name":      email["sender_name"],
                    "subject":            email["subject"],
                    "timestamp":          datetime.now(timezone.utc).isoformat(),
                    "store_id":           brand_id,
                    "brand_name":         brand.get("name", ""),
                    "gmail_thread_id":    thread_id,
                    "gmail_message_id":   gmail_msg_id,
                    # Classification fields from filter service
                    "email_category":     filter_result.email_category,
                    "sender_type":        filter_result.sender_type,
                    # Guardian flag — suppresses AI email reply when False
                    "auto_reply_enabled": auto_reply_enabled,
                }

                if self.processor:
                    await self.processor.process_message("email_incoming", payload)
                    logger.info(f"[Poller] Processed email from {sender} → brand '{brand['name']}'")

            # Update last_polled_at after processing all emails in this batch
            try:
                supabase_update("brands", {"id": f"eq.{brand_id}"}, {
                    "last_polled_at": datetime.now(timezone.utc).isoformat(),
                })
                logger.debug(f"[Poller] Updated last_polled_at for brand '{brand.get('name')}'")
            except Exception as ts_err:
                logger.warning(f"[Poller] Could not update last_polled_at for brand {brand_id}: {ts_err}")

        except Exception as e:
            logger.error(f"[Poller] Error polling brand '{brand.get('name')}': {e}")

    # ── CSAT surveys ──────────────────────────────────────────────────────

    async def _send_csat_surveys(self):
        """Send a one-question satisfaction survey to tickets resolved 30-60 min ago."""
        try:
            from src.services.brand_gmail_service import brand_gmail_service
            now = datetime.now(timezone.utc)
            window_start = (now - timedelta(minutes=60)).isoformat()
            window_end = (now - timedelta(minutes=30)).isoformat()

            # Fetch recently resolved tickets
            resolved = supabase_select("tickets", {
                "status": "in.(auto_resolved,resolved)",
                "email_sent": "is.true",
                "updated_at": f"gte.{window_start}",
            })
            if not resolved:
                return

            for ticket in resolved:
                # Skip if CSAT already sent (column may not exist yet — handled by KeyError)
                if ticket.get("csat_sent"):
                    continue
                # Skip tickets outside the 30-60 min window
                updated = ticket.get("updated_at", "")
                if not updated or updated < window_start or updated > window_end:
                    continue

                brand_id = ticket.get("store_id") or ticket.get("brand_id")
                customer_email = ticket.get("customer_email")
                thread_id = ticket.get("gmail_thread_id")
                subject = ticket.get("subject") or "Your inquiry"
                if not brand_id or not customer_email or not thread_id:
                    continue

                brands = supabase_select("brands", {"id": f"eq.{brand_id}", "gmail_connected": "is.true"})
                if not brands:
                    continue
                brand = brands[0]

                brand_name = brand.get("name", "us")
                csat_body = (
                    f"Hey,\n\n"
                    f"Just following up — was your issue resolved to your satisfaction?\n\n"
                    f"Reply with:\n"
                    f"YES — all sorted, thanks!\n"
                    f"NO — still need help\n\n"
                    f"Your feedback helps us improve.\n\n"
                    f"Luna\n{brand_name}"
                )

                try:
                    await brand_gmail_service.send_reply_in_thread(
                        brand=brand,
                        to_email=customer_email,
                        subject=f"Re: {subject}",
                        body=csat_body,
                        thread_id=thread_id,
                    )
                    # Mark csat_sent — safe if column doesn't exist yet
                    try:
                        supabase_update("tickets", {"id": f"eq.{ticket['id']}"}, {"csat_sent": True})
                    except Exception:
                        pass
                    logger.info(f"[CSAT] Sent survey for ticket {ticket['id']} to {customer_email}")
                except Exception as e:
                    logger.warning(f"[CSAT] Could not send survey for ticket {ticket['id']}: {e}")
        except Exception as e:
            logger.error(f"[CSAT] _send_csat_surveys error: {e}")

    # ── Legacy global inbox fallback ───────────────────────────────────────

    async def _poll_global_inbox(self):
        try:
            from production.channels.gmail_handler import gmail_handler
            result = await gmail_handler.process_new_emails()

            if result["count"] == 0:
                return

            support_email = os.getenv("SUPPORT_EMAIL_ADDRESS", "").lower()

            for email in result["emails"]:
                sender = email["sender_email"].lower()
                if sender == support_email:
                    continue
                if _is_automated(sender, email["body"]):
                    logger.info(f"[Poller] Skipping automated email from {sender}")
                    continue

                payload = {
                    "channel":        "email",
                    "content":        email["body"],
                    "customer_email": email["sender_email"],
                    "customer_name":  email["sender_name"],
                    "subject":        email["subject"],
                    "timestamp":      datetime.now(timezone.utc).isoformat(),
                }

                if self.processor:
                    await self.processor.process_message("email_incoming", payload)

        except Exception as e:
            logger.error(f"[Poller] Global inbox error: {e}")
