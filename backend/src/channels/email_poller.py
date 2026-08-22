#!/usr/bin/env python3
"""
Email Polling Service
=====================
Polls every connected brand's Gmail inbox (per-brand OAuth).
Falls back to the global Gmail handler if no brands have Gmail connected.
"""
import asyncio
import json
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
            brand_count = 0
            try:
                brand_count = await self._poll_all_inboxes()
            except Exception as e:
                logger.exception("Email polling error")
                await asyncio.sleep(5)

            self._csat_loop_counter += 1
            if self._csat_loop_counter >= self._csat_every_n:
                self._csat_loop_counter = 0
                try:
                    await self._send_csat_surveys()
                except Exception as e:
                    logger.error(f"[CSAT] Survey send failed: {e}")

            # Few brands (e.g. live testing) → poll faster since there's headroom
            sleep_interval = 10 if 0 < brand_count <= 2 else self.poll_interval
            await asyncio.sleep(sleep_interval)

    # ── Main dispatch ──────────────────────────────────────────────────────

    async def _poll_all_inboxes(self) -> int:
        """
        Poll every brand that has Gmail connected, concurrently — Gmail API calls are
        I/O-bound, so running them in parallel cuts worst-case per-brand latency without
        adding real CPU load. Falls back to the single global Gmail handler if none are set up.
        Returns the number of brands polled (used to pace the outer loop's sleep interval).
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

            async def _poll_one(brand):
                try:
                    await self._poll_brand_inbox(brand, all_brand_emails)
                except Exception as e:
                    logger.exception(f"[Poller] Brand {brand.get('id')} poll failed")

            await asyncio.gather(*[_poll_one(b) for b in brands])
            return len(brands)
        else:
            # No brands with Gmail connected — use legacy global handler
            await self._poll_global_inbox()
            return 0

    # ── Per-brand polling ──────────────────────────────────────────────────

    async def _get_required_interval(self, brand: dict) -> int:
        """Founding-cohort (free) brands poll at half the configured frequency."""
        try:
            tenant_id = brand.get("tenant_id")
            if not tenant_id:
                return self.poll_interval
            tenants = await asyncio.to_thread(supabase_select, "tenants", {"id": f"eq.{tenant_id}"})
            if tenants and tenants[0].get("plan") == "founding_free":
                return self.poll_interval * 2
        except Exception:
            pass
        return self.poll_interval

    async def _poll_brand_inbox(self, brand: dict, all_brand_emails: frozenset = frozenset()):
        try:
            from src.services.brand_gmail_service import brand_gmail_service

            brand_id = brand["id"]
            support_email = (brand.get("support_email") or "").lower()

            # Determine last_polled_at; fall back to 24h ago if NULL
            last_polled_at = brand.get("last_polled_at")

            # Founding-cohort (free) brands poll at half frequency — they're capped at
            # 5 tickets/day anyway, no need to burn Gmail quota at the paid-tier cadence.
            required_interval = await self._get_required_interval(brand)
            if last_polled_at:
                try:
                    last_dt = datetime.fromisoformat(last_polled_at.replace("Z", "+00:00"))
                    elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
                    if elapsed < required_interval:
                        return
                except Exception:
                    pass

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
            processed_count = 0
            failure_count = 0

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

                # Skip emails containing our own reply signature — third, independent
                # line of loop defence. Catches cases the sender-address and Re:-count
                # guards above both miss: an auto-forwarder, ticketing tool, or
                # third-party integration that echoes our own reply back to us without
                # preserving the original sender address or bumping the Re: count.
                agent_name = brand.get("agent_name") or "Luna"
                signature_marker = f"— {agent_name}"
                if signature_marker in (email.get("body") or ""):
                    logger.info(f"[Poller] Skipping email containing our own reply signature ('{signature_marker}') — loop prevention")
                    continue

                # Skip if this exact Gmail message was already stored (survives restarts)
                if gmail_msg_id:
                    try:
                        already_seen = await asyncio.to_thread(
                            supabase_select, "tickets", {"gmail_message_id": f"eq.{gmail_msg_id}"}
                        )
                        if already_seen:
                            logger.debug(f"[Poller] Skipping already-processed message {gmail_msg_id}")
                            continue
                    except Exception:
                        pass  # column may not exist yet — safe to continue

                # ── Filter evaluation (runs before any ticket or AI work) ──
                logger.info(f"[email_filter] evaluating gmail_message_id={gmail_msg_id} sender={sender} brand={brand.get('name')}")
                email["brand_support_email"] = support_email
                filter_result = await asyncio.to_thread(email_filter_service.evaluate, email, brand_id)
                await asyncio.to_thread(email_filter_service.log_decision, brand_id, sender, thread_id, filter_result)

                if filter_result.decision == "blocked":
                    logger.info(
                        f"[email_filter] rejected gmail_message_id={gmail_msg_id} sender={sender} "
                        f"reason={filter_result.reason} (brand: {brand['name']})"
                    )
                    continue

                # ── Guardian evaluation (Layers 4–5: AI intent + confidence gate) ──
                guardian_result = await asyncio.to_thread(
                    email_guardian_service.evaluate, email, brand_id, brand_name=brand.get("name")
                )
                await asyncio.to_thread(
                    email_guardian_service.log_guardian_decision, brand_id, sender, thread_id, guardian_result
                )

                if guardian_result.decision in ("blocked", "quarantined"):
                    logger.info(
                        f"[email_filter] {guardian_result.decision} gmail_message_id={gmail_msg_id} sender={sender} "
                        f"reason={guardian_result.reason} classification={guardian_result.classification}"
                    )
                    continue

                logger.info(
                    f"[email_filter] accepted gmail_message_id={gmail_msg_id} sender={sender} "
                    f"classification={guardian_result.classification} confidence={guardian_result.confidence:.2f}"
                )

                auto_reply_enabled = guardian_result.auto_reply_enabled

                # ── Thread-risk check only — the actual thread-match/append
                # decision now lives entirely in message_processor.py's own
                # STAGE 1.5, which (unlike this poller previously) continues
                # on into AI generation instead of silently appending the
                # message and stopping. A same-thread customer reply used to
                # never receive any AI response at all because of that early
                # stop — this poller and message_processor.py each did their
                # own separate thread-match check, and only the poller's
                # (append-then-continue-the-loop, no AI call) actually ran.
                if thread_id:
                    try:
                        results = await asyncio.to_thread(
                            supabase_select, "tickets", {"gmail_thread_id": f"eq.{thread_id}"}
                        )
                        if results and results[0].get("loop_risk"):
                            logger.info(
                                f"[Poller] Loop-risk thread {thread_id} — suppressing further processing"
                            )
                            continue
                    except Exception as te:
                        logger.warning(f"[Poller] Thread risk lookup failed (continuing): {te}")

                # ── New ticket, or thread continuation (message_processor.py
                # appends to the existing ticket and generates a real reply
                # for it — see STAGE 1.5 there) ──────────────────────────
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
                    try:
                        result = await self.processor.process_message("email_incoming", payload)
                        if isinstance(result, dict) and result.get("status") == "error":
                            failure_count += 1
                            logger.error(f"[Poller] Processor error for brand {brand_id} message {gmail_msg_id}: {result.get('error')}")
                        else:
                            processed_count += 1
                            ticket_id = result.get("ticket_id") if isinstance(result, dict) else None
                            logger.info(
                                f"[email_filter] ticket_created ticket_id={ticket_id} gmail_message_id={gmail_msg_id} "
                                f"sender={sender} brand='{brand['name']}'"
                            )
                    except Exception:
                        failure_count += 1
                        logger.exception(f"[Poller] Processor exception for brand {brand_id} message {gmail_msg_id}")

            logger.info(f"[POLL] Brand {brand_id} summary: fetched={len(emails)} processed={processed_count} failures={failure_count}")

            # Update last_polled_at after processing all emails in this batch
            try:
                await asyncio.to_thread(
                    supabase_update, "brands", {"id": f"eq.{brand_id}"}, {
                        "last_polled_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                logger.debug(f"[Poller] Updated last_polled_at for brand '{brand.get('name')}'")
            except Exception as ts_err:
                logger.warning(f"[Poller] Could not update last_polled_at for brand {brand_id}: {ts_err}")

        except Exception as e:
            logger.error(f"[Poller] Error polling brand '{brand.get('name')}': {e}")

    # ── CSAT surveys ──────────────────────────────────────────────────────

    @staticmethod
    def _build_csat_email(ticket_id: str, brand_name: str) -> tuple:
        """(plain_text, html) for the tap-a-star CSAT email. Pure/testable —
        no I/O, no ticket/brand lookups here. Each star links straight to
        GET /widget/feedback/rate?ticket_id=...&stars=N&token=... (see
        v2_chat_widget.py) — tapping one records that rating immediately,
        no reply-parsing needed (the old YES/NO version never actually
        recorded a customer's reply anywhere)."""
        from src.api.routes.v2_chat_widget import star_rating_email_url

        star_links = "\n".join(
            f"{'⭐' * n}  —  {star_rating_email_url(ticket_id, n)}" for n in range(1, 6)
        )
        plain = (
            f"Hey!\n\n"
            f"How did we do?\n\n"
            f"Tap a star to rate your experience:\n\n"
            f"{star_links}\n\n"
            f"Takes two seconds and really helps us out. Thank you!\n\n"
            f"Luna\n{brand_name}"
        )

        star_rows_html = "".join(
            f'<tr><td style="padding:4px 0;"><a href="{star_rating_email_url(ticket_id, n)}" '
            f'style="display:block;text-decoration:none;font-size:22px;letter-spacing:4px;'
            f'padding:10px 16px;border-radius:10px;background:#FFFBF5;">{"⭐" * n}</a></td></tr>'
            for n in range(1, 6)
        )
        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f5;">
<tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:420px;width:100%;background:#ffffff;border-radius:16px;">
<tr><td style="padding:40px 32px;text-align:center;">
<div style="font-size:15px;color:#374151;margin-bottom:4px;">Hey! 👋</div>
<h1 style="font-size:20px;font-weight:700;color:#1F2937;margin:0 0 4px;">How did we do?</h1>
<p style="font-size:13px;color:#9CA3AF;margin:0 0 20px;">Tap a star to rate your experience.</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{star_rows_html}</table>
<p style="font-size:12.5px;color:#9CA3AF;margin:20px 0 0;">Takes two seconds and really helps us out. Thank you!</p>
</td></tr>
</table>
</td></tr>
</table>
</body></html>"""
        return plain, html

    # Post-resolution buffer: long enough that the customer has actually seen
    # the resolution before being asked to rate it, short enough that the
    # poller (running every _CSAT_POLL_INTERVAL) reliably catches every
    # ticket at least once. Same spirit as the old 30-60min window, now keyed
    # off the real resolved_at signal instead of a status-string heuristic.
    _CSAT_MIN_AGE = timedelta(minutes=30)
    _CSAT_MAX_AGE = timedelta(minutes=90)
    _CSAT_CUSTOMER_COOLDOWN = timedelta(days=30)

    @staticmethod
    def _is_ticket_csat_eligible(ticket: Dict[str, Any], now: datetime) -> bool:
        """Pure eligibility check - deterministic, no AI call, no I/O.
        Genuinely resolved (resolved_at set, see message_processor.py /
        v2_tickets.py::close_ticket), inside the post-resolution window,
        not already sent, and not an abandoned/one-sided exchange (needs at
        least a customer message and a reply for feedback to make sense)."""
        if ticket.get("csat_sent"):
            return False
        resolved_at_raw = ticket.get("resolved_at")
        if not resolved_at_raw:
            return False
        try:
            resolved_at = datetime.fromisoformat(resolved_at_raw.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return False
        age = now - resolved_at
        if age < EmailPoller._CSAT_MIN_AGE or age > EmailPoller._CSAT_MAX_AGE:
            return False

        messages = ticket.get("messages") or []
        if isinstance(messages, str):
            try:
                messages = json.loads(messages)
            except (ValueError, TypeError):
                messages = []
        if len(messages) < 2:
            return False  # abandoned / one-sided - nothing to rate

        return bool(ticket.get("customer_email") and ticket.get("gmail_thread_id") and
                    (ticket.get("store_id") or ticket.get("brand_id")))

    @staticmethod
    async def _customer_in_csat_cooldown(customer_email: str, brand_id: str, now: datetime) -> bool:
        """Has this customer (scoped to this brand - a different merchant's
        survey to the same address is a separate relationship) already
        received a CSAT survey within the last 30 days, for any ticket?"""
        cutoff = (now - EmailPoller._CSAT_CUSTOMER_COOLDOWN).isoformat()
        recent = await asyncio.to_thread(supabase_select, "tickets", {
            "customer_email": f"eq.{customer_email}",
            "store_id": f"eq.{brand_id}",
            "csat_sent_at": f"gte.{cutoff}",
            "limit": "1",
        })
        return bool(recent)

    @staticmethod
    async def _claim_csat_send(ticket_id: str, now: datetime) -> bool:
        """Atomically claim this ticket for sending - conditioned on
        csat_sent still being false, exactly like actions_service.py's
        approve_action() claims a pending action before touching Shopify.
        Closes the race between two overlapping poller runs (or a slow
        first run still finishing when the next poll fires) double-sending
        the same survey."""
        claimed = await asyncio.to_thread(
            supabase_update, "tickets",
            {"id": f"eq.{ticket_id}", "csat_sent": "is.false"},
            {"csat_sent": True, "csat_sent_at": now.isoformat()},
        )
        return bool(claimed)

    async def _send_csat_surveys(self):
        """Send a one-question satisfaction survey once a ticket is
        genuinely resolved - never repeatedly, never for an abandoned
        conversation, at most one per customer per 30 days. Deterministic
        eligibility only - no AI/model call anywhere in this path."""
        try:
            from src.services.brand_gmail_service import brand_gmail_service
            now = datetime.now(timezone.utc)
            window_start = (now - self._CSAT_MAX_AGE).isoformat()

            candidates = await asyncio.to_thread(supabase_select, "tickets", {
                "channel": "eq.email",
                "csat_sent": "is.false",
                "resolved_at": f"gte.{window_start}",
            })
            if not candidates:
                return

            for ticket in candidates:
                if not self._is_ticket_csat_eligible(ticket, now):
                    continue

                brand_id = ticket.get("store_id") or ticket.get("brand_id")
                customer_email = ticket.get("customer_email")
                thread_id = ticket.get("gmail_thread_id")
                subject = ticket.get("subject") or "Your inquiry"

                if await self._customer_in_csat_cooldown(customer_email, brand_id, now):
                    continue

                brands = await asyncio.to_thread(
                    supabase_select, "brands", {"id": f"eq.{brand_id}", "gmail_connected": "is.true"}
                )
                if not brands:
                    continue
                brand = brands[0]

                # Claim before sending, not after - a send that fails after a
                # successful claim just doesn't get retried this cycle (safe
                # failure mode: at most one survey per ticket, never zero
                # protection against a genuine double-send).
                if not await self._claim_csat_send(ticket["id"], now):
                    continue

                brand_name = brand.get("name", "us")
                plain_body, html_body = self._build_csat_email(ticket["id"], brand_name)

                try:
                    await brand_gmail_service.send_html_reply_in_thread(
                        brand=brand,
                        to_email=customer_email,
                        subject=f"Re: {subject}",
                        html_body=html_body,
                        plain_text_body=plain_body,
                        thread_id=thread_id,
                    )
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





