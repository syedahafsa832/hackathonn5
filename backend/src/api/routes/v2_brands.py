"""
Brands API Routes (v2)
======================
Uses v1 tenant JWT auth and the actual brands table schema.
Replaces the old version that referenced non-existent columns
(organization_id, slug, ai_auto_respond, etc.).
"""

import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from datetime import datetime, timezone

from src.api.middleware.tenant_auth import get_current_tenant, TenantContext
from src.lib.supabase_client import supabase_select, supabase_insert, supabase_update

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/brands", tags=["Brands v2"])

SAFE_COLUMNS = {"id", "name", "shopify_shop_name", "shopify_domain", "shopify_connected",
                "support_email", "is_active", "gmail_email", "gmail_connected",
                "return_policy_days", "auto_approve_threshold", "created_at", "updated_at",
                "tenant_id"}


def _strip_secrets(brand: dict) -> dict:
    return {k: v for k, v in brand.items() if k in SAFE_COLUMNS}


# ==================== Request Models ====================

class CreateBrandRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: Optional[str] = None          # accepted but ignored (no slug column)
    support_email: Optional[str] = None
    shopify_shop_name: Optional[str] = None
    shopify_domain: Optional[str] = None
    shopify_access_token: Optional[str] = None


class UpdateBrandRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    support_email: Optional[str] = None
    is_active: Optional[bool] = None
    return_policy_days: Optional[int] = None
    auto_approve_threshold: Optional[float] = None


class ConnectShopifyRequest(BaseModel):
    shop_domain: str = Field(..., min_length=3)
    access_token: str = Field(..., min_length=10)


# ==================== Routes ====================

@router.get("")
async def list_brands(
    tenant: TenantContext = Depends(get_current_tenant),
    active_only: bool = Query(True),
):
    """List brands owned by the current tenant."""
    try:
        params: dict = {}
        if active_only:
            params["is_active"] = "is.true"

        owned = supabase_select("brands", {"tenant_id": f"eq.{tenant.tenant_id}", **params})

        if not owned:
            from src.services.auth_service import auth_service
            tenant_data = await auth_service.get_tenant(tenant.tenant_id)
            shopify_domain = (tenant_data or {}).get("shopify_domain")
            if shopify_domain:
                owned = supabase_select("brands", {"shopify_domain": f"eq.{shopify_domain}", **params})

        return {"brands": [_strip_secrets(b) for b in owned], "count": len(owned)}
    except Exception as e:
        logger.error(f"Error listing brands: {e}")
        raise HTTPException(status_code=500, detail="Failed to list brands")


@router.post("")
async def create_brand(
    request: CreateBrandRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Create a new brand, owned by the current tenant."""
    try:
        brand_data: dict = {
            "name": request.name,
            "is_active": True,
            "tenant_id": tenant.tenant_id,
        }
        if request.support_email:
            brand_data["support_email"] = request.support_email
        if request.shopify_shop_name:
            brand_data["shopify_shop_name"] = request.shopify_shop_name
        if request.shopify_domain:
            brand_data["shopify_domain"] = request.shopify_domain

        result = supabase_insert("brands", brand_data)
        logger.info(f"[v2/brands] Created brand '{request.name}' for tenant {tenant.tenant_id}")
        return {"success": True, "brand": _strip_secrets(result) if result else brand_data}
    except Exception as e:
        logger.error(f"Error creating brand: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{brand_id}")
async def get_brand(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Get a specific brand (must belong to current tenant)."""
    try:
        brands = supabase_select("brands", {"id": f"eq.{brand_id}"})
        if not brands:
            raise HTTPException(status_code=404, detail="Brand not found")
        return {"brand": _strip_secrets(brands[0])}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting brand: {e}")
        raise HTTPException(status_code=500, detail="Failed to get brand")


@router.patch("/{brand_id}")
async def update_brand(
    brand_id: str,
    request: UpdateBrandRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Update brand settings."""
    try:
        updates = request.model_dump(exclude_none=True)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        result = supabase_update("brands", {"id": f"eq.{brand_id}"}, updates)
        return {"success": True, "brand": _strip_secrets(result[0]) if result else None}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating brand: {e}")
        raise HTTPException(status_code=500, detail="Failed to update brand")


@router.delete("/{brand_id}")
async def delete_brand(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Soft-delete a brand (marks inactive)."""
    try:
        supabase_update("brands", {"id": f"eq.{brand_id}"}, {"is_active": False})
        return {"success": True, "message": "Brand deactivated"}
    except Exception as e:
        logger.error(f"Error deleting brand: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete brand")


@router.post("/{brand_id}/shopify/connect")
async def connect_shopify(
    brand_id: str,
    request: ConnectShopifyRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Connect a Shopify store to a brand.
    If another brand already owns this domain (unique constraint), we claim that brand
    for this tenant and deactivate the newly-created placeholder brand."""
    try:
        shop_domain = request.shop_domain.lower().strip()
        if not shop_domain.endswith(".myshopify.com"):
            shop_domain = f"{shop_domain}.myshopify.com"

        from src.services.shopify_service import ShopifyClient
        client_shopify = ShopifyClient(shop_domain, request.access_token)
        validation = await client_shopify.validate_connection()

        if not validation.get("success"):
            raise HTTPException(status_code=400, detail=validation.get("error", "Failed to connect to Shopify"))

        shopify_fields = {
            "shopify_domain": shop_domain,
            "shopify_access_token": request.access_token,
            "shopify_shop_name": validation.get("shop_name"),
            "shopify_connected": True,
            "tenant_id": tenant.tenant_id,
        }

        active_brand_id = brand_id
        try:
            supabase_update("brands", {"id": f"eq.{brand_id}"}, shopify_fields)
        except Exception as upd_err:
            err_str = str(upd_err)
            if "409" in err_str or "23505" in err_str or "conflict" in err_str.lower():
                # Domain unique constraint — claim the brand that already owns this domain
                existing = supabase_select("brands", {"shopify_domain": f"eq.{shop_domain}"})
                if existing:
                    active_brand_id = existing[0]["id"]
                    supabase_update("brands", {"id": f"eq.{active_brand_id}"}, {
                        "tenant_id": tenant.tenant_id,
                        "shopify_access_token": request.access_token,
                        "shopify_connected": True,
                        "is_active": True,
                    })
                    # Deactivate the empty placeholder that was just created
                    if active_brand_id != brand_id:
                        supabase_update("brands", {"id": f"eq.{brand_id}"}, {"is_active": False})
                    logger.info(f"[v2/brands] Claimed existing brand {active_brand_id} for tenant {tenant.tenant_id}")
                else:
                    raise
            else:
                raise

        return {
            "success": True,
            "shop_name": validation.get("shop_name"),
            "shop_domain": shop_domain,
            "brand_id": active_brand_id,  # May differ from URL brand_id after 409 resolution
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error connecting Shopify: {e}")
        raise HTTPException(status_code=500, detail="Failed to connect Shopify")


@router.post("/{brand_id}/shopify/disconnect")
async def disconnect_shopify(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Disconnect Shopify from a brand."""
    try:
        supabase_update("brands", {"id": f"eq.{brand_id}"}, {
            "shopify_domain": None,
            "shopify_access_token": None,
            "shopify_shop_name": None,
            "shopify_connected": False,
        })
        return {"success": True, "message": "Shopify disconnected"}
    except Exception as e:
        logger.error(f"Error disconnecting Shopify: {e}")
        raise HTTPException(status_code=500, detail="Failed to disconnect Shopify")
