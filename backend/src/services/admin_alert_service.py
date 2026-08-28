"""
Admin Alert Service
====================
Two lightweight, zero-token/zero-LLM admin email notifications, built on top
of the existing Resend-based system_email_service.py - no second email
delivery system, no new infrastructure:

1. notify_critical_error() - called from LoggingMiddleware (the app's one
   central request-handling wrapper) on a genuinely unexpected backend
   failure: an unhandled exception, or a response/HTTPException carrying a
   5xx status. Deduplicated per error signature within a short in-memory
   window - same in-memory-cache convention already used elsewhere in this
   codebase (see brand_knowledge_service._kb_doc_cache) rather than adding
   Redis/Celery/a monitoring platform for this.
2. notify_upgrade_request() - called from upgrade_requests.py right after a
   manual-activation upgrade request row is persisted.

Both are best-effort and MUST NEVER raise: a failed admin notification must
never turn a successful request into a failed one, or replace/mask the real
error response a customer would otherwise see.
"""
import os
import re
import time
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.services.system_email_service import send_admin_notification

logger = logging.getLogger(__name__)

ADMIN_ALERT_EMAIL = os.getenv("ADMIN_ALERT_EMAIL", "syedahafsa772@gmail.com")
ENVIRONMENT = os.getenv("ENVIRONMENT") or os.getenv("ENV") or "production"

# ── Redaction ────────────────────────────────────────────────────────────
# Deterministic regex scrub, not a secret-scanning system: wherever one of
# these key names is immediately followed by `: value` or `= value`
# (including embedded mid-sentence, e.g. an exception message that
# interpolated a key), the value is blanked. \b keeps this from matching
# inside unrelated words (e.g. "secrets" in ordinary prose never matches
# the "secret" key, since there's no boundary after "secret" there anyway).
_SENSITIVE_KEYS = (
    "password", "passwd", "pwd", "token", "secret", "api_key", "apikey",
    "authorization", "access_token", "refresh_token", "client_secret",
    "private_key", "encryption_key", "smtp_password", "set-cookie",
)
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)\b(" + "|".join(re.escape(k) for k in _SENSITIVE_KEYS) + r")\b\s*[:=]\s*(?:Bearer\s+)?\"?[^\s,;&\"']+\"?"
)


def redact_text(text: Optional[str]) -> str:
    """Blanks the value following any credential-shaped key (`key: value` /
    `key=value`, anywhere in the text - not just whole-line matches).
    Keeps the key name itself (useful for debugging)."""
    if not text:
        return ""
    return _SENSITIVE_KEY_PATTERN.sub(lambda m: f"{m.group(1)}: [REDACTED]", text)


# ── Dedup / rate limiting ────────────────────────────────────────────────
# Plain in-memory dict, per-process, TTL-window style - the same shape as
# this codebase's other lightweight caches. No new infrastructure.
_ALERT_WINDOW_SECONDS = 300  # 5 minutes
_alert_state: Dict[str, Dict[str, float]] = {}


def _should_send(signature: str) -> Optional[int]:
    """Returns None if this occurrence should be suppressed (an alert for
    this same signature already went out within the window). Otherwise
    returns how many prior occurrences were suppressed since the last alert
    (0 for a fresh signature or one whose window has expired) and starts a
    new window."""
    now = time.time()
    entry = _alert_state.get(signature)
    if entry and (now - entry["last_sent"]) < _ALERT_WINDOW_SECONDS:
        entry["suppressed"] += 1
        return None
    suppressed_since_last = int(entry["suppressed"]) if entry else 0
    _alert_state[signature] = {"last_sent": now, "suppressed": 0}
    return suppressed_since_last


def notify_critical_error(
    *,
    error_type: str,
    error_message: str,
    route: str,
    method: str,
    status_code: int,
    request_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    user_email: Optional[str] = None,
    stack_trace: Optional[str] = None,
) -> None:
    """Best-effort admin email for a genuinely unexpected backend failure.
    Never raises."""
    try:
        signature = f"{error_type}:{method}:{route}"
        suppressed = _should_send(signature)
        if suppressed is None:
            return

        lines = [
            f"Error type: {error_type}",
            f"Error message: {redact_text(error_message)}",
            f"Timestamp: {datetime.now(timezone.utc).isoformat()}",
            f"Environment: {ENVIRONMENT}",
            f"Route: {route}",
            f"HTTP method: {method}",
            f"HTTP status: {status_code}",
        ]
        if request_id:
            lines.append(f"Request ID: {request_id}")
        if tenant_id:
            lines.append(f"Tenant/Brand ID: {tenant_id}")
        if user_email:
            lines.append(f"User email: {user_email}")
        if suppressed:
            lines.append(
                f"Occurrences suppressed since last alert: {suppressed} "
                f"(within the last {_ALERT_WINDOW_SECONDS // 60} min)"
            )
        if stack_trace:
            lines.append("")
            lines.append("Stack trace:")
            lines.append(redact_text(stack_trace))

        send_admin_notification(
            ADMIN_ALERT_EMAIL,
            "\U0001F6A8 tResolv ERROR — Production Error Detected",
            "\n".join(lines),
        )
    except Exception:
        logger.error("[AdminAlert] Failed to send critical-error admin notification", exc_info=True)


def notify_provider_degradation(
    *,
    provider_label: str,
    model: str,
    reason: str,
    attempt_number: int,
    total_providers: int,
    elapsed_seconds: float,
) -> None:
    """Best-effort admin email when a configured AI provider key fails
    mid-request (timeout, rate limit, quota, 5xx) - even if a later
    fallback key recovers the request and it ultimately returns 200.
    notify_critical_error() only fires on an unhandled exception or a 5xx
    response, so a request that fails over through several dead/slow keys
    before finally succeeding never trips it - even though each of those
    keys timing out is real degraded service, and chained across enough
    of them can single-handedly blow the frontend's request budget before
    the eventually-successful response ever arrives. Never raises."""
    try:
        signature = f"provider_degradation:{provider_label}:{reason}"
        suppressed = _should_send(signature)
        if suppressed is None:
            return

        lines = [
            f"Provider: {provider_label}",
            f"Model: {model}",
            f"Failure reason: {reason}",
            f"Attempt: {attempt_number} of {total_providers} configured provider(s)",
            f"Elapsed before failure: {elapsed_seconds:.1f}s",
            f"Timestamp: {datetime.now(timezone.utc).isoformat()}",
            f"Environment: {ENVIRONMENT}",
        ]
        if suppressed:
            lines.append(
                f"Occurrences suppressed since last alert: {suppressed} "
                f"(within the last {_ALERT_WINDOW_SECONDS // 60} min)"
            )

        send_admin_notification(
            ADMIN_ALERT_EMAIL,
            f"⚠️ tResolv WARNING — AI provider '{provider_label}' failed ({reason})",
            "\n".join(lines),
        )
    except Exception:
        logger.error("[AdminAlert] Failed to send provider-degradation admin notification", exc_info=True)


def notify_upgrade_request(
    *,
    name: str,
    email: str,
    tenant_id: str,
    requested_plan: str,
    brand: Optional[str] = None,
    current_plan: Optional[str] = None,
    request_id: Optional[str] = None,
    transaction_reference: Optional[str] = None,
    account_status: Optional[str] = None,
) -> None:
    """Best-effort admin email for a new manual-activation upgrade request.
    Never raises - the merchant's request must stay successful even if this
    fails."""
    try:
        lines: list = ["NEW PAID PLAN REQUEST", ""]
        lines.append(f"Merchant: {name}")
        lines.append(f"Email: {email}")
        lines.append(f"Tenant ID: {tenant_id}")
        if brand:
            lines.append(f"Brand: {brand}")
        lines.append(f"Current plan: {current_plan or 'unknown'}")
        lines.append(f"Requested plan: {requested_plan}")
        lines.append(f"Requested at: {datetime.now(timezone.utc).isoformat()}")
        if request_id:
            lines.append(f"Request ID: {request_id}")
        if transaction_reference:
            lines.append(f"Transaction reference: {transaction_reference}")
        if account_status is not None:
            lines.append(f"Current account status: {account_status}")
        lines.append("")
        lines.append("ACTION REQUIRED: Review this request in the tResolv admin panel.")

        send_admin_notification(
            ADMIN_ALERT_EMAIL,
            f"\U0001F4B0 NEW UPGRADE REQUEST — {requested_plan}",
            "\n".join(lines),
        )
    except Exception:
        logger.error("[AdminAlert] Failed to send upgrade-request admin notification", exc_info=True)
