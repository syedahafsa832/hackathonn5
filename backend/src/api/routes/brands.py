"""
Brand Management API Routes
============================
Endpoints for managing multiple Shopify brands.
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field
from typing import Optional, List
import logging

from src.api.middleware.tenant_auth import get_current_tenant, TenantContext
from src.lib.supabase_client import supabase_select

router = APIRouter(prefix="/brands", tags=["brands"])
logger = logging.getLogger(__name__)


def _assert_owned(brand_id: str, tenant_id: str) -> None:
    """get/put/delete below previously had no tenant check at all — any
    caller who knew or guessed a brand UUID could read another tenant's
    support_email/email_signature or overwrite their shopify_access_token.
    Raises 404 (not 403) so an unauthorized caller can't distinguish 'not
    yours' from 'doesn't exist' by probing IDs, matching v2_brands.py's
    _get_owned_brand convention."""
    owned = supabase_select("brands", {"id": f"eq.{brand_id}", "tenant_id": f"eq.{tenant_id}"})
    if not owned:
        raise HTTPException(status_code=404, detail="Brand not found")


# ============== Request/Response Models ==============

class CreateBrandRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Brand display name")
    shopify_shop_name: str = Field(..., description="Shopify store name (without .myshopify.com)")
    shopify_access_token: str = Field(..., description="Shopify Admin API access token")
    support_email: str = Field(..., description="Customer support email")
    sender_name: Optional[str] = Field(None, description="Email sender display name")
    email_signature: Optional[str] = Field(None, description="HTML email signature")
    logo_url: Optional[str] = Field(None, description="Brand logo URL")
    primary_color: Optional[str] = Field("#000000", description="Brand primary color (hex)")
    return_policy_days: Optional[int] = Field(30, ge=0, le=365, description="Return window in days")
    auto_approve_threshold: Optional[float] = Field(50.0, ge=0, description="Max order value for auto-approval")


class UpdateBrandRequest(BaseModel):
    name: Optional[str] = None
    agent_name: Optional[str] = None
    shopify_access_token: Optional[str] = None
    support_email: Optional[str] = None
    sender_name: Optional[str] = None
    email_signature: Optional[str] = None
    logo_url: Optional[str] = None
    primary_color: Optional[str] = None
    return_policy_days: Optional[int] = None
    auto_approve_threshold: Optional[float] = None
    is_active: Optional[bool] = None


class BrandResponse(BaseModel):
    id: str
    name: str
    shopify_shop_name: str
    support_email: str
    sender_name: Optional[str]
    logo_url: Optional[str]
    primary_color: Optional[str]
    is_active: bool
    return_policy_days: int
    auto_approve_threshold: float
    created_at: Optional[str]


# ============== Endpoints ==============

@router.post("", response_model=dict)
async def create_brand(
    request: CreateBrandRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """
    Create a new brand with Shopify integration.
    Validates credentials before saving.

    Previously had no tenant auth and never wrote tenant_id, so brands created
    here never showed up in GET /api/brands (which is tenant-scoped) — fixed
    alongside adding the plan's brand-count limit, since enforcing that limit
    requires knowing whose tenant is creating the brand in the first place.
    """
    try:
        from src.services.brand_manager import brand_manager
        from src.services.plan_service import check_limit, build_limit_error

        brand_limit = check_limit(tenant.tenant_id, "brands", email=tenant.email)
        if not brand_limit["allowed"]:
            raise HTTPException(status_code=402, detail=build_limit_error("brands", brand_limit))

        logger.info(f"[Brands API] Creating brand: {request.name} for tenant {tenant.tenant_id}")

        result = await brand_manager.create_brand(
            name=request.name,
            shopify_shop_name=request.shopify_shop_name,
            shopify_access_token=request.shopify_access_token,
            support_email=request.support_email,
            sender_name=request.sender_name,
            email_signature=request.email_signature,
            logo_url=request.logo_url,
            primary_color=request.primary_color,
            return_policy_days=request.return_policy_days,
            auto_approve_threshold=request.auto_approve_threshold,
            tenant_id=tenant.tenant_id,
        )

        if result.get("success"):
            return result
        else:
            raise HTTPException(status_code=400, detail=result.get("error"))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Brands API] Error creating brand: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=dict)
async def list_brands(
    active_only: bool = Query(True, description="Only show active brands"),
    tenant: TenantContext = Depends(get_current_tenant),
):
    """
    List brands owned by the current tenant.
    """
    try:
        params: dict = {}
        if active_only:
            params["is_active"] = "is.true"

        # Scope to tenant — try tenant_id column first (requires migration 010),
        # then fall back to shopify_domain match so older rows still appear.
        # The fallback never returns a brand another tenant already owns — a
        # shared/duplicate shopify_domain value must not leak another
        # tenant's brand into this list.
        from src.services.auth_service import auth_service
        owned = supabase_select("brands", {"tenant_id": f"eq.{tenant.tenant_id}", **params})
        if not owned:
            tenant_data = await auth_service.get_tenant(tenant.tenant_id)
            shopify_domain = (tenant_data or {}).get("shopify_domain")
            if shopify_domain:
                candidates = supabase_select("brands", {"shopify_domain": f"eq.{shopify_domain}", **params})
                owned = [
                    b for b in candidates
                    if b.get("tenant_id") is None or b.get("tenant_id") == tenant.tenant_id
                ]

        # Strip secrets before returning
        safe = []
        for b in owned:
            safe.append({k: v for k, v in b.items() if k not in ("shopify_access_token", "gmail_token")})

        return {"brands": safe, "count": len(safe)}
    except Exception as e:
        logger.error(f"[Brands API] Error listing brands: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{brand_id}", response_model=dict)
async def get_brand(brand_id: str, tenant: TenantContext = Depends(get_current_tenant)):
    """
    Get a specific brand by ID.
    """
    try:
        from src.services.brand_manager import brand_manager

        _assert_owned(brand_id, tenant.tenant_id)
        brand = await brand_manager.get_brand(brand_id)

        if not brand:
            raise HTTPException(status_code=404, detail="Brand not found")

        # Remove sensitive data before returning
        return {
            "id": brand.get("id"),
            "name": brand.get("name"),
            "agent_name": brand.get("agent_name"),
            "shopify_shop_name": brand.get("shopify_shop_name"),
            "support_email": brand.get("support_email"),
            "sender_name": brand.get("sender_name"),
            "email_signature": brand.get("email_signature"),
            "logo_url": brand.get("logo_url"),
            "primary_color": brand.get("primary_color"),
            "is_active": brand.get("is_active"),
            "return_policy_days": brand.get("return_policy_days"),
            "auto_approve_threshold": brand.get("auto_approve_threshold"),
            "created_at": brand.get("created_at"),
            "updated_at": brand.get("updated_at")
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Brands API] Error getting brand: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{brand_id}", response_model=dict)
async def update_brand(brand_id: str, request: UpdateBrandRequest, tenant: TenantContext = Depends(get_current_tenant)):
    """
    Update brand settings.
    """
    try:
        from src.services.brand_manager import brand_manager

        _assert_owned(brand_id, tenant.tenant_id)

        # Build updates dict from non-None fields
        updates = {k: v for k, v in request.dict().items() if v is not None}

        if not updates:
            raise HTTPException(status_code=400, detail="No updates provided")

        result = await brand_manager.update_brand(brand_id, updates)

        if result.get("success"):
            return result
        else:
            raise HTTPException(status_code=400, detail=result.get("error"))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Brands API] Error updating brand: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{brand_id}", response_model=dict)
async def delete_brand(
    brand_id: str,
    hard_delete: bool = Query(False, description="Permanently delete (vs deactivate)"),
    tenant: TenantContext = Depends(get_current_tenant),
):
    """
    Delete or deactivate a brand.
    """
    try:
        from src.services.brand_manager import brand_manager

        _assert_owned(brand_id, tenant.tenant_id)
        result = await brand_manager.delete_brand(brand_id, soft_delete=not hard_delete)

        if result.get("success"):
            return result
        else:
            raise HTTPException(status_code=400, detail=result.get("error"))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Brands API] Error deleting brand: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{brand_id}/test-connection", response_model=dict)
async def test_brand_connection(brand_id: str, tenant: TenantContext = Depends(get_current_tenant)):
    """
    Test Shopify API connection for a brand.
    """
    try:
        from src.services.brand_manager import brand_manager

        _assert_owned(brand_id, tenant.tenant_id)
        result = await brand_manager.test_connection(brand_id)

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Brands API] Error testing connection: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{brand_id}/sync-products", response_model=dict)
async def sync_brand_products(brand_id: str, tenant: TenantContext = Depends(get_current_tenant)):
    """
    Trigger product sync from Shopify for this brand.
    """
    try:
        from src.services.brand_manager import brand_manager
        from src.services.shopify_sync import ShopifySyncService

        _assert_owned(brand_id, tenant.tenant_id)
        brand = await brand_manager.get_brand(brand_id)
        if not brand:
            raise HTTPException(status_code=404, detail="Brand not found")

        # Create a sync service for this brand
        sync_service = ShopifySyncService()
        sync_service.shop_name = brand.get("shopify_shop_name")
        sync_service.access_token = brand.get("_decrypted_token")
        sync_service.base_url = f"https://{sync_service.shop_name}.myshopify.com/admin/api/{sync_service.api_version}"
        sync_service.headers = {
            "X-Shopify-Access-Token": sync_service.access_token,
            "Content-Type": "application/json"
        }

        # Start sync (async)
        await sync_service.sync_all_products(store_id=brand_id)

        return {
            "success": True,
            "message": f"Product sync started for {brand.get('name')}"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Brands API] Error syncing products: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{brand_id}/sync-orders", response_model=dict)
async def sync_brand_orders(brand_id: str, tenant: TenantContext = Depends(get_current_tenant)):
    """
    Trigger order sync from Shopify for this brand.
    """
    try:
        from src.services.brand_manager import brand_manager
        from src.services.shopify_sync import ShopifySyncService

        _assert_owned(brand_id, tenant.tenant_id)
        brand = await brand_manager.get_brand(brand_id)
        if not brand:
            raise HTTPException(status_code=404, detail="Brand not found")

        # Create a sync service for this brand
        sync_service = ShopifySyncService()
        sync_service.shop_name = brand.get("shopify_shop_name")
        sync_service.access_token = brand.get("_decrypted_token")
        sync_service.base_url = f"https://{sync_service.shop_name}.myshopify.com/admin/api/{sync_service.api_version}"
        sync_service.headers = {
            "X-Shopify-Access-Token": sync_service.access_token,
            "Content-Type": "application/json"
        }

        # Start sync (async)
        await sync_service.sync_all_orders(store_id=brand_id)

        return {
            "success": True,
            "message": f"Order sync started for {brand.get('name')}"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Brands API] Error syncing orders: {e}")
        raise HTTPException(status_code=500, detail=str(e))
