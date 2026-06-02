"""
Supabase Auth Service
=====================
Integrates with Supabase Auth for user management.
Handles JWT validation, user context, and role-based access.
"""

import os
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from functools import lru_cache
import jwt
from jwt import PyJWKClient
from pydantic import BaseModel, EmailStr

from src.lib.supabase_client import supabase_select, supabase_insert, supabase_update, supabase_delete

logger = logging.getLogger(__name__)


# ==================== Data Models ====================

class UserRole:
    ADMIN = "admin"
    AGENT = "agent"
    READ_ONLY = "read_only"

    @classmethod
    def all_roles(cls) -> List[str]:
        return [cls.ADMIN, cls.AGENT, cls.READ_ONLY]

    @classmethod
    def can_manage(cls, role: str) -> bool:
        """Check if role can manage resources"""
        return role in [cls.ADMIN, cls.AGENT]

    @classmethod
    def is_admin(cls, role: str) -> bool:
        return role == cls.ADMIN


class UserContext(BaseModel):
    """User context from authenticated request"""
    user_id: str
    supabase_auth_id: str
    organization_id: str
    email: str
    full_name: Optional[str] = None
    role: str = UserRole.AGENT
    permissions: List[str] = []
    brands: List[str] = []  # Brand IDs user can access


class OrganizationContext(BaseModel):
    """Organization context"""
    id: str
    name: str
    slug: str
    plan: str = "free"
    plan_limits: Dict[str, Any] = {}
    is_active: bool = True


# ==================== Supabase Auth Service ====================

class SupabaseAuthService:
    """
    Service for Supabase Auth integration.

    Features:
    - JWT validation using Supabase JWKS
    - User context enrichment from database
    - Role-based access control
    - Organization/brand scoping
    """

    def __init__(self):
        self.supabase_url = os.getenv("SUPABASE_URL", "")
        self.supabase_jwt_secret = os.getenv("SUPABASE_JWT_SECRET", os.getenv("JWT_SECRET", os.getenv("SECRET_KEY", "")))
        self.jwks_url = f"{self.supabase_url}/auth/v1/.well-known/jwks.json" if self.supabase_url else None
        self._jwks_client = None

        if not self.supabase_url:
            logger.warning("SUPABASE_URL not set - auth features may be limited")

    @property
    def jwks_client(self) -> Optional[PyJWKClient]:
        """Lazy-load JWKS client"""
        if self._jwks_client is None and self.jwks_url:
            try:
                self._jwks_client = PyJWKClient(self.jwks_url)
            except Exception as e:
                logger.error(f"Failed to initialize JWKS client: {e}")
        return self._jwks_client

    def verify_jwt(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Verify JWT token from Supabase Auth.

        Returns decoded payload if valid, None otherwise.
        """
        try:
            # Try JWKS verification first (preferred)
            if self.jwks_client:
                try:
                    signing_key = self.jwks_client.get_signing_key_from_jwt(token)
                    payload = jwt.decode(
                        token,
                        signing_key.key,
                        algorithms=["RS256"],
                        audience="authenticated",
                        options={"verify_exp": True}
                    )
                    return payload
                except jwt.exceptions.PyJWKClientError:
                    logger.debug("JWKS verification failed, falling back to secret")

            # Fallback to JWT secret (HS256)
            if self.supabase_jwt_secret:
                payload = jwt.decode(
                    token,
                    self.supabase_jwt_secret,
                    algorithms=["HS256"],
                    options={"verify_exp": True}
                )
                return payload

            logger.error("No JWT verification method available")
            return None

        except jwt.ExpiredSignatureError:
            logger.warning("JWT token expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid JWT token: {e}")
            return None
        except Exception as e:
            logger.error(f"JWT verification error: {e}")
            return None

    async def get_user_context(self, supabase_auth_id: str) -> Optional[UserContext]:
        """
        Get full user context from database.

        Args:
            supabase_auth_id: The auth.users.id from Supabase Auth

        Returns:
            UserContext with user details and permissions
        """
        try:
            # Get user from our users table
            users = supabase_select("users", {
                "supabase_auth_id": f"eq.{supabase_auth_id}",
                "is_active": "eq.true"
            })

            if not users:
                logger.debug(f"No v2 user for auth_id: {supabase_auth_id} (v1 token fallback will be tried)")
                return None

            user = users[0]

            # Get user's accessible brands
            brands = supabase_select("brands", {
                "organization_id": f"eq.{user['organization_id']}",
                "is_active": "eq.true"
            })
            brand_ids = [b["id"] for b in brands] if brands else []

            # Update last login
            supabase_update("users", {"id": f"eq.{user['id']}"}, {
                "last_login_at": datetime.now(timezone.utc).isoformat()
            })

            return UserContext(
                user_id=user["id"],
                supabase_auth_id=supabase_auth_id,
                organization_id=user["organization_id"],
                email=user["email"],
                full_name=user.get("full_name"),
                role=user.get("role", UserRole.AGENT),
                permissions=user.get("permissions", []),
                brands=brand_ids
            )

        except Exception as e:
            logger.error(f"Error getting user context: {e}")
            return None

    async def get_organization_context(self, org_id: str) -> Optional[OrganizationContext]:
        """Get organization details"""
        try:
            orgs = supabase_select("organizations", {
                "id": f"eq.{org_id}",
                "is_active": "eq.true"
            })

            if not orgs:
                return None

            org = orgs[0]
            return OrganizationContext(
                id=org["id"],
                name=org["name"],
                slug=org["slug"],
                plan=org.get("plan", "free"),
                plan_limits=org.get("plan_limits", {}),
                is_active=org.get("is_active", True)
            )

        except Exception as e:
            logger.error(f"Error getting organization: {e}")
            return None

    async def create_user_from_auth(
        self,
        supabase_auth_id: str,
        email: str,
        organization_id: str,
        full_name: Optional[str] = None,
        role: str = UserRole.AGENT
    ) -> Optional[Dict[str, Any]]:
        """
        Create a user record linked to Supabase Auth.

        Called after successful Supabase Auth signup.
        """
        try:
            user_data = {
                "supabase_auth_id": supabase_auth_id,
                "organization_id": organization_id,
                "email": email,
                "full_name": full_name,
                "role": role,
                "is_active": True
            }

            result = supabase_insert("users", user_data)
            logger.info(f"Created user record for {email} in org {organization_id}")
            return result

        except Exception as e:
            logger.error(f"Error creating user: {e}")
            return None

    async def create_organization(
        self,
        name: str,
        slug: str,
        billing_email: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Create a new organization"""
        try:
            org_data = {
                "name": name,
                "slug": slug.lower().replace(" ", "-"),
                "billing_email": billing_email,
                "plan": "free",
                "is_active": True
            }

            result = supabase_insert("organizations", org_data)
            logger.info(f"Created organization: {name}")
            return result

        except Exception as e:
            logger.error(f"Error creating organization: {e}")
            return None

    async def signup_with_organization(
        self,
        email: str,
        organization_name: str,
        full_name: Optional[str] = None,
        supabase_auth_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Complete signup flow: create org + admin user.

        This is called after Supabase Auth signup succeeds.
        """
        try:
            # Create organization
            slug = organization_name.lower().replace(" ", "-").replace("_", "-")
            org = await self.create_organization(
                name=organization_name,
                slug=slug,
                billing_email=email
            )

            if not org:
                return {"success": False, "error": "Failed to create organization"}

            # Create admin user
            user = await self.create_user_from_auth(
                supabase_auth_id=supabase_auth_id or "",
                email=email,
                organization_id=org["id"],
                full_name=full_name,
                role=UserRole.ADMIN
            )

            if not user:
                # Rollback org creation
                supabase_delete("organizations", {"id": f"eq.{org['id']}"})
                return {"success": False, "error": "Failed to create user"}

            return {
                "success": True,
                "organization_id": org["id"],
                "user_id": user["id"],
                "role": UserRole.ADMIN
            }

        except Exception as e:
            logger.error(f"Signup error: {e}")
            return {"success": False, "error": str(e)}

    async def invite_user(
        self,
        organization_id: str,
        email: str,
        role: str,
        invited_by: str
    ) -> Dict[str, Any]:
        """Create an invitation for a new team member"""
        import secrets
        from datetime import timedelta

        try:
            token = secrets.token_urlsafe(32)
            expires_at = datetime.now(timezone.utc) + timedelta(days=7)

            invitation = {
                "organization_id": organization_id,
                "email": email,
                "role": role,
                "invited_by": invited_by,
                "token": token,
                "expires_at": expires_at.isoformat()
            }

            result = supabase_insert("invitations", invitation)

            return {
                "success": True,
                "token": token,
                "expires_at": expires_at.isoformat()
            }

        except Exception as e:
            logger.error(f"Invitation error: {e}")
            return {"success": False, "error": str(e)}

    async def accept_invitation(
        self,
        token: str,
        supabase_auth_id: str,
        full_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Accept an invitation and create user"""
        try:
            # Find valid invitation
            invitations = supabase_select("invitations", {
                "token": f"eq.{token}",
                "accepted_at": "is.null"
            })

            if not invitations:
                return {"success": False, "error": "Invalid or expired invitation"}

            invitation = invitations[0]

            # Check expiration
            expires_at = datetime.fromisoformat(invitation["expires_at"].replace("Z", "+00:00"))
            if expires_at < datetime.now(timezone.utc):
                return {"success": False, "error": "Invitation expired"}

            # Create user
            user = await self.create_user_from_auth(
                supabase_auth_id=supabase_auth_id,
                email=invitation["email"],
                organization_id=invitation["organization_id"],
                full_name=full_name,
                role=invitation["role"]
            )

            if not user:
                return {"success": False, "error": "Failed to create user"}

            # Mark invitation as accepted
            supabase_update("invitations", {"id": f"eq.{invitation['id']}"}, {
                "accepted_at": datetime.now(timezone.utc).isoformat()
            })

            return {
                "success": True,
                "user_id": user["id"],
                "organization_id": invitation["organization_id"],
                "role": invitation["role"]
            }

        except Exception as e:
            logger.error(f"Accept invitation error: {e}")
            return {"success": False, "error": str(e)}

    async def get_tenant_by_id(self, tenant_id: str) -> Optional[UserContext]:
        """
        Look up a v1 tenant by ID and return a UserContext.
        Used as fallback when JWT sub is a tenant_id rather than a Supabase auth UUID.
        """
        try:
            tenants = supabase_select("tenants", {
                "id": f"eq.{tenant_id}",
                "is_active": "eq.true"
            })
            if not tenants:
                logger.warning(f"No tenant found for id: {tenant_id}")
                return None
            tenant = tenants[0]
            return UserContext(
                user_id=tenant["id"],
                supabase_auth_id=tenant["id"],
                organization_id=tenant["id"],
                email=tenant.get("email", ""),
                role=UserRole.ADMIN,
                brands=[]
            )
        except Exception as e:
            logger.error(f"Error getting tenant context: {e}")
            return None

    async def check_permission(
        self,
        user_context: UserContext,
        permission: str,
        brand_id: Optional[str] = None
    ) -> bool:
        """
        Check if user has a specific permission.

        Args:
            user_context: The authenticated user context
            permission: Permission to check (e.g., "tickets:write", "actions:approve")
            brand_id: Optional brand scope

        Returns:
            True if permitted, False otherwise
        """
        # Admins have all permissions
        if UserRole.is_admin(user_context.role):
            return True

        # Check brand access if specified
        if brand_id and brand_id not in user_context.brands:
            return False

        # Check specific permissions
        if permission in user_context.permissions:
            return True

        # Role-based defaults
        if user_context.role == UserRole.AGENT:
            agent_permissions = [
                "tickets:read", "tickets:write", "tickets:respond",
                "actions:read", "actions:approve", "actions:reject",
                "brands:read", "knowledge:read"
            ]
            return permission in agent_permissions

        if user_context.role == UserRole.READ_ONLY:
            read_only_permissions = [
                "tickets:read", "actions:read", "brands:read",
                "analytics:read", "knowledge:read"
            ]
            return permission in read_only_permissions

        return False

    def check_plan_limit(
        self,
        org_context: OrganizationContext,
        limit_type: str,
        current_count: int
    ) -> bool:
        """Check if organization is within plan limits"""
        limits = org_context.plan_limits
        max_allowed = limits.get(limit_type, float("inf"))
        return current_count < max_allowed


# Global instance
supabase_auth_service = SupabaseAuthService()
