"""
SaaS Authentication Routes
==========================
Handles user registration, login, token refresh, and account management.
"""
import logging
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, EmailStr, Field
from typing import Optional

from src.services.auth_service import auth_service
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext

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


class RefreshRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    current_password: str
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
    error: Optional[str] = None


# ==================== Public Routes ====================

@router.post("/register", response_model=AuthResponse)
async def register(request: RegisterRequest):
    """
    Register a new tenant account.

    Creates a new account and returns authentication tokens.
    """
    result = await auth_service.register(
        email=request.email,
        password=request.password,
        company_name=request.company_name
    )

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Registration failed"))

    return AuthResponse(**result)


@router.post("/login", response_model=AuthResponse)
async def login(request: LoginRequest):
    """
    Authenticate and get tokens.

    Returns access and refresh tokens for the authenticated user.
    """
    result = await auth_service.login(
        email=request.email,
        password=request.password
    )

    if not result.get("success"):
        raise HTTPException(status_code=401, detail=result.get("error", "Invalid credentials"))

    return AuthResponse(**result)


@router.post("/refresh")
async def refresh_token(request: RefreshRequest):
    """
    Get a new access token using refresh token.
    """
    result = await auth_service.refresh_access_token(request.refresh_token)

    if not result.get("success"):
        raise HTTPException(status_code=401, detail=result.get("error", "Invalid refresh token"))

    return result


@router.post("/logout")
async def logout(request: RefreshRequest):
    """
    Logout and invalidate refresh token.
    """
    result = await auth_service.logout(request.refresh_token)
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

    return {
        "success": True,
        **tenant_data
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
async def change_password(
    request: ChangePasswordRequest,
    tenant: TenantContext = Depends(get_current_tenant)
):
    """
    Change account password.
    """
    result = await auth_service.change_password(
        tenant_id=tenant.tenant_id,
        current_password=request.current_password,
        new_password=request.new_password
    )

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))

    return result


# ==================== Health Check ====================

@router.get("/health")
async def auth_health():
    """Health check for auth routes."""
    return {"status": "ok", "service": "auth"}
