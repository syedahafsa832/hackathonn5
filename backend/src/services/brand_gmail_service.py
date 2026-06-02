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
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from src.lib.supabase_client import supabase_select, supabase_update

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/gmail.modify']
_STATE_TTL = 600  # 10-minute OAuth window


def _state_key() -> bytes:
    return os.getenv("SECRET_KEY", os.getenv("JWT_SECRET", "change-me-in-production")).encode()


def _sign_state(brand_id: str) -> str:
    """Return HMAC-signed state token: base64(payload).signature
    The payload encodes brand_id and an expiry, making tampering detectable."""
    payload = json.dumps({"brand_id": brand_id, "exp": int(time.time()) + _STATE_TTL}).encode()
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


def _get_client_config() -> dict:
    """Build OAuth client config from env vars."""
    client_id = os.getenv("GMAIL_CLIENT_ID")
    client_secret = os.getenv("GMAIL_CLIENT_SECRET")

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

    # Fall back to full GMAIL_CREDENTIALS blob
    raw = os.getenv("GMAIL_CREDENTIALS")
    if raw:
        return json.loads(raw)

    raise ValueError("No Gmail OAuth credentials configured. Set GMAIL_CLIENT_ID + GMAIL_CLIENT_SECRET.")


def _callback_uri() -> str:
    base = os.getenv("API_BASE_URL", "http://localhost:8001")
    return f"{base}/api/brands/gmail/callback"


class BrandGmailService:

    # ── OAuth ──────────────────────────────────────────────────────────────

    def get_auth_url(self, brand_id: str) -> str:
        """Return the Google consent-screen URL for a brand.
        The state parameter is HMAC-signed so the callback can verify authenticity."""
        flow = Flow.from_client_config(_get_client_config(), scopes=SCOPES)
        flow.redirect_uri = _callback_uri()
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            state=_sign_state(brand_id),  # signed — not plain brand_id
            prompt="consent",
        )
        return auth_url

    async def handle_callback(self, state: str, code: str) -> Dict[str, Any]:
        """Exchange auth code → tokens, save to brand, return gmail address.
        Verifies the signed state before trusting the brand_id it encodes."""
        brand_id = _verify_state(state)
        if not brand_id:
            return {"success": False, "error": "invalid_or_expired_state"}
        try:
            flow = Flow.from_client_config(_get_client_config(), scopes=SCOPES, state=state)
            flow.redirect_uri = _callback_uri()
            flow.fetch_token(code=code)
            creds = flow.credentials

            # Discover which Gmail address was granted
            svc = build("gmail", "v1", credentials=creds)
            profile = svc.users().getProfile(userId="me").execute()
            email = profile.get("emailAddress", "")

            token_data = {
                "token":         creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri":     creds.token_uri,
                "client_id":     creds.client_id,
                "client_secret": creds.client_secret,
                "scopes":        list(creds.scopes or SCOPES),
                "expiry":        creds.expiry.isoformat() if creds.expiry else None,
            }

            supabase_update("brands", {"id": f"eq.{brand_id}"}, {
                "gmail_email":     email,
                "gmail_token":     json.dumps(token_data),
                "gmail_connected": True,
                "is_active":       True,  # Reactivate if deactivated during onboarding 409 flow
                "updated_at":      datetime.now(timezone.utc).isoformat(),
            })

            logger.info(f"[BrandGmail] Connected {email} to brand {brand_id}")
            return {"success": True, "email": email}

        except Exception as e:
            logger.error(f"[BrandGmail] Callback error for brand {brand_id}: {e}")
            return {"success": False, "error": str(e)}

    def disconnect(self, brand_id: str):
        supabase_update("brands", {"id": f"eq.{brand_id}"}, {
            "gmail_connected": False,
            "gmail_token":     None,
            "gmail_email":     None,
            "updated_at":      datetime.now(timezone.utc).isoformat(),
        })

    # ── Gmail service ──────────────────────────────────────────────────────

    def _build_service(self, brand: dict):
        """Build an authenticated Gmail API client from stored brand token."""
        raw = brand.get("gmail_token")
        if not raw:
            return None
        try:
            data = json.loads(raw)
            expiry = None
            if data.get("expiry"):
                from datetime import datetime
                try:
                    expiry = datetime.fromisoformat(data["expiry"])
                except Exception:
                    pass

            creds = Credentials(
                token=data.get("token"),
                refresh_token=data.get("refresh_token"),
                token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
                client_id=data.get("client_id") or os.getenv("GMAIL_CLIENT_ID"),
                client_secret=data.get("client_secret") or os.getenv("GMAIL_CLIENT_SECRET"),
                scopes=data.get("scopes", SCOPES),
                expiry=expiry,
            )

            # Always refresh if we have a refresh_token — token may have expired
            if creds.refresh_token:
                try:
                    creds.refresh(Request())
                    data["token"] = creds.token
                    data["expiry"] = creds.expiry.isoformat() if creds.expiry else None
                    supabase_update("brands", {"id": f"eq.{brand['id']}"}, {
                        "gmail_token": json.dumps(data)
                    })
                except Exception as e:
                    err_str = str(e).lower()
                    if "invalid_grant" in err_str or "token has been expired or revoked" in err_str:
                        # Refresh token was revoked by the user — mark disconnected so we stop polling
                        supabase_update("brands", {"id": f"eq.{brand['id']}"}, {
                            "gmail_connected": False,
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        })
                        logger.error(
                            f"[BrandGmail] Refresh token revoked for brand {brand.get('id')} "
                            f"({brand.get('gmail_email')}) — marked gmail_connected=False, user must reconnect"
                        )
                    else:
                        # Network error, quota, or transient failure — do NOT disconnect, skip this poll cycle
                        logger.warning(f"[BrandGmail] Token refresh warning for brand {brand.get('id')}: {e}")

            return build("gmail", "v1", credentials=creds)
        except Exception as e:
            logger.error(f"[BrandGmail] Failed to build service for brand {brand.get('id')}: {e}")
            return None

    async def get_new_emails(self, brand: dict, max_results: int = 10, since_dt=None) -> List[Dict]:
        """Fetch + mark-as-read emails for one brand received after since_dt (or unread if no timestamp)."""
        svc = self._build_service(brand)
        if not svc:
            logger.warning(f"[BrandGmail] Could not build Gmail service for brand {brand.get('name')}")
            return []

        try:
            if since_dt:
                # Use epoch seconds for Gmail after: filter (more reliable than date strings)
                epoch = int(since_dt.timestamp())
                q = f"in:inbox after:{epoch}"
            else:
                q = "is:unread in:inbox"
            res = svc.users().messages().list(
                userId="me", q=q, maxResults=max_results
            ).execute()
            messages = res.get("messages", [])
            logger.info(f"[BrandGmail] {len(messages)} unread message(s) in {brand.get('name')} inbox")

            emails = []
            for msg in messages[:max_results]:
                try:
                    full = svc.users().messages().get(userId="me", id=msg["id"]).execute()
                    headers = full["payload"]["headers"]
                    subject = next((h["value"] for h in headers if h["name"] == "Subject"), "No Subject")
                    sender  = next((h["value"] for h in headers if h["name"] == "From"), "Unknown")

                    # Parse "Name <email>" format
                    sender_name  = sender
                    sender_email = sender
                    if "<" in sender and ">" in sender:
                        import re
                        sender_name = sender.split("<")[0].strip()
                        m = re.search(r"<(.+?)>", sender)
                        if m:
                            sender_email = m.group(1)

                    # Mark as read immediately
                    svc.users().messages().batchModify(
                        userId="me",
                        body={"ids": [msg["id"]], "removeLabelIds": ["UNREAD"]},
                    ).execute()

                    emails.append({
                        "id":           msg["id"],
                        "thread_id":    full.get("threadId"),
                        "subject":      subject,
                        "sender_name":  sender_name,
                        "sender_email": sender_email,
                        "body":         full.get("snippet", ""),
                        "brand_id":     brand["id"],
                        "brand_name":   brand.get("name", ""),
                        # Fields used by email_filter_service
                        "label_ids":    full.get("labelIds", []),
                        "headers":      {h["name"].lower(): h["value"] for h in headers},
                    })
                except Exception as e:
                    logger.error(f"[BrandGmail] Error reading message {msg['id']}: {e}")

            return emails

        except Exception as e:
            logger.error(f"[BrandGmail] Error fetching emails for brand {brand.get('name')}: {e}")
            return []

    async def send_email(self, brand: dict, to_email: str, subject: str, body: str) -> Dict[str, Any]:
        """Send an email from a brand's connected Gmail account."""
        svc = self._build_service(brand)
        if not svc:
            return {"success": False, "error": "Gmail not connected for this brand"}
        try:
            msg = MIMEText(body)
            msg["to"]      = to_email
            msg["subject"] = subject
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            sent = svc.users().messages().send(userId="me", body={"raw": raw}).execute()
            return {"success": True, "id": sent.get("id")}
        except Exception as e:
            logger.error(f"[BrandGmail] Send error for brand {brand.get('name')}: {e}")
            return {"success": False, "error": str(e)}

    def get_connected_brands(self) -> List[dict]:
        """Return all brands with Gmail connected (active or not — reactivation may lag)."""
        try:
            results = supabase_select("brands", {
                "gmail_connected": "is.true",
            }) or []
            logger.info(f"[BrandGmail] Found {len(results)} brand(s) with Gmail connected")
            return results
        except Exception as e:
            logger.error(f"[BrandGmail] Error fetching connected brands: {e}")
            return []


brand_gmail_service = BrandGmailService()
