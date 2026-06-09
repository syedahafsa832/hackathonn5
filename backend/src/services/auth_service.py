"""
Authentication Service for Multi-Tenant SaaS
=============================================
Handles user registration, login, JWT tokens, and tenant context.
"""
import os
import logging
import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Tuple
import jwt
from passlib.context import CryptContext

from src.lib.supabase_client import supabase_select, supabase_insert, supabase_update

logger = logging.getLogger(__name__)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT Configuration
JWT_SECRET = os.getenv("JWT_SECRET", os.getenv("SECRET_KEY", "change-this-in-production"))
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440  # 24 hours
REFRESH_TOKEN_EXPIRE_DAYS = 30


class AuthService:
    """Handles authentication and tenant management."""

    def hash_password(self, password: str) -> str:
        """Hash a password for storage."""
        return pwd_context.hash(password)

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash."""
        try:
            return pwd_context.verify(plain_password, hashed_password)
        except Exception:
            return False

    def create_access_token(self, tenant_id: str, email: str) -> str:
        """Create a JWT access token."""
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
        """Create a secure refresh token."""
        return secrets.token_urlsafe(64)

    def decode_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Decode and validate a JWT token."""
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            return payload
        except jwt.ExpiredSignatureError:
            logger.warning("Token expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid token: {e}")
            return None

    async def register(
        self,
        email: str,
        password: str,
        company_name: str = None
    ) -> Dict[str, Any]:
        """
        Register a new tenant account.

        Returns:
            Dict with success status, tenant_id, and tokens
        """
        try:
            # Check if email already exists
            existing = supabase_select("tenants", {"email": f"eq.{email}"})
            if existing:
                return {
                    "success": False,
                    "error": "Email already registered"
                }

            # Validate password strength
            if len(password) < 8:
                return {
                    "success": False,
                    "error": "Password must be at least 8 characters"
                }

            # Create tenant
            tenant_data = {
                "email": email.lower().strip(),
                "password_hash": self.hash_password(password),
                "company_name": company_name,
                "is_active": True,
                "shopify_connected": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }

            result = supabase_insert("tenants", tenant_data)
            tenant_id = result.get("id")

            if not tenant_id:
                return {"success": False, "error": "Failed to create account"}

            # Create a default brand for this tenant immediately on registration.
            # This ensures _get_tenant_brand_ids() always returns a brand for
            # this tenant so their ticket view is isolated from day one.
            try:
                brand_name = company_name or f"{email.split('@')[0].title()}'s Store"
                supabase_insert("brands", {
                    "name": brand_name,
                    "is_active": True,
                    "tenant_id": tenant_id,
                    "gmail_connected": False,
                })
                logger.info(f"[Auth] Default brand created for tenant {tenant_id}")
            except Exception as brand_err:
                # Non-fatal — brand can be created later via Shopify connect
                logger.warning(f"[Auth] Could not create default brand: {brand_err}")

            # Generate tokens
            access_token = self.create_access_token(tenant_id, email)
            refresh_token = self.create_refresh_token()

            # Store refresh token
            await self._store_refresh_token(tenant_id, refresh_token)

            logger.info(f"[Auth] New tenant registered: {email}")

            return {
                "success": True,
                "tenant_id": tenant_id,
                "email": email,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "bearer",
                "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60
            }

        except Exception as e:
            logger.error(f"[Auth] Registration error: {e}")
            return {"success": False, "error": str(e)}

    async def login(self, email: str, password: str) -> Dict[str, Any]:
        """
        Authenticate a tenant and return tokens.

        Returns:
            Dict with tokens or error
        """
        try:
            # Find tenant
            tenants = supabase_select("tenants", {"email": f"eq.{email.lower().strip()}"})
            if not tenants:
                return {"success": False, "error": "Invalid email or password"}

            tenant = tenants[0]

            # Check if active
            if not tenant.get("is_active"):
                return {"success": False, "error": "Account is disabled"}

            # Verify password
            if not self.verify_password(password, tenant.get("password_hash", "")):
                return {"success": False, "error": "Invalid email or password"}

            tenant_id = tenant.get("id")

            # Generate tokens
            access_token = self.create_access_token(tenant_id, email)
            refresh_token = self.create_refresh_token()

            # Store refresh token
            await self._store_refresh_token(tenant_id, refresh_token)

            # Update last login
            supabase_update("tenants", {"id": f"eq.{tenant_id}"}, {
                "last_login_at": datetime.now(timezone.utc).isoformat()
            })

            logger.info(f"[Auth] Tenant logged in: {email}")

            return {
                "success": True,
                "tenant_id": tenant_id,
                "email": email,
                "company_name": tenant.get("company_name"),
                "shopify_connected": tenant.get("shopify_connected", False),
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "bearer",
                "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60
            }

        except Exception as e:
            logger.error(f"[Auth] Login error: {e}")
            return {"success": False, "error": str(e)}

    async def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """
        Get a new access token using a refresh token.
        """
        try:
            # Hash the refresh token to compare
            token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()

            # Find session
            sessions = supabase_select("sessions", {
                "refresh_token_hash": f"eq.{token_hash}"
            })

            if not sessions:
                return {"success": False, "error": "Invalid refresh token"}

            session = sessions[0]

            # Check expiration
            expires_at = datetime.fromisoformat(session.get("expires_at").replace("Z", "+00:00"))
            if expires_at < datetime.now(timezone.utc):
                return {"success": False, "error": "Refresh token expired"}

            tenant_id = session.get("tenant_id")

            # Get tenant
            tenants = supabase_select("tenants", {"id": f"eq.{tenant_id}"})
            if not tenants or not tenants[0].get("is_active"):
                return {"success": False, "error": "Account not found or disabled"}

            tenant = tenants[0]

            # Generate new access token
            access_token = self.create_access_token(tenant_id, tenant.get("email"))

            return {
                "success": True,
                "access_token": access_token,
                "token_type": "bearer",
                "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60
            }

        except Exception as e:
            logger.error(f"[Auth] Token refresh error: {e}")
            return {"success": False, "error": str(e)}

    async def logout(self, refresh_token: str) -> Dict[str, Any]:
        """Invalidate a refresh token (logout)."""
        try:
            token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()

            # Delete the session
            from src.lib.supabase_client import supabase_delete
            supabase_delete("sessions", {"refresh_token_hash": f"eq.{token_hash}"})

            return {"success": True, "message": "Logged out successfully"}

        except Exception as e:
            logger.error(f"[Auth] Logout error: {e}")
            return {"success": False, "error": str(e)}

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

    async def change_password(
        self,
        tenant_id: str,
        current_password: str,
        new_password: str
    ) -> Dict[str, Any]:
        """Change tenant password."""
        try:
            # Get tenant
            tenants = supabase_select("tenants", {"id": f"eq.{tenant_id}"})
            if not tenants:
                return {"success": False, "error": "Account not found"}

            tenant = tenants[0]

            # Verify current password
            if not self.verify_password(current_password, tenant.get("password_hash", "")):
                return {"success": False, "error": "Current password is incorrect"}

            # Validate new password
            if len(new_password) < 8:
                return {"success": False, "error": "New password must be at least 8 characters"}

            # Update password
            supabase_update("tenants", {"id": f"eq.{tenant_id}"}, {
                "password_hash": self.hash_password(new_password),
                "updated_at": datetime.now(timezone.utc).isoformat()
            })

            return {"success": True, "message": "Password changed successfully"}

        except Exception as e:
            logger.error(f"[Auth] Change password error: {e}")
            return {"success": False, "error": str(e)}

    async def _store_refresh_token(
        self,
        tenant_id: str,
        refresh_token: str,
        user_agent: str = None,
        ip_address: str = None
    ):
        """Store a refresh token in the sessions table."""
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
