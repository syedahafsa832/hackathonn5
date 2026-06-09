"""
Email Guardian Service — Layers 4 & 5
======================================
Layer 4: AI intent classification via Mistral API.
Layer 5: Confidence gate — low-confidence customer_support emails are quarantined.

Fires AFTER email_filter_service (Layers 1–3). Only called for emails with decision="allowed".
Fail-open: any exception in evaluate() returns GUARDIAN_ALLOW so a real customer is never lost.
"""
import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

from openai import OpenAI

from src.lib.supabase_client import supabase_insert, supabase_select

logger = logging.getLogger(__name__)

DEFAULT_STORE = "00000000-0000-0000-0000-000000000000"

VALID_CLASSIFICATIONS = {
    "customer_support", "promotion", "newsletter",
    "outreach", "spam", "automation", "unknown",
}

# Only these classifications are definitively NOT customer support and should be blocked.
# "unknown" is intentionally excluded — when the AI can't decide, we let it through.
BLOCKED_CLASSIFICATIONS = {"promotion", "newsletter", "outreach", "spam", "automation"}

CLASSIFIER_PROMPT = """Classify the following email into exactly one of these categories:
customer_support, promotion, newsletter, outreach, spam, automation, unknown

Respond with valid JSON only: {{"classification": "<label>", "confidence": <0.0-1.0>}}

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

    def __init__(self):
        self._client: Optional[OpenAI] = None

    def _get_client(self) -> Optional[OpenAI]:
        if self._client is None:
            api_key = os.getenv("MISTRAL_API_KEY") or os.getenv("OPENAI_API_KEY")
            if not api_key:
                logger.warning("[Guardian] No MISTRAL_API_KEY — classifier unavailable")
                return None
            self._client = OpenAI(
                api_key=api_key,
                base_url=os.getenv("MISTRAL_API_BASE_URL", "https://api.mistral.ai/v1"),
            )
        return self._client

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

    def _classify_email(self, subject: str, body: str) -> tuple[str, float]:
        """Call Mistral to classify email intent. Returns (classification, confidence)."""
        client = self._get_client()
        if not client:
            return ("unknown", 0.0)

        prompt = CLASSIFIER_PROMPT.format(
            subject=(subject or "")[:500],
            body=(body or "")[:2000],
        )
        model = os.getenv("MISTRAL_MODEL", "mistral-large-latest")

        try:
            # Attempt with JSON mode first; fall back without it for older models
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=80,
                    response_format={"type": "json_object"},
                )
            except Exception:
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=80,
                )

            raw = response.choices[0].message.content or ""
            data = json.loads(raw)
            classification = str(data.get("classification", "unknown")).lower()
            confidence = float(data.get("confidence", 0.0))

            if classification not in VALID_CLASSIFICATIONS:
                classification = "unknown"
            confidence = max(0.0, min(1.0, confidence))

            logger.info(f"[Guardian] Classifier → {classification} ({confidence:.2f})")
            return (classification, confidence)

        except Exception as e:
            logger.warning(f"[Guardian] Classifier error: {e}")
            return ("unknown", 0.0)

    # ── T006: Quarantine record creation ─────────────────────────────────────

    def _create_quarantine_record(
        self,
        brand_id: str,
        email: dict,
        classification: str,
        confidence: float,
    ) -> Optional[str]:
        """Insert a quarantine record. Returns the new record id or None on error."""
        try:
            row = supabase_insert("email_quarantine", {
                "brand_id":          brand_id,
                "sender_email":      email.get("sender_email") or email.get("customer_email", ""),
                "subject":           email.get("subject", ""),
                "body_preview":      (email.get("body") or email.get("content", ""))[:500],
                "thread_id":         email.get("thread_id"),
                "ai_classification": classification,
                "ai_confidence":     confidence,
                "status":            "pending",
            })
            qid = row.get("id") if row else None
            logger.info(f"[Guardian] Quarantine record created: {qid}")
            return qid
        except Exception as e:
            logger.warning(f"[Guardian] Failed to create quarantine record: {e}")
            return None

    # ── T007: Main evaluate entry-point ──────────────────────────────────────

    def evaluate(self, email: dict, brand_id: str) -> GuardianResult:
        """
        Run Layers 4–5 on an email that passed Layers 1–3.
        Returns GUARDIAN_ALLOW on any unhandled exception (fail-open).
        """
        try:
            settings = self._load_settings(brand_id)
            support_only_mode   = settings["support_only_mode"]
            confidence_threshold = settings["confidence_threshold"]
            auto_reply_enabled  = settings["auto_reply_enabled"]

            subject = email.get("subject", "")
            body    = email.get("body") or email.get("content", "")

            classification, confidence = self._classify_email(subject, body)

            # Layer 4: intent gate — block known non-support categories.
            # "unknown" is intentionally NOT in BLOCKED_CLASSIFICATIONS: when the AI
            # can't decide, we fail-open so real customers aren't silently lost.
            if support_only_mode and classification in BLOCKED_CLASSIFICATIONS:
                return GuardianResult(
                    decision="blocked",
                    classification=classification,
                    confidence=confidence,
                    reason="ai_classification",
                    quarantine_id=None,
                    auto_reply_enabled=auto_reply_enabled,
                )

            # Layer 5: confidence gate — quarantine low-confidence support emails
            if classification == "customer_support" and confidence < confidence_threshold:
                qid = self._create_quarantine_record(brand_id, email, classification, confidence)
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
                return GuardianResult(
                    decision="allowed",
                    classification=classification,
                    confidence=confidence,
                    reason=None,
                    quarantine_id=None,
                    auto_reply_enabled=auto_reply_enabled,
                )

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
