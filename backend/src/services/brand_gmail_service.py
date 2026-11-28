"""
Per-Brand Gmail OAuth Service
==============================
Each brand can connect its own Gmail inbox.
Credentials (OAuth tokens) are stored per-brand in the brands table.
The shared Google Cloud project client_id/secret comes from env vars.
"""
import os
import json
import base64
import hmac
import hashlib
import time
import logging
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import make_msgid

import tenacity
from src.services.email_layout import render_plain_text_email_html
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logging.getLogger('googleapiclient.discovery_cache').setLevel(logging.ERROR)

# Transient Gmail API failures (server-side/rate-limit) worth one bounded
# retry with backoff+jitter - never permanent auth/permission errors (401/
#403), which a retry can't fix and would only delay the real failure.
_TRANSIENT_GMAIL_STATUSES = {429, 500, 502, 503, 504}


def _is_transient_http_error(exc: BaseException) -> bool:
    return isinstance(exc, HttpError) and getattr(exc, "status_code", None) in _TRANSIENT_GMAIL_STATUSES


@tenacity.retry(
    retry=tenacity.retry_if_exception(_is_transient_http_error),
    stop=tenacity.stop_after_attempt(3),  # 1 try + up to 2 retries
    wait=tenacity.wait_exponential_jitter(initial=0.5, max=4),
    reraise=True,
)
def _batch_modify_with_retry(svc, message_id: str, remove_label_ids: list) -> None:
    svc.users().messages().batchModify(
        userId="me",
        body={"ids": [message_id], "removeLabelIds": remove_label_ids},
    ).execute()

from src.lib.supabase_client import supabase_select, supabase_update
# Reused as-is from shopify_service.py — generic string-in/string-out AES-256-GCM
# helpers, not Shopify-specific. gmail_token was the one credential in this
# codebase still written to the database in plaintext (Shopify tokens already
# went through this). decrypt_token() already handles legacy plaintext values
# gracefully (returns them unchanged when they don't match a known ciphertext
# format), so existing connected brands keep working and get re-encrypted the
# next time their token is refreshed/rewritten — no separate migration needed.
from src.services.shopify_service import encrypt_token, decrypt_token

logger = logging.getLogger(__name__)

# The only supported Gmail API permission is send-only. Incoming mail is
# delivered by merchant-configured forwarding to tResolv's inbound address.
SEND_ONLY_SCOPES = [
    'https://www.googleapis.com/auth/gmail.send',
    'openid',
    'email',
]
_STATE_TTL = 600  # 10-minute OAuth window


class FetchedEmails(list):
    """A plain list of email dicts everywhere it's used (iteration, len(),
    indexing) - existing callers/tests that mock get_new_emails with a bare
    list are unaffected. Also carries fetch_failures: how many messages
    Gmail's search matched that could NOT be retrieved this poll (genuine
    content-fetch errors only, not a cosmetic mark-as-read failure - see
    _get_new_emails_sync). email_poller.py reads this to make its
    "fetched=X processed=Y failures=Z" summary log accurate; a plain list
    (as every existing mock still returns) reports 0 via getattr's default,
    identical to today's behavior."""
    fetch_failures = 0


def _state_key() -> bytes:
    return os.getenv("SECRET_KEY", os.getenv("JWT_SECRET", "change-me-in-production")).encode()


def _sign_state(brand_id: str, scope_mode: str = "send_only", return_to: str = "brands") -> str:
    """Return HMAC-signed state token: base64(payload).signature.

    scope_mode is signed with the brand ID so the callback accepts only the
    current send-only Gmail connection.
    """
    payload = json.dumps({
        "brand_id": brand_id,
        "scope_mode": scope_mode,
        "return_to": return_to if return_to in {"brands", "settings", "onboarding"} else "brands",
        "exp": int(time.time()) + _STATE_TTL,
    }).encode()
    sig = hmac.new(_state_key(), payload, hashlib.sha256).hexdigest()[:24]
    b64 = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"{b64}.{sig}"


def _verify_state(state: str) -> Optional[str]:
    """Verify signed state token. Returns brand_id or None if invalid/expired/tampered."""
    try:
        b64, sig = state.rsplit(".", 1)
        pad = 4 - len(b64) % 4
        if pad != 4:
            b64 += "=" * pad
        payload = base64.urlsafe_b64decode(b64.encode())
        expected = hmac.new(_state_key(), payload, hashlib.sha256).hexdigest()[:24]
        if not hmac.compare_digest(sig, expected):
            logger.warning("[BrandGmail] OAuth state signature mismatch — possible tampering")
            return None
        data = json.loads(payload)
        if data.get("exp", 0) < int(time.time()):
            logger.warning("[BrandGmail] OAuth state expired")
            return None
        return data.get("brand_id")
    except Exception as e:
        logger.warning(f"[BrandGmail] State verification error: {e}")
        return None


def _state_scope_mode(state: str) -> str:
    """Read the signed scope mode. Unknown/old states are never upgraded."""
    try:
        b64 = state.rsplit(".", 1)[0]
        b64 += "=" * ((4 - len(b64) % 4) % 4)
        data = json.loads(base64.urlsafe_b64decode(b64.encode()))
        return "send_only" if data.get("scope_mode") == "send_only" else "legacy_disabled"
    except Exception:
        return "legacy_disabled"


def _state_return_to(state: str) -> str:
    """Read the allow-listed destination from a signed OAuth state payload."""
    try:
        b64 = state.rsplit(".", 1)[0]
        b64 += "=" * ((4 - len(b64) % 4) % 4)
        data = json.loads(base64.urlsafe_b64decode(b64.encode()))
        destination = data.get("return_to")
        return destination if destination in {"brands", "settings", "onboarding"} else "brands"
    except Exception:
        return "brands"


def _get_client_config() -> dict:
    """Build the dedicated OAuth client config for Gmail send-only access."""
    client_id = os.getenv("GMAIL_SEND_CLIENT_ID")
    client_secret = os.getenv("GMAIL_SEND_CLIENT_SECRET")
    raw = os.getenv("GMAIL_SEND_CREDENTIALS")
    label = "GMAIL_SEND_CLIENT_ID + GMAIL_SEND_CLIENT_SECRET"

    if client_id and client_secret:
        return {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [_callback_uri()],
            }
        }

    if raw:
        return json.loads(raw)

    raise ValueError(f"No Gmail send-only OAuth credentials configured. Set {label}.")


def _callback_uri() -> str:
    # Respect the exact callback URI already configured in Google OAuth
    # and Render. API_BASE_URL remains the backward-compatible fallback.
    configured_uri = os.getenv("GOOGLE_REDIRECT_URI", "").strip()
    if configured_uri:
        return configured_uri.rstrip("/")
    base = os.getenv("API_BASE_URL", "http://localhost:8001").rstrip("/")
    return f"{base}/api/brands/gmail/callback"


class BrandGmailService:

    # ── OAuth ──────────────────────────────────────────────────────────────

    def get_auth_url(self, brand_id: str) -> str:
        """Legacy inbox-reading OAuth is permanently disabled."""
        raise ValueError(
            "Gmail inbox access is retired. Configure email forwarding and connect Gmail send-only instead."
        )

    def get_send_auth_url(self, brand_id: str, return_to: str = "brands") -> str:
        """Return a Gmail send-only OAuth URL for forwarding-mode brands.

        include_granted_scopes is deliberately false: do not silently carry
        the previous gmail.modify grant into this lower-permission connection.
        """
        flow = Flow.from_client_config(_get_client_config(), scopes=SEND_ONLY_SCOPES)
        flow.redirect_uri = _callback_uri()
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="false",
            state=_sign_state(brand_id, scope_mode="send_only", return_to=return_to),
            prompt="consent",
        )
        return auth_url

    def get_return_path(self, state: str) -> str:
        """Return a safe frontend path only when the OAuth state signature is valid."""
        if not _verify_state(state):
            return "/brands"
        return {
            "brands": "/brands",
            "settings": "/settings",
            "onboarding": "/onboarding",
        }[_state_return_to(state)]

    async def handle_callback(self, state: str, code: str) -> Dict[str, Any]:
        """Exchange auth code → tokens and persist either full or send-only access."""
        brand_id = _verify_state(state)
        if not brand_id:
            return {"success": False, "error": "invalid_or_expired_state"}
        scope_mode = _state_scope_mode(state)
        if scope_mode != "send_only":
            return {"success": False, "error": "legacy_inbox_sync_disabled"}
        requested_scopes = SEND_ONLY_SCOPES
        try:
            flow = Flow.from_client_config(_get_client_config(), scopes=requested_scopes, state=state)
            flow.redirect_uri = _callback_uri()
            flow.fetch_token(code=code)
            creds = flow.credentials

            if scope_mode == "send_only":
                granted_scopes = set(getattr(creds, "granted_scopes", None) or creds.scopes or [])
                gmail_send_scope = "https://www.googleapis.com/auth/gmail.send"
                forbidden_scopes = {
                    "https://www.googleapis.com/auth/gmail.modify",
                    "https://mail.google.com/",
                    "https://www.googleapis.com/auth/gmail.readonly",
                }
                if gmail_send_scope not in granted_scopes or granted_scopes.intersection(forbidden_scopes):
                    raise ValueError("Send-only OAuth grant did not return the expected Gmail scope")

                # gmail.send cannot call users.getProfile(). Use the verified
                # OpenID ID token instead; this does not request mailbox access.
                from google.oauth2 import id_token as google_id_token
                client_id = creds.client_id or os.getenv("GMAIL_SEND_CLIENT_ID")
                id_info = google_id_token.verify_oauth2_token(
                    creds.id_token, Request(), audience=client_id
                )
                if not id_info.get("email") or not id_info.get("email_verified"):
                    raise ValueError("Google did not return a verified account email")
                email = id_info["email"]
            else:
                # Defensive: the legacy mode was rejected before token exchange.
                raise ValueError("Legacy Gmail inbox access is disabled")

            token_data = {
                "token":         creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri":     creds.token_uri,
                "client_id":     creds.client_id,
                "client_secret": creds.client_secret,
                "scopes":        list(creds.scopes or requested_scopes),
                "scope_mode":     scope_mode,
                "expiry":        creds.expiry.isoformat() if creds.expiry else None,
            }

            now_iso = datetime.now(timezone.utc).isoformat()
            if scope_mode == "send_only":
                supabase_update("brands", {"id": f"eq.{brand_id}"}, {
                    "gmail_send_email": email,
                    "gmail_send_token": encrypt_token(json.dumps(token_data)),
                    "gmail_send_connected": True,
                    "is_active": True,
                    "updated_at": now_iso,
                })
            else:
                raise ValueError("Legacy Gmail inbox access is disabled")

            logger.info(f"[BrandGmail] Gmail connection completed for brand {brand_id}; account address omitted from logs.")
            return {"success": True, "email": email}

        except Exception as e:
            # The full exception (which can include request/response detail
            # from Google's token endpoint) is logged server-side only — it
            # must never reach the frontend, since the caller (the /gmail/
            # callback route) puts this "error" value straight into the
            # redirect URL's query string. A raw exception string there would
            # both leak internals and produce an unpredictable, often
            # URL-unsafe value the frontend can't map to a clear message.
            # A stable code is returned instead; Settings/Onboarding show
            # their own specific copy for it.
            logger.error(f"[BrandGmail] Callback error for brand {brand_id}: {e}")
            err_str = str(e).lower()
            if "scope" in err_str:
                # Google granted a different/narrower scope set than requested
                # (e.g. mismatch after a user edits consent) — token exchange
                # itself raises here rather than silently connecting.
                code = "scope_mismatch"
            else:
                code = "connection_failed"
            return {"success": False, "error": code}

    def disconnect(self, brand_id: str):
        """Explicitly disconnect both Gmail capabilities for this brand."""
        supabase_update("brands", {"id": f"eq.{brand_id}"}, {
            "gmail_connected": False, "gmail_token": None, "gmail_email": None,
            "gmail_send_connected": False, "gmail_send_token": None, "gmail_send_email": None,
            "gmail_scope_mode": "send_only",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

    # ── Gmail service ──────────────────────────────────────────────────────

    def _build_service(self, brand: dict):
        """Build Gmail API client from the credential tier matching ingestion mode."""
        # Gmail API access is used for sending only. Incoming messages are
        # forwarded through the tResolv inbound address, never read from Gmail.
        if brand.get("email_ingestion_mode") != "forwarding":
            return None
        send_only = True
        token_field = "gmail_send_token"
        connected_field = "gmail_send_connected"
        email_field = "gmail_send_email"
        raw = brand.get(token_field)
        if not raw:
            return None
        try:
            data = json.loads(decrypt_token(raw))
            expiry = None
            if data.get("expiry"):
                try:
                    expiry = datetime.fromisoformat(data["expiry"])
                except (TypeError, ValueError):
                    expiry = None
            client_id_env = "GMAIL_SEND_CLIENT_ID"
            client_secret_env = "GMAIL_SEND_CLIENT_SECRET"
            creds = Credentials(
                token=data.get("token"),
                refresh_token=data.get("refresh_token"),
                token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
                client_id=data.get("client_id") or os.getenv(client_id_env),
                client_secret=data.get("client_secret") or os.getenv(client_secret_env),
                scopes=data.get("scopes", SEND_ONLY_SCOPES),
                expiry=expiry,
            )
            needs_refresh = not creds.token or creds.expiry is None or creds.expired
            if creds.refresh_token and needs_refresh:
                try:
                    creds.refresh(Request())
                    data["token"] = creds.token
                    data["expiry"] = creds.expiry.isoformat() if creds.expiry else None
                    supabase_update("brands", {"id": f"eq.{brand['id']}"}, {
                        token_field: encrypt_token(json.dumps(data))
                    })
                except Exception as e:
                    err = str(e).lower()
                    # A deleted OAuth client is a permanent credential failure,
                    # not a transient refresh outage. Google rejects every
                    # existing access/refresh token tied to that client, so leave
                    # the account visibly disconnected until an admin reconnects
                    # it using an active OAuth client.
                    oauth_credentials_invalid = (
                        "invalid_grant" in err
                        or "token has been expired or revoked" in err
                        or "deleted_client" in err
                    )
                    if oauth_credentials_invalid:
                        supabase_update("brands", {"id": f"eq.{brand['id']}"}, {
                            connected_field: False,
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        })
                        reason = "OAuth client deleted" if "deleted_client" in err else "refresh token revoked or expired"
                        logger.error(
                            f"[BrandGmail] {connected_field} requires reconnect for "
                            f"{brand.get('id')} ({brand.get(email_field)}): {reason}"
                        )
                    else:
                        logger.warning(f"[BrandGmail] Refresh failed for {brand.get('id')}: {type(e).__name__}: {e}")
                    return None
            return build("gmail", "v1", credentials=creds)
        except Exception:
            logger.exception(f"[BrandGmail] Failed to build Gmail service for {brand.get('id')}")
            return None

    @staticmethod
    def _decode_body(payload: dict) -> str:
        """Decode full email body from Gmail API payload (base64url encoded)."""
        import base64

        def _decode(data: str) -> str:
            try:
                padded = data + "=" * (4 - len(data) % 4)
                return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
            except Exception:
                return ""

        parts = payload.get("parts", [])
        if not parts:
            return _decode(payload.get("body", {}).get("data", ""))

        # Prefer text/plain; fall back to text/html
        plain = ""
        html = ""
        for part in parts:
            mime = part.get("mimeType", "")
            if mime == "text/plain":
                plain = _decode(part.get("body", {}).get("data", ""))
            elif mime == "text/html":
                html = _decode(part.get("body", {}).get("data", ""))
            elif mime.startswith("multipart/"):
                nested = BrandGmailService._decode_body(part)
                if nested and not plain:
                    plain = nested
        return plain or html

    async def get_new_emails(self, brand: dict, max_results: int = 10, since_dt=None) -> "FetchedEmails":
        """Fetch + mark-as-read emails for one brand received after since_dt (or unread if no timestamp).
        Raises on network/API failure so the caller can skip updating last_polled_at.

        The Google API client is fully synchronous, so the actual work runs in a thread —
        this lets the poller's asyncio.gather() across brands achieve real concurrency
        instead of each brand blocking the event loop in turn."""
        return await asyncio.to_thread(self._get_new_emails_sync, brand, max_results, since_dt)

    def _get_new_emails_sync(self, brand: dict, max_results: int = 10, since_dt=None) -> "FetchedEmails":
        """Legacy Gmail inbox polling is permanently disabled.

        Incoming support messages are delivered through merchant-configured
        forwarding to tResolv's inbound address. The Gmail API is send-only.
        This method intentionally never calls users.messages.list/get/modify.
        """
        logger.info("[BrandGmail] Inbox polling is retired; incoming mail uses forwarding.")
        return FetchedEmails()

    @staticmethod
    def _build_message(
        body: str,
        in_reply_to: str = None,
        references: List[str] = None,
    ) -> MIMEMultipart:
        """Build an email with optional RFC threading headers.

        Forwarded messages do not contain Gmail's private threadId, so the
        send-only path relies on Message-ID/In-Reply-To/References instead.
        """
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body, "plain"))
        msg.attach(MIMEText(render_plain_text_email_html(body), "html"))
        if in_reply_to or references:
            msg["Message-ID"] = make_msgid(domain="tresolv.online")
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        cleaned = [value.strip() for value in (references or []) if value and value.strip()]
        if in_reply_to and in_reply_to not in cleaned:
            cleaned.append(in_reply_to)
        if cleaned:
            # Always include the original inbound Message-ID on the first
            # response too; later replies can then retain a stable RFC thread root.
            msg["References"] = " ".join(cleaned[-100:])
        return msg

    async def send_email(
        self,
        brand: dict,
        to_email: str,
        subject: str,
        body: str,
        thread_id: str = None,
        in_reply_to: str = None,
        references: List[str] = None,
    ) -> Dict[str, Any]:
        """Async boundary for _send_email_sync — Gmail's API client is synchronous."""
        return await asyncio.to_thread(
            self._send_email_sync,
            brand,
            to_email,
            subject,
            body,
            thread_id,
            in_reply_to,
            references,
        )

    def _send_email_sync(
        self,
        brand: dict,
        to_email: str,
        subject: str,
        body: str,
        thread_id: str = None,
        in_reply_to: str = None,
        references: List[str] = None,
    ) -> Dict[str, Any]:
        """Send an email from a brand's connected Gmail account.

        thread_id, when given, keeps the reply in Gmail's existing
        conversation (Gmail assigns every send() a brand-new thread unless
        threadId is explicitly passed - a "Re:" subject alone does not do
        this). Without it, the customer's own Gmail "Reply" click lands in
        a thread our poller has never seen, so STAGE 1.5's
        gmail_thread_id match in message_processor.py always misses and a
        duplicate ticket gets created for what is really the same
        conversation."""
        svc = self._build_service(brand)
        if not svc:
            return {"success": False, "error": "Gmail not connected for this brand"}
        try:
            msg = self._build_message(body, in_reply_to=in_reply_to, references=references)
            msg["to"]      = to_email
            msg["subject"] = subject
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            send_body = {"raw": raw}
            if thread_id:
                send_body["threadId"] = thread_id
            sent = svc.users().messages().send(userId="me", body=send_body).execute()
            return {
                "success": True,
                "id": sent.get("id"),
                "message_id": msg.get("Message-ID"),
            }
        except Exception as e:
            logger.error(f"[BrandGmail] Send error for brand {brand.get('name')}: {e}")
            return {"success": False, "error": str(e)}

    async def send_html_reply_in_thread(
        self, brand: dict, to_email: str, subject: str, html_body: str,
        plain_text_body: str, thread_id: str = None,
        in_reply_to: str = None, references: List[str] = None,
    ) -> Dict[str, Any]:
        """Async boundary for HTML replies; supports Gmail thread IDs or RFC headers."""
        return await asyncio.to_thread(
            self._send_html_reply_in_thread_sync, brand, to_email, subject,
            html_body, plain_text_body, thread_id, in_reply_to, references,
        )

    def _send_html_reply_in_thread_sync(
        self, brand: dict, to_email: str, subject: str, html_body: str,
        plain_text_body: str, thread_id: str = None,
        in_reply_to: str = None, references: List[str] = None,
    ) -> Dict[str, Any]:
        """Send the CSAT email using the real Gmail thread ID or RFC reply headers."""
        svc = self._build_service(brand)
        if not svc:
            return {"success": False, "error": "Gmail not connected for this brand"}
        try:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(plain_text_body, "plain"))
            msg.attach(MIMEText(html_body, "html"))
            msg["to"] = to_email
            msg["subject"] = subject if subject.startswith("Re:") else f"Re: {subject}"
            if in_reply_to or references:
                msg["Message-ID"] = make_msgid(domain="tresolv.online")
            if in_reply_to:
                msg["In-Reply-To"] = in_reply_to
            cleaned = [value.strip() for value in (references or []) if value and value.strip()]
            if in_reply_to and in_reply_to not in cleaned:
                cleaned.append(in_reply_to)
            if cleaned:
                msg["References"] = " ".join(cleaned[-100:])
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            send_body = {"raw": raw}
            if thread_id:
                send_body["threadId"] = thread_id
            sent = svc.users().messages().send(userId="me", body=send_body).execute()
            return {"success": True, "id": sent.get("id"), "message_id": msg.get("Message-ID")}
        except Exception as e:
            logger.error(f"[BrandGmail] HTML thread reply error for brand {brand.get('id')}; error_type={type(e).__name__}")
            return {"success": False, "error": str(e)}

    async def send_reply_in_thread(self, brand: dict, to_email: str, subject: str, body: str, thread_id: str) -> Dict[str, Any]:
        """Async boundary for _send_reply_in_thread_sync — see send_email's
        own docstring for why (fully synchronous Google API client)."""
        return await asyncio.to_thread(self._send_reply_in_thread_sync, brand, to_email, subject, body, thread_id)

    def _send_reply_in_thread_sync(self, brand: dict, to_email: str, subject: str, body: str, thread_id: str) -> Dict[str, Any]:
        """Send a reply in an existing Gmail thread (e.g. CSAT follow-up)."""
        svc = self._build_service(brand)
        if not svc:
            return {"success": False, "error": "Gmail not connected for this brand"}
        try:
            msg = self._build_message(body)
            msg["to"]      = to_email
            msg["subject"] = subject if subject.startswith("Re:") else f"Re: {subject}"
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            sent = svc.users().messages().send(
                userId="me",
                body={"raw": raw, "threadId": thread_id},
            ).execute()
            return {"success": True, "id": sent.get("id")}
        except Exception as e:
            logger.error(f"[BrandGmail] Thread reply error for brand {brand.get('id')}; error_type={type(e).__name__}")
            return {"success": False, "error": str(e)}

    def get_connected_brands(self) -> List[dict]:
        """Legacy inbox polling is retired; no Gmail inbox accounts are polled."""
        return []


brand_gmail_service = BrandGmailService()

