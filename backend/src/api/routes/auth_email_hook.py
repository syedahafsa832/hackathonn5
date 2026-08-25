"""
Supabase Auth "Send Email" Hook
=================================
Receives Supabase Auth's Send Email Hook webhook — Authentication > Hooks >
"Send Email" must be configured in the Supabase dashboard to POST here —
and sends the actual email ourselves via system_email_service, instead of
Supabase's own built-in email provider (unreliable for this product).

Supabase hands us the real token_hash/redirect_to/email_action_type in the
payload; we reconstruct the same confirmation URL Supabase's own
`{{ .ConfirmationURL }}` email-template variable would have built —
GET {SUPABASE_URL}/auth/v1/verify?token=<token_hash>&type=<action_type>
&redirect_to=<redirect_to> — just delivered through our own SMTP sender
instead of theirs. Nothing about the recovery/session mechanism changes;
only who sends the email.

This hook is GLOBAL across every Supabase Auth email type (recovery,
signup confirmation, magic link, email change, invite) once enabled —
there is no way to scope it to password-reset only at the Supabase config
level. All types are handled here so enabling the hook doesn't silently
break signup confirmation; only "recovery" gets the fully branded
template for now (see system_email_service.send_generic_auth_email for
the rest).

Security: every request is verified against SEND_EMAIL_HOOK_SECRET using
the Standard Webhooks signing scheme Supabase uses (HMAC-SHA256 over
"{id}.{timestamp}.{body}", header-carried signature/timestamp/id) —
implemented here with stdlib hmac/hashlib rather than adding a dependency
for one signature check.
"""
import base64
import hashlib
import hmac
import logging
import os
import time
from urllib.parse import quote

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks

from src.services import system_email_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Auth Email Hook"])

SEND_EMAIL_HOOK_SECRET = os.getenv("SEND_EMAIL_HOOK_SECRET", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")

_WEBHOOK_TOLERANCE_SECONDS = 300  # Standard Webhooks' own recommended replay-protection window.

# subject, action button label — per Supabase's `email_action_type` values.
_ACTION_COPY = {
    "signup": ("Confirm your tResolv account", "Confirm email"),
    "email_change": ("Confirm your new email address", "Confirm email change"),
    "email_change_current": ("Confirm your new email address", "Confirm email change"),
    "email_change_new": ("Confirm your new email address", "Confirm email change"),
    "magiclink": ("Your tResolv sign-in link", "Sign in"),
    "invite": ("You've been invited to tResolv", "Accept invite"),
    "reauthentication": ("Confirm it's you", "Confirm"),
}


def _verify_signature(payload: bytes, headers, secret: str) -> bool:
    """Standard Webhooks verification (the scheme Supabase Auth Hooks use).
    See https://www.standardwebhooks.com/ — no dependency added; this is a
    ~15-line HMAC check against stdlib primitives."""
    webhook_id = headers.get("webhook-id")
    webhook_timestamp = headers.get("webhook-timestamp")
    webhook_signature = headers.get("webhook-signature")
    if not (webhook_id and webhook_timestamp and webhook_signature):
        return False

    try:
        ts = int(webhook_timestamp)
    except ValueError:
        return False
    if abs(time.time() - ts) > _WEBHOOK_TOLERANCE_SECONDS:
        return False

    try:
        # Supabase requires the secret in Standard Webhooks' versioned form
        # "v1,whsec_<base64>" - strip the "v1," version prefix (if present)
        # before the "whsec_" prefix, then decode.
        raw_secret = secret.split(",", 1)[1] if "," in secret else secret
        secret_bytes = base64.b64decode(raw_secret.removeprefix("whsec_"))
    except Exception:
        return False

    signed_content = f"{webhook_id}.{webhook_timestamp}.{payload.decode('utf-8')}"
    expected = base64.b64encode(
        hmac.new(secret_bytes, signed_content.encode("utf-8"), hashlib.sha256).digest()
    ).decode()

    for candidate in webhook_signature.split():
        parts = candidate.split(",", 1)
        if len(parts) == 2 and hmac.compare_digest(parts[1], expected):
            return True
    return False


def _build_verify_url(token_hash: str, action_type: str, redirect_to: str) -> str:
    return (
        f"{SUPABASE_URL}/auth/v1/verify"
        f"?token={quote(token_hash, safe='')}"
        f"&type={quote(action_type, safe='')}"
        f"&redirect_to={quote(redirect_to, safe='')}"
    )


def _send_email_in_background(action_type: str, to_email: str, verify_url: str) -> None:
    if action_type == "recovery":
        sent = system_email_service.send_password_reset_email(to_email, verify_url)
    else:
        subject, action_label = _ACTION_COPY.get(action_type, ("tResolv account notification", "Continue"))
        sent = system_email_service.send_generic_auth_email(to_email, subject, action_label, verify_url)

    if not sent:
        logger.error(f"[AuthEmailHook] Delivery failed for '{action_type}' to {to_email}")


@router.post("/email-hook")
async def send_email_hook(request: Request, background_tasks: BackgroundTasks):
    """Supabase calls this instead of sending the auth email itself, and
    enforces a hard 5-second response budget — if we don't answer in time it
    treats the underlying auth operation (recover, signup, etc.) as failed
    entirely, even though the SMTP send itself can easily take longer than
    that (connection + TLS handshake + auth + send, on top of whatever
    latency getting to this instance at all takes). So the actual send is
    scheduled as a background task and we return 2xx as soon as the request
    is verified and valid — Supabase only needs to know we *accepted* the
    job, not that delivery finished. A send failure after this point is
    only visible in backend logs, not retried by Supabase; that's the
    correct trade-off for a hook with an external hard timeout."""
    raw_body = await request.body()

    if not SEND_EMAIL_HOOK_SECRET:
        logger.error("[AuthEmailHook] SEND_EMAIL_HOOK_SECRET not configured — rejecting request")
        raise HTTPException(status_code=500, detail="Email hook not configured")

    if not _verify_signature(raw_body, request.headers, SEND_EMAIL_HOOK_SECRET):
        logger.warning("[AuthEmailHook] Signature verification failed")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid payload")

    user = payload.get("user") or {}
    email_data = payload.get("email_data") or {}

    to_email = user.get("email")
    token_hash = email_data.get("token_hash")
    redirect_to = email_data.get("redirect_to") or ""
    action_type = email_data.get("email_action_type") or ""

    if not (to_email and token_hash and action_type):
        logger.error("[AuthEmailHook] Missing required fields in payload")
        raise HTTPException(status_code=400, detail="Missing required fields")

    # Never log token_hash, redirect_to, or the URL built from them.
    logger.info(f"[AuthEmailHook] Sending '{action_type}' email to {to_email}")

    verify_url = _build_verify_url(token_hash, action_type, redirect_to)

    background_tasks.add_task(_send_email_in_background, action_type, to_email, verify_url)

    return {"success": True}
