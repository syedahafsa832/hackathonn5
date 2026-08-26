"""
Authentication Service for Multi-Tenant SaaS
=============================================
Handles tenant registration/login and tenant context.

Identity now lives in Supabase Auth (auth.users) — this service delegates
all credential handling (password hashing, Google verification, sessions)
to Supabase's GoTrue API via `supabase_gotrue`, and is responsible only for
mapping an authenticated Supabase user to a tResolv tenant.
"""
import os
import logging
import hashlib
import secrets
import time
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

import jwt
from passlib.context import CryptContext

from src.lib.supabase_client import supabase_select, supabase_insert, supabase_update
from src.services import supabase_gotrue, system_email_service
from src.services.supabase_gotrue import GoTrueError
from src.services.supabase_auth_service import supabase_auth_service
from src.services.plan_service import TRIAL_DAYS

logger = logging.getLogger(__name__)

# Same variable shopify_auth.py and cors.py already use for redirect/CORS
# config — reused here rather than introducing a second "app URL" name, so
# password-reset links use the correct domain per environment automatically.
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

# --- Legacy custom-JWT config (deprecated) ---------------------------------
# Kept only so tenant_auth can still verify access tokens issued before the
# Supabase Auth migration, until they naturally expire (ACCESS_TOKEN_EXPIRE_
# MINUTES old max lifetime) or the user logs out/back in. Nothing new is
# issued with these anymore — see hash_password/verify_password/
# create_access_token/decode_token docstrings below.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
JWT_SECRET = os.getenv("JWT_SECRET", os.getenv("SECRET_KEY", "change-this-in-production"))
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440  # 24 hours — legacy token lifetime only
REFRESH_TOKEN_EXPIRE_DAYS = 30

FOUNDING_COHORT_CAP = 20
FOUNDING_DAILY_TICKET_LIMIT = 5

# Per-email cooldown for password-reset requests, on top of the existing
# per-IP slowapi limit on the route itself (10/minute in saas_auth.py). A
# plain in-process dict — not persisted, not shared across worker
# processes/instances — is a real limitation under multi-worker deployment,
# but requires no schema change and stops the common single-attacker/
# single-process spam case immediately. See PASSWORD_RESET_FLOW notes for
# the tradeoff.
_PASSWORD_RESET_COOLDOWN_SECONDS = 60
_last_password_reset_request: Dict[str, float] = {}

# A registered email does strictly more work than an unregistered one (an
# extra Resend API call), which without padding would make response time
# itself an enumeration side channel even though the response body is
# already identical either way.
_PASSWORD_RESET_MIN_RESPONSE_SECONDS = 1.0


class FoundingCohortFullError(Exception):
    """Raised when a brand-new tenant would exceed the Founding 20 cap."""


class AuthService:
    """Handles tenant registration/login/session management on top of Supabase Auth."""

    # ==================== Deprecated: legacy custom JWT ====================
    # Not used by any active registration/login path anymore. Left in place
    # only because tenant_auth.py must still be able to verify a custom
    # token that was already handed to a browser before this migration
    # shipped (it will simply stop working once it expires or the user logs
    # out — no new ones are ever created).

    def hash_password(self, password: str) -> str:
        """Deprecated — passwords are no longer stored or hashed by this app; Supabase Auth owns them."""
        return pwd_context.hash(password)

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Deprecated — retained only to read pre-migration tenants.password_hash rows if ever needed."""
        try:
            return pwd_context.verify(plain_password, hashed_password)
        except Exception:
            return False

    def create_access_token(self, tenant_id: str, email: str) -> str:
        """Deprecated — no longer issued. Supabase Auth issues all new session tokens."""
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        payload = {
            "sub": tenant_id,
            "email": email,
            "type": "access",
            "exp": expire,
            "iat": datetime.now(timezone.utc)
        }
        return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    def create_refresh_token(self) -> str:
        """Deprecated — no longer issued. Supabase Auth manages refresh tokens."""
        return secrets.token_urlsafe(64)

    def decode_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Deprecated — tenant_auth verifies via supabase_auth_service.verify_jwt() instead, which
        handles both legacy tokens (this same secret, as a fallback) and real Supabase Auth tokens."""
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            return payload
        except jwt.ExpiredSignatureError:
            logger.warning("Token expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid token: {e}")
            return None

    # ==================== Tenant resolution ====================

    async def resolve_or_create_tenant_for_supabase_user(
        self,
        supabase_user_id: str,
        email: str,
        full_name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Map an authenticated Supabase Auth user to a tResolv tenant.

        1. Look up by supabase_user_id (the common case after first login).
        2. Fall back to email — links a pre-migration tenant (or one created
           by a differently-timed request) to this Supabase identity instead
           of creating a duplicate. This is also what makes "signed up with
           Google, later signs in with the same email via password" (and
           vice versa) resolve to the same tenant, since Supabase treats
           those as the same auth.users row when the email matches.
        3. Otherwise create a brand-new tenant + default brand, exactly like
           the old register() used to.

        Raises FoundingCohortFullError instead of creating a tenant past the
        Founding 20 cap.
        """
        email = email.strip().lower()

        matches = supabase_select("tenants", {"supabase_user_id": f"eq.{supabase_user_id}"})
        if matches:
            return matches[0]

        matches = supabase_select("tenants", {"email": f"eq.{email}"})
        if matches:
            tenant = matches[0]
            if not tenant.get("supabase_user_id"):
                updated = supabase_update("tenants", {"id": f"eq.{tenant['id']}"}, {
                    "supabase_user_id": supabase_user_id,
                })
                if updated:
                    tenant = updated
                logger.info(f"[Auth] Linked pre-existing tenant {tenant['id']} to Supabase user {supabase_user_id}")
            return tenant

        # Founding 20 hard cap — first 20 orgs get the free founding cohort
        # plan, everyone after gets sent to the waitlist instead of silently
        # overloading the free Mistral/Render tier.
        founding_count = supabase_select("tenants", {
            "founding_cohort": "eq.true",
            "select": "id",
        })
        if len(founding_count or []) >= FOUNDING_COHORT_CAP:
            raise FoundingCohortFullError()

        now = datetime.now(timezone.utc)
        tenant_data = {
            "email": email,
            "password_hash": None,
            "supabase_user_id": supabase_user_id,
            "company_name": full_name,
            "is_active": True,
            "shopify_connected": False,
            "plan": "trial",
            "trial_start_at": now.isoformat(),
            "trial_end_at": (now + timedelta(days=TRIAL_DAYS)).isoformat(),
            "founding_cohort": True,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        tenant = supabase_insert("tenants", tenant_data)
        tenant_id = tenant.get("id")
        if not tenant_id:
            return None

        self._create_default_brand(tenant_id, email, full_name)
        logger.info(f"[Auth] New tenant created for Supabase user {supabase_user_id}: {email}")
        return tenant

    def _create_default_brand(self, tenant_id: str, email: str, company_name: Optional[str]) -> None:
        """
        Create a default brand (+ paused system_settings) for a newly created tenant.

        This ensures _get_tenant_brand_ids() always returns a brand for this
        tenant, and that Gmail/Shopify connect (which both 400 with "No brand
        found" if none exists) work immediately after signup.

        brands' original schema (migration 002) has shopify_shop_name,
        shopify_access_token, and support_email as NOT NULL — this insert
        deliberately doesn't set the first two (a placeholder value would
        make Shopify-connection checks elsewhere read as falsely
        "connected"), so it depends on migration 026 having relaxed those
        constraints. support_email defaults to the tenant's own email — a
        reasonable default until they configure a dedicated one.
        """
        try:
            brand_name = company_name or f"{email.split('@')[0].title()}'s Store"
            created_brand = supabase_insert("brands", {
                "name": brand_name,
                "is_active": True,
                "tenant_id": tenant_id,
                "gmail_connected": False,
                "support_email": email.lower().strip(),
            })
            logger.info(f"[Auth] Default brand '{brand_name}' ({created_brand.get('id')}) created for tenant {tenant_id}")

            # Safe go-live gate: new brands start paused until the merchant
            # explicitly activates in onboarding. This ONLY affects brands
            # created from this point forward — get_system_settings() falls
            # back to ai_mode="active" when no system_settings row exists at
            # all, which is exactly the case for every brand created before
            # this change, so existing live merchants are untouched. Do not
            # change that fallback; it's what keeps this backward-compatible.
            brand_id = created_brand.get("id")
            if brand_id:
                try:
                    supabase_insert("system_settings", {
                        "store_id": brand_id,
                        "ai_mode": "paused",
                        "confidence_threshold": 0.65,
                    })
                    logger.info(f"[Auth] Brand {brand_id} initialized with ai_mode=paused pending Go Live")
                except Exception as settings_err:
                    # Not fatal — get_system_settings() falls back to "active" if this
                    # row is missing, same as it always has. Logged because that
                    # fallback means this specific brand would (unintentionally) be
                    # live from Gmail-connect instead of paused as designed.
                    logger.error(
                        f"[Auth] Failed to create paused system_settings for new brand "
                        f"{brand_id}: {settings_err} — this brand will default to ai_mode=active"
                    )
        except Exception as brand_err:
            # Registration itself still succeeds — a tenant with no brand is
            # recoverable, a fully-failed signup isn't. But this must NOT be
            # silent: without a brand, Gmail/Shopify connect will 400 for this
            # tenant until someone notices and fixes it manually.
            logger.error(
                f"[Auth] REGISTRATION SUCCEEDED BUT DEFAULT BRAND CREATION FAILED for "
                f"tenant {tenant_id} ({email}) — Gmail/Shopify connect will fail until a "
                f"brand is created manually. Error: {brand_err}"
            )

    def _session_response(self, session: Dict[str, Any], tenant: Dict[str, Any]) -> Dict[str, Any]:
        """Build the AuthResponse-shaped dict the frontend expects, from a GoTrue session + tenant row."""
        return {
            "success": True,
            "tenant_id": tenant["id"],
            "email": tenant["email"],
            "company_name": tenant.get("company_name"),
            "shopify_connected": tenant.get("shopify_connected", False),
            "access_token": session.get("access_token"),
            "refresh_token": session.get("refresh_token"),
            "token_type": session.get("token_type", "bearer"),
            "expires_in": session.get("expires_in"),
        }

    @staticmethod
    def _founding_cohort_full_response() -> Dict[str, Any]:
        return {
            "success": False,
            "error": "founding_cohort_full",
            "message": "Our Founding 20 program is full. Join the waitlist instead.",
            "waitlist_url": "https://tresolv.online/waitlist",
        }

    # ==================== Public auth flows ====================

    async def check_daily_ticket_limit(self, tenant_id: Optional[str]) -> bool:
        """Deprecated boolean wrapper — kept for existing callers.
        Real logic now lives in plan_service.can_process_ticket(), which is
        plan/trial/super-admin aware and tracks usage without a per-call
        COUNT query across every brand. New call sites should use that
        directly (it also returns remaining/limit/reset_at for API responses)."""
        from src.services.plan_service import can_process_ticket
        return can_process_ticket(tenant_id).get("allowed", True)

    async def register(self, email: str, password: str, company_name: str = None) -> Dict[str, Any]:
        """
        Register a new tenant via Supabase Auth email/password signup.

        If the Supabase project requires email confirmation, no session is
        returned yet — the tenant is still created immediately (so a
        subsequent login after confirming maps straight to it), but the
        caller must tell the user to check their inbox instead of logging
        them straight in.
        """
        email = email.strip().lower()
        if len(password) < 8:
            return {"success": False, "error": "Password must be at least 8 characters"}

        try:
            signup = supabase_gotrue.sign_up(email, password)
        except GoTrueError as e:
            if "already" in e.message.lower() or e.status_code == 422:
                return {"success": False, "error": "Email already registered"}
            logger.error(f"[Auth] Supabase signup error: {e.message}")
            return {"success": False, "error": e.message}

        user = signup.get("user") or {}
        supabase_user_id = user.get("id")
        if not supabase_user_id:
            return {"success": False, "error": "Registration failed"}

        try:
            tenant = await self.resolve_or_create_tenant_for_supabase_user(supabase_user_id, email, company_name)
        except FoundingCohortFullError:
            return self._founding_cohort_full_response()

        if not tenant:
            return {"success": False, "error": "Failed to create account"}

        logger.info(f"[Auth] New tenant registered: {email}")

        session = signup.get("session")
        if not session:
            return {
                "success": True,
                "tenant_id": tenant["id"],
                "email": email,
                "company_name": tenant.get("company_name"),
                "email_confirmation_required": True,
            }

        return self._session_response(session, tenant)

    async def login(self, email: str, password: str) -> Dict[str, Any]:
        """Authenticate via Supabase Auth email/password and resolve the tenant."""
        email = email.strip().lower()

        try:
            session = supabase_gotrue.sign_in_with_password(email, password)
        except GoTrueError as e:
            # "Email not confirmed" is worth surfacing distinctly — it's
            # actionable and doesn't meaningfully add to what an attacker
            # could already infer from the signup form's own duplicate-email
            # check. Everything else stays generic.
            if "confirm" in e.message.lower():
                return {"success": False, "error": e.message}
            logger.info(f"[Auth] Login failed for {email}: {e.message}")
            return {"success": False, "error": "Invalid email or password"}

        user = session.get("user") or {}
        supabase_user_id = user.get("id")
        if not supabase_user_id:
            return {"success": False, "error": "Invalid email or password"}

        try:
            tenant = await self.resolve_or_create_tenant_for_supabase_user(supabase_user_id, email)
        except FoundingCohortFullError:
            return self._founding_cohort_full_response()

        if not tenant:
            return {"success": False, "error": "Account not found"}

        if not tenant.get("is_active"):
            return {"success": False, "error": "Account is disabled"}

        supabase_update("tenants", {"id": f"eq.{tenant['id']}"}, {
            "last_login_at": datetime.now(timezone.utc).isoformat()
        })

        logger.info(f"[Auth] Tenant logged in: {email}")
        return self._session_response(session, tenant)

    async def google_auth(self, credential: str) -> Dict[str, Any]:
        """
        Sign in or register a tenant from a Google Identity Services ID token,
        via Supabase Auth's Google provider (grant_type=id_token). Supabase
        verifies the token and creates/authenticates the auth.users row —
        this only maps that identity to a tenant.
        """
        try:
            session = supabase_gotrue.sign_in_with_id_token(credential, provider="google")
        except GoTrueError as e:
            logger.warning(f"[Auth] Google sign-in failed: {e.message}")
            return {"success": False, "error": "Google sign-in failed"}

        user = session.get("user") or {}
        supabase_user_id = user.get("id")
        email = (user.get("email") or "").strip().lower()
        if not supabase_user_id or not email:
            return {"success": False, "error": "Google sign-in failed"}

        metadata = user.get("user_metadata") or {}
        full_name = metadata.get("full_name") or metadata.get("name")

        try:
            tenant = await self.resolve_or_create_tenant_for_supabase_user(supabase_user_id, email, full_name)
        except FoundingCohortFullError:
            return self._founding_cohort_full_response()

        if not tenant:
            return {"success": False, "error": "Failed to create account"}

        if not tenant.get("is_active"):
            return {"success": False, "error": "Account is disabled"}

        supabase_update("tenants", {"id": f"eq.{tenant['id']}"}, {
            "last_login_at": datetime.now(timezone.utc).isoformat()
        })

        logger.info(f"[Auth] Tenant authenticated via Google: {email}")
        return self._session_response(session, tenant)

    async def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """Exchange a Supabase refresh token for a new access/refresh token pair."""
        try:
            session = supabase_gotrue.refresh_session(refresh_token)
        except GoTrueError as e:
            logger.info(f"[Auth] Token refresh failed: {e.message}")
            return {"success": False, "error": "Invalid refresh token"}

        return {
            "success": True,
            "access_token": session.get("access_token"),
            "refresh_token": session.get("refresh_token"),
            "token_type": session.get("token_type", "bearer"),
            "expires_in": session.get("expires_in"),
        }

    async def logout(self, access_token: str) -> Dict[str, Any]:
        """Invalidate the current Supabase session."""
        try:
            supabase_gotrue.sign_out(access_token)
        except Exception as e:
            # Logout must not fail the request — the frontend clears its
            # local tokens regardless, so the user is logged out client-side
            # either way.
            logger.warning(f"[Auth] Logout error (ignored): {e}")
        return {"success": True, "message": "Logged out successfully"}

    async def request_password_reset(self, email: str) -> Dict[str, Any]:
        """
        Send a password-reset email. Always reports the same success
        message, whether or not the email is registered, and whether or
        not this specific call actually triggered anything (see the
        cooldown below) — anti-enumeration by construction.

        Generates the recovery link via Supabase's Admin API and sends it
        ourselves, synchronously, in this same request — deliberately not
        via Supabase's Send Email Hook, which is bound to Supabase's own
        hard 5-second webhook timeout regardless of how long our SMTP send
        actually takes. That external deadline caused real production
        failures (the hook would still be mid-send when Supabase gave up
        waiting). Doing it inline here means the only timeout that applies
        is our own.
        """
        email = email.strip().lower()
        generic_response = {"success": True, "message": "If that email is registered, a reset link has been sent."}

        now = time.monotonic()
        last = _last_password_reset_request.get(email)
        if last is not None and (now - last) < _PASSWORD_RESET_COOLDOWN_SECONDS:
            # Same generic response either way — a cooldown-skipped request
            # must be indistinguishable from a real one.
            return generic_response
        _last_password_reset_request[email] = now

        start = time.monotonic()
        redirect_to = f"{FRONTEND_URL}/reset-password"
        action_link = supabase_gotrue.generate_recovery_link(email, redirect_to=redirect_to)
        if action_link:
            system_email_service.send_password_reset_email(email, action_link)

        elapsed = time.monotonic() - start
        if elapsed < _PASSWORD_RESET_MIN_RESPONSE_SECONDS:
            await asyncio.sleep(_PASSWORD_RESET_MIN_RESPONSE_SECONDS - elapsed)

        return generic_response

    async def confirm_password_reset(self, recovery_access_token: str, new_password: str) -> Dict[str, Any]:
        """
        Set a new password using the access token from a Supabase recovery
        link.

        Requires the token to actually be recovery-issued, not just any
        valid session — Supabase's own "update password with any valid
        access token" API doesn't distinguish the two, so an ordinary
        logged-in session's token would otherwise work here too, silently
        bypassing change_password()'s current-password check. This app has
        no magic-link login (the only other flow that also produces an
        "otp"-method token), so amr containing "otp" is a reliable signal
        that this token came from the recovery-link flow specifically.
        """
        if len(new_password) < 8:
            return {"success": False, "error": "Password must be at least 8 characters"}

        payload = supabase_auth_service.verify_jwt(recovery_access_token)
        if not payload:
            return {"success": False, "error": "Reset link is invalid or expired"}

        amr_methods = {entry.get("method") for entry in (payload.get("amr") or [])}
        if "otp" not in amr_methods:
            logger.warning("[Auth] Password reset confirmation rejected: token is not from a recovery link")
            return {"success": False, "error": "Reset link is invalid or expired"}

        try:
            supabase_gotrue.update_user_password(recovery_access_token, new_password)
        except GoTrueError as e:
            logger.warning(f"[Auth] Password reset confirmation failed: {e.message}")
            return {"success": False, "error": "Reset link is invalid or expired"}
        return {"success": True, "message": "Password updated. You can now sign in."}

    async def change_password(
        self,
        tenant_id: str,
        email: str,
        current_password: str,
        new_password: str,
        access_token: str,
    ) -> Dict[str, Any]:
        """
        Change the authenticated tenant's password.

        Re-verifies current_password by attempting a real Supabase sign-in
        with it (Supabase Auth doesn't expose a "verify without a session"
        endpoint) rather than comparing against a locally stored hash.
        """
        if len(new_password) < 8:
            return {"success": False, "error": "New password must be at least 8 characters"}

        try:
            supabase_gotrue.sign_in_with_password(email, current_password)
        except GoTrueError:
            # Supabase returns the same generic "invalid credentials" error
            # whether the password was simply wrong or the account never had
            # one at all (e.g. Google-only sign-up) - anti-enumeration by
            # design, so it can't be told apart from the sign-in attempt
            # alone. Check the verified token's own provider list instead:
            # a Google-only account never gained an "email" identity, so it
            # has no password to be "incorrect" in the first place.
            payload = supabase_auth_service.verify_jwt(access_token)
            providers = ((payload or {}).get("app_metadata") or {}).get("providers") or []
            if "email" not in providers:
                return {
                    "success": False,
                    "error": "This account signed up with Google and doesn't have a password yet. "
                             "Use \"Forgot password\" on the sign-in page to set one.",
                }
            return {"success": False, "error": "Current password is incorrect"}

        try:
            supabase_gotrue.update_user_password(access_token, new_password)
        except GoTrueError as e:
            logger.error(f"[Auth] Password update failed for tenant {tenant_id}: {e.message}")
            return {"success": False, "error": "Failed to update password"}

        return {"success": True, "message": "Password changed successfully"}

    # ==================== Tenant record helpers (unchanged by the migration) ====================

    async def get_tenant(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Get tenant by ID (excluding sensitive fields)."""
        try:
            tenants = supabase_select("tenants", {"id": f"eq.{tenant_id}"})
            if not tenants:
                return None

            tenant = tenants[0]

            # Return safe fields only
            return {
                "id": tenant.get("id"),
                "email": tenant.get("email"),
                "company_name": tenant.get("company_name"),
                "shopify_domain": tenant.get("shopify_domain"),
                "shopify_connected": tenant.get("shopify_connected"),
                "shopify_shop_name": tenant.get("shopify_shop_name"),
                "support_email": tenant.get("support_email"),
                "auto_approve_threshold": tenant.get("auto_approve_threshold"),
                "is_active": tenant.get("is_active"),
                "created_at": tenant.get("created_at"),
                "last_login_at": tenant.get("last_login_at")
            }

        except Exception as e:
            logger.error(f"[Auth] Get tenant error: {e}")
            return None

    async def update_tenant(self, tenant_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update tenant settings (safe fields only)."""
        try:
            # Only allow updating certain fields
            allowed_fields = {
                "company_name", "support_email", "auto_approve_threshold", "timezone"
            }
            safe_updates = {k: v for k, v in updates.items() if k in allowed_fields}

            if not safe_updates:
                return {"success": False, "error": "No valid fields to update"}

            safe_updates["updated_at"] = datetime.now(timezone.utc).isoformat()

            supabase_update("tenants", {"id": f"eq.{tenant_id}"}, safe_updates)

            return {"success": True, "message": "Settings updated"}

        except Exception as e:
            logger.error(f"[Auth] Update tenant error: {e}")
            return {"success": False, "error": str(e)}

    async def _store_refresh_token(
        self,
        tenant_id: str,
        refresh_token: str,
        user_agent: str = None,
        ip_address: str = None
    ):
        """Deprecated — Supabase Auth manages its own refresh tokens; the `sessions` table
        is no longer written to by any active flow. Left in place, unused, rather than
        dropping the table/method outright."""
        try:
            token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
            expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

            session_data = {
                "tenant_id": tenant_id,
                "refresh_token_hash": token_hash,
                "user_agent": user_agent,
                "ip_address": ip_address,
                "expires_at": expires_at.isoformat(),
                "created_at": datetime.now(timezone.utc).isoformat()
            }

            supabase_insert("sessions", session_data)

        except Exception as e:
            logger.error(f"[Auth] Failed to store refresh token: {e}")


# Singleton instance
auth_service = AuthService()
