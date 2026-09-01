"""
Email Guardian Service — Layers 4 & 5
======================================
Layer 4: AI intent classification via Mistral API.
Layer 5: Confidence gate — low-confidence customer_support emails are quarantined.

Fires AFTER email_filter_service (Layers 1–3). Only called for emails with decision="allowed".
Fail-open: any exception in evaluate() returns GUARDIAN_ALLOW so a real customer is never lost.
"""
import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from bs4 import BeautifulSoup, Comment

from src.lib.supabase_client import supabase_insert, supabase_select
from src.services.ai_provider_manager import ai_provider_manager, AllProvidersFailedError

logger = logging.getLogger(__name__)

DEFAULT_STORE = "00000000-0000-0000-0000-000000000000"


def _html_to_preview_text(raw: str, max_len: int = 200) -> str:
    """Reduce a raw email body (often full HTML — DOCTYPE, <style>, Outlook VML
    conditional comments, etc.) to a short, readable plain-text preview.
    Safe to call on already-plain-text input too — BeautifulSoup just returns
    it unchanged (aside from whitespace collapsing) when there's no markup."""
    if not raw:
        return ""
    try:
        soup = BeautifulSoup(raw, "html.parser")
        for tag in soup(["style", "script", "head"]):
            tag.decompose()
        # get_text() includes HTML comments (Comment is a NavigableString
        # subclass) — without this, Outlook's <!--[if mso]>...VML...<![endif]-->
        # conditional-comment blocks leak straight into the "clean" preview.
        for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
            comment.extract()
        text = soup.get_text(separator=" ")
    except Exception:
        # Malformed markup BeautifulSoup can't parse — fall back to a blunt
        # tag strip rather than showing raw markup.
        text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]

VALID_CLASSIFICATIONS = {
    "customer_support", "promotion", "newsletter",
    "outreach", "spam", "automation", "unknown",
}

# Only these classifications are definitively NOT customer support and should be blocked.
# "unknown" is intentionally excluded — when the AI can't decide, we let it through.
BLOCKED_CLASSIFICATIONS = {"promotion", "newsletter", "outreach", "spam", "automation"}

CLASSIFIER_PROMPT = """You are screening inbound email for the support inbox of "{brand_name}", a Shopify store.

Classify the email into exactly one of these categories:
customer_support, promotion, newsletter, outreach, spam, automation, unknown

Also decide: is this email actually addressed to "{brand_name}" as one of ITS customers
(an order, product, shipping, refund, or account question about buying from {brand_name})?
An email that merely contains the word "support" — e.g. a receipt, registration confirmation,
or notification from a DIFFERENT company or service — is NOT relevant, even if it reads as
formal/transactional. Only mark relevant=true if the email is genuinely about {brand_name}
as a business the sender bought from or is asking to buy from.

Respond with valid JSON only:
{{"classification": "<label>", "confidence": <0.0-1.0>, "relevant": <true|false>}}

Subject: {subject}

Body:
{body}"""


@dataclass
class GuardianResult:
    decision: str            # "allowed" | "blocked" | "quarantined"
    classification: str      # Mistral classification label
    confidence: float        # 0.0–1.0
    reason: Optional[str]    # "ai_classification" | "low_confidence" | None
    quarantine_id: Optional[str]
    auto_reply_enabled: bool


GUARDIAN_ALLOW = GuardianResult(
    decision="allowed",
    classification="customer_support",
    confidence=1.0,
    reason=None,
    quarantine_id=None,
    auto_reply_enabled=True,
)


class EmailGuardianService:

    # ── T003: Settings loader ────────────────────────────────────────────────

    def _load_settings(self, brand_id: str) -> dict:
        """Load guardian settings for brand; falls back to global defaults on any error."""
        defaults = {
            "support_only_mode": True,
            "confidence_threshold": 0.75,
            "auto_reply_enabled": True,
        }
        try:
            rows = supabase_select("system_settings", {"store_id": f"eq.{brand_id}"})
            if not rows and brand_id != DEFAULT_STORE:
                rows = supabase_select("system_settings", {"store_id": f"eq.{DEFAULT_STORE}"})
            if rows:
                r = rows[0]
                for key in defaults:
                    if key in r and r[key] is not None:
                        defaults[key] = r[key]
        except Exception as e:
            logger.warning(f"[Guardian] Failed to load settings for {brand_id}: {e} — using defaults")
        return defaults

    # ── T005: AI classifier ──────────────────────────────────────────────────

    async def _classify_email(self, subject: str, body: str, brand_name: str = "our store") -> tuple[str, float, bool]:
        """Classify email intent via the shared ai_provider_manager (Mistral
        primary + fallback keys, then Groq — the same failover chain every
        other AI call in this app goes through). Returns
        (classification, confidence, relevant_to_brand).

        This used to build its own single-key OpenAI client with zero
        failover. That meant a single broken key/model (e.g. a subscription
        tier that doesn't include the configured model — confirmed live via
        a 403 "model not available in your subscription tier" on every
        single call) made EVERY email hit the except-block fallback below —
        (unknown, 0.0, False) — which unconditionally quarantines. Legitimate
        order-status, cancellation, address-change, and refund requests were
        all being quarantined for this exact reason, 100% of the time,
        because the classifier itself could never succeed, not because any
        of those messages were actually ambiguous. Routing through
        ai_provider_manager (already used by the main agent and
        intent_detector) means a single dead key/model no longer takes the
        classifier down — it fails over to the next configured provider
        first, same as everywhere else.
        """
        if not ai_provider_manager.has_providers:
            # No classifier available — genuinely uncertain. Per explicit product
            # requirement: uncertain means quarantine, not auto-allow. relevant=False
            # + confidence=0.0 routes this to the quarantine branch in evaluate().
            return ("unknown", 0.0, False)

        prompt = CLASSIFIER_PROMPT.format(
            brand_name=brand_name or "our store",
            subject=(subject or "")[:500],
            body=(body or "")[:2000],
        )
        call_start = time.monotonic()

        try:
            # ai_provider_manager already retries once per-provider without
            # response_format if a provider rejects it, then fails over to
            # the next configured provider — no need to duplicate that here.
            response, provider_label, model, usage = await ai_provider_manager.create_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                response_format={"type": "json_object"},
            )

            raw = response.choices[0].message.content or ""
            data = json.loads(raw)
            classification = str(data.get("classification", "unknown")).lower()
            confidence = float(data.get("confidence", 0.0))

            if classification not in VALID_CLASSIFICATIONS:
                classification = "unknown"
            confidence = max(0.0, min(1.0, confidence))
            # Default relevant=True when the model omits the field, so a missing
            # key never turns into a silent false-positive block.
            relevant = bool(data.get("relevant", True))

            latency_ms = round((time.monotonic() - call_start) * 1000)
            logger.info(
                f"[Guardian] Classifier → {classification} ({confidence:.2f}) relevant={relevant} "
                f"provider={provider_label} model={model} tokens={usage.get('total_tokens')} latency_ms={latency_ms}"
            )
            return (classification, confidence, relevant)

        except AllProvidersFailedError as e:
            logger.warning(f"[Guardian] All AI providers failed: {e}")
            # Every configured key/model failed — genuinely uncertain, so
            # quarantine rather than auto-allow. See has_providers branch above.
            return ("unknown", 0.0, False)
        except Exception as e:
            logger.warning(f"[Guardian] Classifier response parse error: {e}")
            return ("unknown", 0.0, False)

    # ── T006: Quarantine record creation ─────────────────────────────────────

    def _create_quarantine_record(
        self,
        brand_id: str,
        email: dict,
        classification: str,
        confidence: float,
        status: str = "pending",
    ) -> Optional[str]:
        """Insert a quarantine/decision record. Returns the record id
        (existing or new) or None on error. `status="pending"` (default) is
        a genuine quarantine awaiting merchant review; `status="auto_blocked"`
        persists an outright-blocked decision purely so it's never
        re-classified (see _find_existing_decision below) - it never appears
        in the merchant's review queue.

        Dedupes on gmail_message_id first: Gmail's `after:` search operator
        is date-level, not time-level, so the same message keeps reappearing
        in every poll for the rest of that calendar day. Without this check,
        a single low-confidence email produces a fresh quarantine row on
        every ~15s poll cycle for hours — this is what actually happened in
        production (one email, 458 duplicate rows)."""
        gmail_message_id = email.get("id")
        if gmail_message_id:
            try:
                existing = supabase_select("email_quarantine", {
                    "brand_id": f"eq.{brand_id}",
                    "gmail_message_id": f"eq.{gmail_message_id}",
                })
                if existing:
                    return existing[0].get("id")
            except Exception as e:
                logger.warning(f"[Guardian] Quarantine dedup check failed, proceeding to insert: {e}")

        try:
            row = supabase_insert("email_quarantine", {
                "brand_id":          brand_id,
                "sender_email":      email.get("sender_email") or email.get("customer_email", ""),
                "subject":           email.get("subject", ""),
                "body_preview":      _html_to_preview_text(email.get("body") or email.get("content", "")),
                "thread_id":         email.get("thread_id"),
                "gmail_message_id":  gmail_message_id,
                "ai_classification": classification,
                "ai_confidence":     confidence,
                "status":            status,
            })
            qid = row.get("id") if row else None
            logger.info(f"[Guardian] Quarantine record created: {qid} (status={status})")
            return qid
        except Exception as e:
            logger.warning(f"[Guardian] Failed to create quarantine record: {e}")
            return None

    def _find_existing_decision(self, brand_id: str, gmail_message_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Looks up a persisted guardian decision for this exact message
        BEFORE spending a fresh AI classification call on it.

        The dedup check inside _create_quarantine_record above only ever
        prevented a duplicate DATABASE ROW - it runs after _classify_email()
        already called the AI, so a message already quarantined minutes
        earlier still burned a brand-new classification call on every
        subsequent ~15s poll cycle (confirmed live: the same
        gmail_message_id was re-classified 5+ minutes after its quarantine
        row already existed). This check runs first so a message this brand
        has already evaluated - quarantined OR outright blocked - is never
        re-classified for the rest of the day Gmail's `after:` window keeps
        surfacing it."""
        if not gmail_message_id:
            return None
        try:
            existing = supabase_select("email_quarantine", {
                "brand_id": f"eq.{brand_id}",
                "gmail_message_id": f"eq.{gmail_message_id}",
            })
            return existing[0] if existing else None
        except Exception as e:
            logger.warning(f"[Guardian] Existing-decision lookup failed, proceeding to classify: {e}")
            return None

    def _result_from_existing_record(self, record: Dict[str, Any]) -> "GuardianResult":
        """Reconstructs the GuardianResult a fresh classification would have
        produced, from what was already persisted - no AI call. 'discarded'
        is already filtered out upstream by email_poller.py before evaluate()
        is ever called, and 'promoted' means a real ticket exists (caught by
        the poller's own gmail_message_id-on-tickets check before this), so
        in practice this only ever sees 'pending' or 'auto_blocked' - anything
        else fails toward quarantine (never silently auto-replying to a
        message this brand hasn't cleared)."""
        status = record.get("status")
        classification = record.get("ai_classification") or "unknown"
        confidence = record.get("ai_confidence") or 0.0
        if status == "auto_blocked":
            return GuardianResult(
                decision="blocked", classification=classification, confidence=confidence,
                reason="previously_blocked", quarantine_id=record.get("id"), auto_reply_enabled=False,
            )
        return GuardianResult(
            decision="quarantined", classification=classification, confidence=confidence,
            reason="previously_quarantined", quarantine_id=record.get("id"), auto_reply_enabled=False,
        )

    # ── T007: Main evaluate entry-point ──────────────────────────────────────

    async def evaluate(self, email: dict, brand_id: str, brand_name: str = "our store") -> GuardianResult:
        """
        Run Layers 4–5 on an email that passed Layers 1–3.
        Returns GUARDIAN_ALLOW on any unhandled exception (fail-open).
        """
        try:
            settings = await asyncio.to_thread(self._load_settings, brand_id)
            support_only_mode   = settings["support_only_mode"]
            confidence_threshold = settings["confidence_threshold"]
            auto_reply_enabled  = settings["auto_reply_enabled"]

            subject = email.get("subject", "")
            body    = email.get("body") or email.get("content", "")
            sender  = email.get("sender_email", "")

            gmail_message_id = email.get("id")
            existing_record = await asyncio.to_thread(self._find_existing_decision, brand_id, gmail_message_id)
            if existing_record:
                logger.info(
                    f"[Guardian] Reusing prior decision for gmail_message_id={gmail_message_id} "
                    f"sender={sender} — skipping re-classification"
                )
                return self._result_from_existing_record(existing_record)

            classification, confidence, relevant = await self._classify_email(subject, body, brand_name)

            # Relevance gate: the email may look like customer support in shape
            # (formal tone, "support" in the sender, a registration/transactional
            # style) but be about a completely different company/product — e.g. a
            # course registration receipt from an unrelated service landing in the
            # brand's inbox. Confident "not relevant" is ignored outright; anything
            # uncertain is quarantined rather than auto-replied to.
            if not relevant:
                if confidence >= confidence_threshold:
                    qid = await asyncio.to_thread(
                        self._create_quarantine_record, brand_id, email, classification, confidence, "auto_blocked"
                    )
                    logger.info(f"[email_filter] rejected sender={sender} reason=unrelated_to_brand classification={classification}")
                    return GuardianResult(
                        decision="blocked",
                        classification=classification,
                        confidence=confidence,
                        reason="unrelated_to_brand",
                        quarantine_id=qid,
                        auto_reply_enabled=auto_reply_enabled,
                    )
                qid = await asyncio.to_thread(self._create_quarantine_record, brand_id, email, classification, confidence)
                logger.info(f"[email_filter] quarantined sender={sender} reason=unrelated_to_brand_low_confidence classification={classification}")
                return GuardianResult(
                    decision="quarantined",
                    classification=classification,
                    confidence=confidence,
                    reason="unrelated_to_brand_low_confidence",
                    quarantine_id=qid,
                    auto_reply_enabled=False,
                )

            # Layer 4: intent gate — block known non-support categories.
            # "unknown" is intentionally NOT in BLOCKED_CLASSIFICATIONS: when the AI
            # can't decide, we fail-open so real customers aren't silently lost.
            if support_only_mode and classification in BLOCKED_CLASSIFICATIONS:
                qid = await asyncio.to_thread(
                    self._create_quarantine_record, brand_id, email, classification, confidence, "auto_blocked"
                )
                logger.info(f"[email_filter] rejected sender={sender} reason=ai_classification classification={classification}")
                return GuardianResult(
                    decision="blocked",
                    classification=classification,
                    confidence=confidence,
                    reason="ai_classification",
                    quarantine_id=qid,
                    auto_reply_enabled=auto_reply_enabled,
                )

            # Layer 5: confidence gate — quarantine low-confidence support emails
            if classification == "customer_support" and confidence < confidence_threshold:
                qid = await asyncio.to_thread(self._create_quarantine_record, brand_id, email, classification, confidence)
                logger.info(f"[email_filter] quarantined sender={sender} reason=low_confidence classification={classification}")
                return GuardianResult(
                    decision="quarantined",
                    classification=classification,
                    confidence=confidence,
                    reason="low_confidence",
                    quarantine_id=qid,
                    auto_reply_enabled=False,
                )

            # Unknown classification: allow through (fail-open).
            # Defer to the brand's auto_reply_enabled setting — the processor's own
            # confidence gate (default 65%) still prevents low-quality auto-replies.
            # Hardcoding False here blocked legitimate short emails (e.g. "hii") where
            # the guardian can't classify intent but the AI gets high reply confidence.
            if classification == "unknown":
                logger.info(f"[email_filter] accepted sender={sender} reason=unknown_classification_fail_open")
                return GuardianResult(
                    decision="allowed",
                    classification=classification,
                    confidence=confidence,
                    reason=None,
                    quarantine_id=None,
                    auto_reply_enabled=auto_reply_enabled,
                )

            logger.info(f"[email_filter] accepted sender={sender} reason=ai_classification classification={classification}")
            return GuardianResult(
                decision="allowed",
                classification=classification,
                confidence=confidence,
                reason=None,
                quarantine_id=None,
                auto_reply_enabled=auto_reply_enabled,
            )

        except Exception as e:
            logger.warning(f"[Guardian] evaluate() failed for brand {brand_id}: {e} — failing open")
            return GUARDIAN_ALLOW

    # ── T008: Audit log writer ────────────────────────────────────────────────

    def log_guardian_decision(
        self,
        brand_id: str,
        sender_email: str,
        thread_id: Optional[str],
        result: GuardianResult,
    ) -> None:
        """Append guardian decision to email_filter_log (audit trail)."""
        try:
            supabase_insert("email_filter_log", {
                "brand_id":          brand_id,
                "sender_email":      sender_email,
                "thread_id":         thread_id,
                "decision":          result.decision,
                "filter_reason":     result.reason,
                "email_category":    "unknown",
                "sender_type":       "automated",
                "ai_classification": result.classification,
                "ai_confidence":     result.confidence,
            })
        except Exception as e:
            logger.warning(f"[Guardian] log_guardian_decision failed: {e}")


# Module-level singleton
email_guardian_service = EmailGuardianService()
