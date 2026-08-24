"""
SaaS Authentication Routes
==========================
Handles user registration, login, token refresh, and account management.
"""
import logging
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field
from typing import Optional

from src.services.auth_service import auth_service
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext, security
# Security audit finding A3: these endpoints had no rate limiting at all,
# despite being the primary brute-force/credential-stuffing surface. Reuses
# the same slowapi Limiter + pattern already applied to other endpoints in
# main.py (see src/lib/rate_limiter.py for why it's a separate module).
from src.lib.rate_limiter import limiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentication"])


# ==================== Request/Response Models ====================

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, description="Password must be at least 8 characters")
    company_name: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class GoogleAuthRequest(BaseModel):
    credential: str = Field(description="Google Identity Services ID token (JWT)")


class RefreshRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirmRequest(BaseModel):
    new_password: str = Field(min_length=8)


class UpdateProfileRequest(BaseModel):
    company_name: Optional[str] = None
    support_email: Optional[EmailStr] = None
    auto_approve_threshold: Optional[float] = None


class AuthResponse(BaseModel):
    success: bool
    tenant_id: Optional[str] = None
    email: Optional[str] = None
    company_name: Optional[str] = None
    shopify_connected: Optional[bool] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    token_type: Optional[str] = None
    expires_in: Optional[int] = None
    email_confirmation_required: Optional[bool] = None
    error: Optional[str] = None


# ==================== Public Routes ====================

@router.post("/register", response_model=AuthResponse)
@limiter.limit("10/minute")
async def register(request: Request, payload: RegisterRequest):
    """
    Register a new tenant account.

    Creates a new account and returns authentication tokens.
    """
    result = await auth_service.register(
        email=payload.email,
        password=payload.password,
        company_name=payload.company_name
    )

    if not result.get("success"):
        if result.get("error") == "founding_cohort_full":
            raise HTTPException(status_code=403, detail={
                "error": "founding_cohort_full",
                "message": result.get("message"),
                "waitlist_url": result.get("waitlist_url"),
            })
        raise HTTPException(status_code=400, detail=result.get("error", "Registration failed"))

    return AuthResponse(**result)


@router.post("/login", response_model=AuthResponse)
@limiter.limit("10/minute")
async def login(request: Request, payload: LoginRequest):
    """
    Authenticate and get tokens.

    Returns access and refresh tokens for the authenticated user.
    """
    result = await auth_service.login(
        email=payload.email,
        password=payload.password
    )

    if not result.get("success"):
        raise HTTPException(status_code=401, detail=result.get("error", "Invalid credentials"))

    return AuthResponse(**result)


@router.post("/google", response_model=AuthResponse)
@limiter.limit("10/minute")
async def google_auth(request: Request, payload: GoogleAuthRequest):
    """
    Sign in or register using a Google Identity Services ID token.

    Creates a new tenant on first sign-in, or logs into the existing tenant
    matching the Google account's email.
    """
    result = await auth_service.google_auth(payload.credential)

    if not result.get("success"):
        raise HTTPException(status_code=401, detail=result.get("error", "Google sign-in failed"))

    return AuthResponse(**result)


@router.post("/refresh")
@limiter.limit("30/minute")
async def refresh_token(request: Request, payload: RefreshRequest):
    """
    Get a new access/refresh token pair from a Supabase refresh token.
    """
    result = await auth_service.refresh_access_token(payload.refresh_token)

    if not result.get("success"):
        raise HTTPException(status_code=401, detail=result.get("error", "Invalid refresh token"))

    return result


@router.post("/logout")
async def logout(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Logout and invalidate the current Supabase session.

    Takes the access token as a Bearer credential (not a request body) —
    that's what Supabase's logout endpoint needs to identify the session.
    """
    if credentials:
        await auth_service.logout(credentials.credentials)
    return {"success": True, "message": "Logged out successfully"}


@router.post("/password/reset-request")
@limiter.limit("5/minute")
async def request_password_reset(request: Request, payload: PasswordResetRequest):
    """
    Send a password-reset email. Always returns success, whether or not the
    email is registered, so this can't be used to enumerate accounts.
    """
    return await auth_service.request_password_reset(payload.email)


@router.post("/password/reset-confirm")
@limiter.limit("10/minute")
async def confirm_password_reset(
    request: Request,
    payload: PasswordResetConfirmRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """
    Set a new password from a Supabase password-recovery link.

    The frontend's reset-password page extracts the recovery access token
    Supabase redirects with and sends it here as a Bearer credential.
    """
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing or invalid reset link")

    result = await auth_service.confirm_password_reset(credentials.credentials, payload.new_password)

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))

    return result


# ==================== Protected Routes ====================

@router.get("/me")
async def get_current_user(tenant: TenantContext = Depends(get_current_tenant)):
    """
    Get current user profile.

    Returns tenant information for the authenticated user.
    """
    tenant_data = await auth_service.get_tenant(tenant.tenant_id)

    if not tenant_data:
        raise HTTPException(status_code=404, detail="Account not found")

    from src.services.plan_service import is_super_admin
    return {
        "success": True,
        **tenant_data,
        "is_super_admin": is_super_admin(tenant_data.get("email")),
    }


@router.put("/me")
async def update_profile(
    request: UpdateProfileRequest,
    tenant: TenantContext = Depends(get_current_tenant)
):
    """
    Update user profile.

    Updates allowed profile fields for the authenticated user.
    """
    updates = request.model_dump(exclude_none=True)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = await auth_service.update_tenant(tenant.tenant_id, updates)

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))

    return result


@router.post("/change-password")
@limiter.limit("10/minute")
async def change_password(
    request: Request,
    payload: ChangePasswordRequest,
    tenant: TenantContext = Depends(get_current_tenant),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """
    Change account password.
    """
    result = await auth_service.change_password(
        tenant_id=tenant.tenant_id,
        email=tenant.email,
        current_password=payload.current_password,
        new_password=payload.new_password,
        access_token=credentials.credentials,
    )

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))

    return result


# ==================== Health Check ====================

@router.get("/health")
async def auth_health():
    """Health check for auth routes."""
    return {"status": "ok", "service": "auth"}
