"""
Supabase Auth (GoTrue) REST Client
===================================
Thin wrapper over Supabase's Auth HTTP API — mirrors the existing
`supabase_client.py` pattern (raw `requests` calls, no supabase-py SDK) so
auth calls share the same dependency footprint as the rest of the app.

Uses the anon key, exactly as Supabase's own JS client does for these
endpoints — it is safe by design to use from a backend as well as a
browser. The service-role key is never used here and never reaches the
frontend.
"""
import os
import logging
from typing import Optional, Dict, Any

import requests

logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

_session = requests.Session()


class GoTrueError(Exception):
    """Raised for any non-2xx response from Supabase Auth, with the parsed message."""

    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _headers(access_token: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Content-Type": "application/json",
    }
    headers["Authorization"] = f"Bearer {access_token or SUPABASE_ANON_KEY}"
    return headers


def _extract_error(resp: requests.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:300] or f"HTTP {resp.status_code}"
    return (
        body.get("error_description")
        or body.get("msg")
        or body.get("error")
        or body.get("message")
        or f"HTTP {resp.status_code}"
    )


def _url(path: str) -> str:
    return f"{SUPABASE_URL}/auth/v1/{path.lstrip('/')}"


_TIMEOUT = 15  # seconds — GoTrue calls are on the auth critical path; fail fast rather than hang a request.


def sign_up(email: str, password: str) -> Dict[str, Any]:
    """
    Create a new Supabase Auth user with email+password.

    Returns the full GoTrue response. If the project has "Confirm email"
    enabled, `session` will be null and only `user` is populated — the
    caller must not treat that as a login.
    """
    resp = _session.post(_url("signup"), headers=_headers(), json={"email": email, "password": password}, timeout=_TIMEOUT)
    if resp.status_code >= 400:
        raise GoTrueError(_extract_error(resp), resp.status_code)
    data = resp.json()
    # Supabase returns a 200 for a duplicate email instead of an error
    # (anti-enumeration behavior), but the exact shape varies: sometimes
    # `user` has an empty `identities` array, sometimes `user` is null
    # outright. A genuine new signup pending confirmation always comes back
    # with a real `user.id` even without a session — so "no session and no
    # usable user.id" is the reliable signal for "this email already
    # exists," not just the empty-identities case.
    user = data.get("user") or {}
    if not data.get("session") and (not user.get("id") or user.get("identities") == []):
        raise GoTrueError("Email already registered", 400)
    return data


def sign_in_with_password(email: str, password: str) -> Dict[str, Any]:
    """Authenticate with email+password. Returns the session (access/refresh tokens + user)."""
    resp = _session.post(
        _url("token"),
        headers=_headers(),
        params={"grant_type": "password"},
        json={"email": email, "password": password},
        timeout=_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise GoTrueError(_extract_error(resp), resp.status_code)
    return resp.json()


def sign_in_with_id_token(id_token: str, provider: str = "google") -> Dict[str, Any]:
    """Exchange a Google (or other provider) ID token for a Supabase session."""
    resp = _session.post(
        _url("token"),
        headers=_headers(),
        params={"grant_type": "id_token"},
        json={"provider": provider, "id_token": id_token},
        timeout=_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise GoTrueError(_extract_error(resp), resp.status_code)
    return resp.json()


def refresh_session(refresh_token: str) -> Dict[str, Any]:
    """Exchange a refresh token for a new access/refresh token pair."""
    resp = _session.post(
        _url("token"),
        headers=_headers(),
        params={"grant_type": "refresh_token"},
        json={"refresh_token": refresh_token},
        timeout=_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise GoTrueError(_extract_error(resp), resp.status_code)
    return resp.json()


def sign_out(access_token: str) -> None:
    """Invalidate the session's refresh token."""
    resp = _session.post(_url("logout"), headers=_headers(access_token), timeout=_TIMEOUT)
    if resp.status_code >= 400 and resp.status_code != 401:
        # Already-invalid tokens (401) are a no-op success from the caller's
        # perspective — they wanted to be logged out, and now they are.
        logger.warning(f"[GoTrue] Logout returned {resp.status_code}: {_extract_error(resp)}")


def _generate_admin_link(link_type: str, email: str, password: Optional[str] = None, redirect_to: Optional[str] = None) -> Optional[str]:
    """
    Generate an action_link via Supabase's Admin API, without Supabase
    sending any email itself or involving the Send Email Hook at all —
    this is Supabase's own documented mechanism for custom email delivery
    ("Generates email links and OTPs to be sent via a custom email
    provider"). The caller is responsible for emailing the returned link.

    Deliberately synchronous and hook-free: the Send Email Hook approach
    this replaced was tied to Supabase's own hard 5-second webhook
    timeout, which a cold or slow backend instance can blow past even when
    the SMTP send itself would have succeeded a moment later. Generating
    the link here happens inside our own request, under our own timeout
    budget, with no external deadline imposed on us.

    Requires the service-role key — the only calls in this module that do,
    since /admin/* GoTrue endpoints are privileged and every other
    function here deliberately uses only the anon key. Never raises;
    returns None on any failure (including "no such account") since
    callers like request_password_reset() must not reveal whether the
    email exists either way.
    """
    if not SUPABASE_SERVICE_ROLE_KEY:
        logger.error(f"[GoTrue] SUPABASE_SERVICE_ROLE_KEY not configured — cannot generate {link_type} link")
        return None

    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    body: Dict[str, Any] = {"type": link_type, "email": email}
    if password:
        body["password"] = password
    if redirect_to:
        body["redirect_to"] = redirect_to

    try:
        resp = _session.post(_url("admin/generate_link"), headers=headers, json=body, timeout=_TIMEOUT)
    except requests.RequestException as e:
        logger.warning(f"[GoTrue] {link_type} link generation request failed: {e}")
        return None

    if resp.status_code >= 400:
        logger.warning(f"[GoTrue] {link_type} link generation returned {resp.status_code}: {_extract_error(resp)}")
        return None

    return resp.json().get("action_link")


def generate_recovery_link(email: str, redirect_to: Optional[str] = None) -> Optional[str]:
    """Generate a password-reset action_link. See _generate_admin_link for
    why this bypasses Supabase's own mailer/Send Email Hook entirely."""
    return _generate_admin_link("recovery", email, redirect_to=redirect_to)


def generate_signup_confirmation_link(email: str, password: str, redirect_to: Optional[str] = None) -> Optional[str]:
    """Generate a signup-confirmation action_link the same hook-free way
    generate_recovery_link does, for the same underlying problem: the
    confirmation email Supabase's own signup call was supposed to send
    (via its default mailer or the Send Email Hook) was never reliably
    reaching new signups.

    Requires the password the user already chose — Supabase's admin
    generate_link with type=signup upserts the (already-existing, from the
    earlier public /signup call) user record, so passing the same password
    back keeps it unchanged rather than resetting it to something new."""
    return _generate_admin_link("signup", email, password=password, redirect_to=redirect_to)


def update_user_password(access_token: str, new_password: str) -> Dict[str, Any]:
    """Set a new password for the user identified by `access_token` (a normal session or a recovery session)."""
    resp = _session.put(_url("user"), headers=_headers(access_token), json={"password": new_password}, timeout=_TIMEOUT)
    if resp.status_code >= 400:
        raise GoTrueError(_extract_error(resp), resp.status_code)
    return resp.json()
