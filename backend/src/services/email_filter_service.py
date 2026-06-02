"""
Email Filter Service
====================
Evaluates every incoming email against six ordered filter layers before
any ticket creation or AI processing. Returns a FilterResult for each
email and logs the decision to email_filter_log.

Filter layer order (first match wins):
  0. Self-reply detection (sender == brand's own support address)
  1. Whitelist bypass     (sender domain in whitelisted_domains)
  2. Blocked domain       (tenant blocklist + built-in automated domains)
  3. Sender prefix        (noreply@, newsletter@, etc.)
  4. Gmail category       (CATEGORY_PROMOTIONS / SOCIAL / UPDATES labels)
  5. Auto-reply headers   (Auto-Submitted, Precedence: bulk, X-Autoreply, etc.)
  6. Promotional content  (keyword heuristics when promotion_filter_enabled=True)

Safety invariant: evaluate() NEVER raises. On any unhandled exception it
returns ALLOWED_RESULT so a filter bug never blocks a real customer.
"""
import re
import logging
from dataclasses import dataclass
from typing import Optional

from src.lib.supabase_client import supabase_select, supabase_insert

logger = logging.getLogger(__name__)

DEFAULT_STORE = "00000000-0000-0000-0000-000000000000"
DEFAULT_MAX_AUTO_REPLIES = 2

BLOCKED_SENDER_PREFIXES = [
    "noreply", "no-reply", "notifications", "newsletter",
    "digest", "mailer", "hello", "marketing", "updates",
    "notify", "do-not-reply", "donotreply", "bounce",
    "auto-reply", "autoreply", "info", "news",
]

BUILT_IN_BLOCKED_DOMAINS = [
    "mailchimp.com", "list.mailchimp.com",
    "klaviyo.com", "klaviyo-mail.io",
    "linkedin.com", "bounce.linkedin.com", "e.linkedin.com",
    "facebookmail.com", "mail.instagram.com",
    "indeed.com",
    "skool.com",
    "twitter.com",
    "mc.sendgrid.net",
    "hubspot.com", "list.hubspot.com",
    "mailer-daemon.google.com",
    "accounts.google.com",
]

PROMOTIONAL_KEYWORDS = [
    r"\bunsubscribe\b",
    r"\bwebinar\b",
    r"\bdiscount\b",
    r"\bnewsletter\b",
    r"\boutreach\b",
    r"\bsponsorship\b",
    r"\brecruiting\b",
    r"\bjob alert\b",
    r"\bgrowth hacks?\b",
    r"\bcourse offer\b",
    r"\bmarketing\b",
    r"\bopt out\b",
    r"\bmanage preferences\b",
    r"\bview in browser\b",
    r"\blimited time offer\b",
    r"\bspecial offer\b",
    r"\bfree trial\b",
    r"\bclick here\b",
]


@dataclass
class FilterResult:
    decision: str            # "allowed" | "blocked"
    reason: Optional[str]    # filter_reason code or None when allowed
    email_category: str      # "support" | "promotional" | "social" | "updates" | "unknown"
    sender_type: str         # "human" | "automated" | "unknown"


ALLOWED_RESULT = FilterResult(
    decision="allowed",
    reason=None,
    email_category="support",
    sender_type="human",
)


class EmailFilterService:

    # ── Settings ──────────────────────────────────────────────────────────────

    def _load_settings(self, brand_id: str) -> dict:
        """Load filter config from system_settings for this brand.
        Falls back to global default store row if no brand-specific row exists."""
        defaults = {
            "blocked_domains": [],
            "whitelisted_domains": [],
            "max_auto_replies": DEFAULT_MAX_AUTO_REPLIES,
            "promotion_filter_enabled": True,
            "loop_protection_enabled": True,
        }
        try:
            rows = supabase_select("system_settings", {"store_id": f"eq.{brand_id}"})
            if not rows and brand_id != DEFAULT_STORE:
                rows = supabase_select("system_settings", {"store_id": f"eq.{DEFAULT_STORE}"})
            if rows:
                r = rows[0]
                defaults["blocked_domains"] = r.get("blocked_domains") or []
                defaults["whitelisted_domains"] = r.get("whitelisted_domains") or []
                if r.get("max_auto_replies") is not None:
                    defaults["max_auto_replies"] = r["max_auto_replies"]
                if r.get("promotion_filter_enabled") is not None:
                    defaults["promotion_filter_enabled"] = r["promotion_filter_enabled"]
                if r.get("loop_protection_enabled") is not None:
                    defaults["loop_protection_enabled"] = r["loop_protection_enabled"]
        except Exception as e:
            logger.warning(f"[Filter] Could not load settings for brand {brand_id}: {e}")
        return defaults

    # ── Filter layers (private) ───────────────────────────────────────────────

    def _check_whitelist(self, sender_email: str, settings: dict) -> bool:
        """Returns True if sender domain is on the tenant whitelist."""
        if "@" not in sender_email:
            return False
        domain = sender_email.split("@")[-1].lower()
        return domain in [d.lower() for d in (settings.get("whitelisted_domains") or [])]

    def _check_blocked_domain(self, sender_email: str, settings: dict) -> Optional[FilterResult]:
        """Check sender domain against tenant blocklist and built-in automated domain list."""
        if "@" not in sender_email:
            return None
        domain = sender_email.split("@")[-1].lower()
        tenant_blocked = [d.lower() for d in (settings.get("blocked_domains") or [])]
        if domain in tenant_blocked or domain in BUILT_IN_BLOCKED_DOMAINS:
            return FilterResult(
                decision="blocked",
                reason="blocked_domain",
                email_category="unknown",
                sender_type="automated",
            )
        return None

    def _check_sender_prefix(self, sender_email: str) -> Optional[FilterResult]:
        """Check sender local-part against the blocked prefix list."""
        local = sender_email.split("@")[0].lower() if "@" in sender_email else sender_email.lower()
        if any(local == p or local.startswith(p) for p in BLOCKED_SENDER_PREFIXES):
            return FilterResult(
                decision="blocked",
                reason="blocked_sender_pattern",
                email_category="unknown",
                sender_type="automated",
            )
        return None

    def _check_gmail_category(self, label_ids: list) -> Optional[FilterResult]:
        """Map Gmail category labels to filter decisions.
        Returns None gracefully if label_ids is absent or empty."""
        if not label_ids:
            return None
        # Only block Gmail's Promotions tab — Personal, Updates, Social pass through
        if "CATEGORY_PROMOTIONS" in label_ids:
            return FilterResult(
                decision="blocked",
                reason="gmail_category",
                email_category="promotional",
                sender_type="automated",
            )
        return None

    def _check_auto_reply_headers(self, headers: dict) -> Optional[FilterResult]:
        """Inspect lowercased header dict for auto-reply signals.
        Respects 'Auto-Submitted: no' — does not block explicit opt-outs."""
        auto_submitted = headers.get("auto-submitted", "").lower().strip()
        if auto_submitted in ("auto-generated", "auto-replied"):
            return FilterResult(
                decision="blocked",
                reason="auto_reply_header",
                email_category="unknown",
                sender_type="automated",
            )

        precedence = headers.get("precedence", "").lower().strip()
        if precedence in ("bulk", "list"):
            return FilterResult(
                decision="blocked",
                reason="auto_reply_header",
                email_category="unknown",
                sender_type="automated",
            )

        if "x-autoreply" in headers or "x-autorespond" in headers:
            return FilterResult(
                decision="blocked",
                reason="auto_reply_header",
                email_category="unknown",
                sender_type="automated",
            )

        if "list-unsubscribe" in headers:
            return FilterResult(
                decision="blocked",
                reason="auto_reply_header",
                email_category="unknown",
                sender_type="automated",
            )

        return None

    def _check_promotional_content(self, body_text: str, settings: dict) -> Optional[FilterResult]:
        """Keyword scan for promotional content. Skipped when promotion_filter_enabled=False."""
        if not settings.get("promotion_filter_enabled", True):
            return None
        b = body_text.lower()
        if any(re.search(pattern, b) for pattern in PROMOTIONAL_KEYWORDS):
            return FilterResult(
                decision="blocked",
                reason="promotional_content",
                email_category="promotional",
                sender_type="unknown",
            )
        return None

    # ── Public API ────────────────────────────────────────────────────────────

    def evaluate(self, email: dict, brand_id: str) -> FilterResult:
        """Evaluate one email against all six filter layers.

        email dict fields used:
          sender_email        (str)   required
          label_ids           (list)  optional — Gmail category label IDs
          headers             (dict)  optional — lowercased header name → value
          body                (str)   optional — email body/snippet text
          brand_support_email (str)   optional — brand's own support address

        Always returns a FilterResult; swallows all exceptions and returns
        ALLOWED_RESULT as a safety fallback.
        """
        try:
            sender_email = (email.get("sender_email") or "").lower().strip()
            label_ids = email.get("label_ids") or []
            headers = email.get("headers") or {}
            body = email.get("body") or ""
            support_email = (email.get("brand_support_email") or "").lower().strip()

            settings = self._load_settings(brand_id)

            # Layer 0: Self-reply — email is from the brand's own support address
            if support_email and sender_email == support_email:
                return FilterResult(
                    decision="blocked",
                    reason="self_reply",
                    email_category="unknown",
                    sender_type="automated",
                )

            # Layer 1: Whitelist bypass — skip domain/prefix/category checks
            if self._check_whitelist(sender_email, settings):
                # Header and content checks are NOT bypassed (per spec decision 8)
                result = self._check_auto_reply_headers(headers)
                if result:
                    return result
                result = self._check_promotional_content(body, settings)
                if result:
                    return result
                return ALLOWED_RESULT

            # Layer 2: Blocked domain
            result = self._check_blocked_domain(sender_email, settings)
            if result:
                return result

            # Layer 3: Blocked sender prefix
            result = self._check_sender_prefix(sender_email)
            if result:
                return result

            # Layer 4: Gmail category labels
            result = self._check_gmail_category(label_ids)
            if result:
                return result

            # Layer 5: Auto-reply headers
            result = self._check_auto_reply_headers(headers)
            if result:
                return result

            # Layer 6: Promotional content keywords
            result = self._check_promotional_content(body, settings)
            if result:
                return result

            return ALLOWED_RESULT

        except Exception as e:
            logger.warning(f"[Filter] evaluate() exception for brand {brand_id} sender={email.get('sender_email', '?')}: {e}")
            return ALLOWED_RESULT  # Never block a real customer on filter error

    def check_loop_risk(self, ticket: dict, settings: dict) -> bool:
        """Returns True if this thread has reached the auto_reply_count threshold.

        Returns False when:
          - loop_protection_enabled is False
          - ticket has no gmail_thread_id (can't safely detect loops)
          - auto_reply_count is below max_auto_replies
        """
        if not settings.get("loop_protection_enabled", True):
            return False
        if not ticket.get("gmail_thread_id"):
            return False
        count = ticket.get("auto_reply_count") or 0
        max_replies = settings.get("max_auto_replies", DEFAULT_MAX_AUTO_REPLIES)
        return count >= max_replies

    def log_decision(
        self,
        brand_id: str,
        sender_email: str,
        thread_id: Optional[str],
        result: FilterResult,
    ) -> None:
        """Insert one row into email_filter_log. Swallows all errors so a logging
        failure never crashes the email pipeline."""
        try:
            supabase_insert("email_filter_log", {
                "brand_id":      brand_id,
                "sender_email":  sender_email,
                "thread_id":     thread_id,
                "decision":      result.decision,
                "filter_reason": result.reason,
                "email_category": result.email_category,
                "sender_type":   result.sender_type,
            })
        except Exception as e:
            logger.warning(f"[Filter] log_decision insert failed (non-blocking): {e}")


email_filter_service = EmailFilterService()
