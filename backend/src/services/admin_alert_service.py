"""
Admin Alert Service
====================
Lightweight, zero-token/zero-LLM admin email notifications, built on top of
the existing Resend-based system_email_service.py - no second email delivery
system, no new infrastructure:

1. notify_critical_error() - called from LoggingMiddleware (the app's one
   central request-handling wrapper) on a genuinely unexpected backend
   failure: an unhandled exception, or a response/HTTPException carrying a
   5xx status. Deduplicated per error signature within a short in-memory
   window - same in-memory-cache convention already used elsewhere in this
   codebase (see brand_knowledge_service._kb_doc_cache) rather than adding
   Redis/Celery/a monitoring platform for this.
2. notify_provider_exhausted() / notify_provider_recovered() - an incident
   pair for a shared AI provider service (e.g. chat completion): exhausted
   fires once when EVERY configured provider fails a request outright
   (never for a request that recovers via fallback - that's normal
   operation, not an incident); recovered fires at most once, only if there
   was a genuinely active exhausted incident to close out. See PART "Stop
   Email Alert Spam" - this replaced a per-failed-attempt alert that fired
   inside the provider-rotation retry loop itself, which is what produced a
   burst of ~20 emails for what was really one ongoing incident: every
   configured provider failing on every request minted its own
   provider+reason signature, and a request that ultimately succeeded via
   fallback still alerted on every provider it passed through on the way.
3. notify_upgrade_request() - called from upgrade_requests.py right after a
   manual-activation upgrade request row is persisted.

All of these are best-effort and MUST NEVER raise: a failed admin
notification must never turn a successful request into a failed one, or
replace/mask the real error response a customer would otherwise see.
"""
import os
import re
import time
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.lib.supabase_client import supabase_get_setting, supabase_set_setting
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
# Persisted via the existing settings key-value table (supabase_get_setting/
# supabase_set_setting in src/lib/supabase_client.py - already used by
# auth.py for GMAIL_TOKEN) instead of an in-memory dict. An in-memory dict
# only lives for the life of one process, so every deploy/restart reset it
# to empty - a genuine ongoing incident spanning several redeploys (e.g.
# active debugging of a real outage) then looked like a brand-new incident
# on each restart, sending a fresh exhausted+recovered email pair every
# time instead of the intended "one alert pair per incident, however long
# it lasts and however many redeploys happen during it." Reuses the
# existing settings table rather than adding a second state store; alerts
# are inherently low-frequency, so the extra round trip is never a hot path.
_ALERT_WINDOW_SECONDS = 300  # 5 minutes
_SETTING_PREFIX = "admin_alert_state:"


def _load_alert_state(signature: str) -> Dict[str, Any]:
    """{'last_sent': epoch seconds or None, 'suppressed': int,
    'incident_active': bool}. Missing/unreadable state degrades to the same
    "nothing sent yet, no incident active" starting point a fresh in-memory
    dict used to have."""
    value = supabase_get_setting(f"{_SETTING_PREFIX}{signature}")
    if not isinstance(value, dict):
        return {"last_sent": None, "suppressed": 0, "incident_active": False}
    return {
        "last_sent": value.get("last_sent"),
        "suppressed": int(value.get("suppressed") or 0),
        "incident_active": bool(value.get("incident_active")),
    }


def _save_alert_state(signature: str, state: Dict[str, Any]) -> None:
    try:
        supabase_set_setting(f"{_SETTING_PREFIX}{signature}", state)
    except Exception as e:
        logger.warning(f"[AdminAlert] Could not persist alert state for {signature}: {e}")


def _should_send(signature: str) -> Optional[int]:
    """Returns None if this occurrence should be suppressed (an alert for
    this same signature already went out within the window - persisted, so
    a redeploy mid-cooldown does not reset it). Otherwise returns how many
    prior occurrences were suppressed since the last alert (0 for a fresh
    signature or one whose window has expired) and starts a new window."""
    now = time.time()
    state = _load_alert_state(signature)
    last_sent = state.get("last_sent")
    if last_sent is not None and (now - last_sent) < _ALERT_WINDOW_SECONDS:
        state["suppressed"] = int(state.get("suppressed") or 0) + 1
        _save_alert_state(signature, state)
        return None
    suppressed_since_last = int(state.get("suppressed") or 0)
    state["last_sent"] = now
    state["suppressed"] = 0
    _save_alert_state(signature, state)
    return suppressed_since_last


def _mark_incident_active(signature: str, active: bool) -> None:
    state = _load_alert_state(signature)
    state["incident_active"] = active
    _save_alert_state(signature, state)


def _pop_incident_active(signature: str) -> bool:
    """Reads the current incident_active flag and immediately clears it -
    same read-then-clear semantics the in-memory version had, so rapid
    failure/success flapping still produces exactly one recovery email."""
    state = _load_alert_state(signature)
    was_active = bool(state.get("incident_active"))
    if was_active:
        state["incident_active"] = False
        _save_alert_state(signature, state)
    return was_active


def _reset_cooldown(signature: str) -> None:
    """Clears the send-cooldown timer (but not incident_active, which
    _pop_incident_active above already owns) once a recovery has genuinely
    fired. Without this, the 5-minute cooldown from the ORIGINAL outage's
    alert would still be running when a genuinely NEW, independent outage
    starts shortly after recovery - _should_send would see a recent
    last_sent and silently suppress the new incident's alert, even though
    it has nothing to do with the one that already closed. Recovery is the
    one moment we know for certain the prior incident is over, so it's the
    right place to let the next one start its own cooldown from zero."""
    state = _load_alert_state(signature)
    state["last_sent"] = None
    state["suppressed"] = 0
    _save_alert_state(signature, state)


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


def notify_provider_exhausted(
    *,
    attempts: list,
    model: str,
    elapsed_seconds: float,
    service: str = "chat_completion",
) -> None:
    """Best-effort admin email for a genuine incident: EVERY configured AI
    provider failed and the request could not be completed at all - the
    "persistent provider failure" / "persistent quota problem" case. Never
    called for a request that ultimately succeeds via fallback (see PART 3
    of the alert-spam fix: retries/fallback recovery are normal operation,
    not an incident, and must never independently alert).

    Deduplicated by `service` alone, NOT by the specific provider/reason
    breakdown of this occurrence - the exact reason a given attempt failed
    (rate_limited vs timeout vs quota_exceeded vs a 5xx) can vary between
    otherwise-identical occurrences of the same underlying outage, and
    keying on it would fragment one real incident into many distinct
    "signatures" that each get their own alert (this is exactly what
    produced a burst of ~20 emails from a single ongoing incident before
    this fix). The full attempt breakdown is still included in the email
    body for diagnosis - only the dedup key is coarse. Never raises."""
    try:
        signature = f"provider_exhausted:{service}"
        suppressed = _should_send(signature)
        if suppressed is None:
            # Still genuinely failing, but this occurrence's email is
            # suppressed by the cooldown - deliberately does NOT mark the
            # incident active here. Only an occurrence that actually sends
            # an email marks the incident "active" for recovery purposes, so
            # a later notify_provider_recovered() only fires to close out an
            # incident the admin was actually told about - otherwise rapid
            # failure/recovery/failure/recovery flapping within one cooldown
            # window could still produce a paired recovery email for a
            # failure alert that never went out. See PART 10 ("do not spam
            # failure/recovery/failure/recovery for rapid flapping").
            return
        _mark_incident_active(signature, True)

        attempt_lines = "; ".join(f"{a['label']}={a['reason']}" for a in attempts) or "no providers configured"
        lines = [
            f"Service: {service}",
            f"Model: {model}",
            f"Affected provider stack: {attempt_lines}",
            f"All {len(attempts)} configured provider(s) failed.",
            f"Elapsed before giving up: {elapsed_seconds:.1f}s",
            f"Timestamp: {datetime.now(timezone.utc).isoformat()}",
            f"Environment: {ENVIRONMENT}",
            "Impact: requests to this service cannot currently be completed automatically.",
            # Reflects the real durable queue (ai_response_retries /
            # provider_retry_worker.py) - affected customer messages are
            # never dropped, and no one needs to manually replay them.
            "Automatic retry: ON — affected messages are queued and will resume automatically once a provider recovers (bounded backoff, no manual replay needed).",
            "Human intervention: check provider account status (quota/billing/key validity).",
        ]
        if suppressed:
            lines.append(
                f"Occurrences suppressed since last alert: {suppressed} "
                f"(within the last {_ALERT_WINDOW_SECONDS // 60} min)"
            )

        send_admin_notification(
            ADMIN_ALERT_EMAIL,
            f"\U0001F6A8 tResolv ALERT — {service} providers exhausted, requests failing",
            "\n".join(lines),
        )
    except Exception:
        logger.error("[AdminAlert] Failed to send provider-exhausted admin notification", exc_info=True)


def notify_provider_recovered(*, service: str = "chat_completion") -> None:
    """Best-effort admin email sent at most once, and only to close out a
    genuinely active notify_provider_exhausted() incident for this service -
    a service that simply always succeeds never triggers this. Immediately
    clears the active flag so rapid failure/success flapping produces
    exactly one recovery email, not one per success. Never raises.

    Called on every successful chat_completion (see ai_provider_manager.py),
    so this always costs one settings-table read even on the overwhelming
    common case (no incident active) - a deliberate tradeoff of one small
    read per response for the persistence guarantee above (a plain
    in-memory flag can't survive the deploy/restart that happens mid-
    incident). No write happens unless an incident was actually active."""
    try:
        signature = f"provider_exhausted:{service}"
        was_active = _pop_incident_active(signature)
        if not was_active:
            return
        # This incident is genuinely over - let the next one (if any) start
        # its own cooldown from zero rather than inheriting whatever's left
        # of this one's, which could otherwise silently swallow a real,
        # independent outage's alert if it starts soon after (see
        # _reset_cooldown's own docstring).
        _reset_cooldown(signature)

        send_admin_notification(
            ADMIN_ALERT_EMAIL,
            f"✅ tResolv RECOVERED — {service} providers are working again",
            (
                f"Service: {service}\n"
                f"Timestamp: {datetime.now(timezone.utc).isoformat()}\n"
                f"Environment: {ENVIRONMENT}\n"
                "A request to this service just succeeded after a prior failure incident."
            ),
        )
    except Exception:
        logger.error("[AdminAlert] Failed to send provider-recovered admin notification", exc_info=True)


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
