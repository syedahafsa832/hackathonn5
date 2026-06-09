"""
SaaS Settings Routes
====================
Shopify connection, account settings, knowledge base, and Gmail management.
"""
import os
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, List

from src.services.shopify_service import shopify_service
from src.services.auth_service import auth_service
from src.services.knowledge_base_service import knowledge_base_service
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext
from src.lib.supabase_client import supabase_select, supabase_update

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings", tags=["Settings"])


# ==================== Request/Response Models ====================

class ConnectShopifyRequest(BaseModel):
    shop_domain: str = Field(
        ...,
        min_length=3,
        description="Shopify store domain (e.g., 'mystore' or 'mystore.myshopify.com')"
    )
    access_token: str = Field(
        ...,
        min_length=10,
        description="Shopify Admin API access token (starts with 'shpat_' or 'shpca_')"
    )


class UpdateSettingsRequest(BaseModel):
    company_name: Optional[str] = None
    support_email: Optional[str] = None
    auto_approve_threshold: Optional[float] = Field(None, ge=0)
    confidence_threshold: Optional[float] = Field(None, ge=0, le=100,
        description="AI confidence % required for auto-send (stored in system_settings)")
    timezone: Optional[str] = None


class ShopifyStatusResponse(BaseModel):
    connected: bool
    shop_name: Optional[str] = None
    shop_domain: Optional[str] = None
    plan: Optional[str] = None
    error: Optional[str] = None


# ==================== Shopify Connection Routes ====================

def _get_shopify_from_brand(tenant_id: str) -> Optional[dict]:
    """Check if the tenant's brand has Shopify connected; return its status fields."""
    try:
        # Try direct tenant_id match first (requires migration 010)
        results = supabase_select("brands", {
            "tenant_id": f"eq.{tenant_id}",
            "is_active": "is.true",
            "shopify_connected": "is.true",
        })
        if results:
            b = results[0]
            return {"connected": True, "shop_name": b.get("shopify_shop_name"), "shop_domain": b.get("shopify_domain"), "brand_id": b.get("id")}
    except Exception:
        pass
    return None


@router.get("/shopify")
async def get_shopify_status(tenant: TenantContext = Depends(get_current_tenant)):
    """Get Shopify connection status — checks brands table first, then tenants."""
    # Check brands table (v2 brands with Shopify)
    brand_status = _get_shopify_from_brand(tenant.tenant_id)
    if brand_status:
        return ShopifyStatusResponse(
            connected=True,
            shop_name=brand_status.get("shop_name"),
            shop_domain=brand_status.get("shop_domain"),
        )

    # Fall back to tenants table (v1 direct connect)
    tenant_data = await auth_service.get_tenant(tenant.tenant_id)
    if tenant_data and tenant_data.get("shopify_connected"):
        return ShopifyStatusResponse(
            connected=True,
            shop_name=tenant_data.get("shopify_shop_name"),
            shop_domain=tenant_data.get("shopify_domain"),
        )

    return ShopifyStatusResponse(connected=False, error="No Shopify store connected")


@router.post("/shopify/connect")
async def connect_shopify(
    request: ConnectShopifyRequest,
    tenant: TenantContext = Depends(get_current_tenant)
):
    """
    Connect a Shopify store.
    Saves to the tenants table AND to the first active brand (for the ticket pipeline).
    """
    result = await shopify_service.connect_store(
        tenant_id=tenant.tenant_id,
        shop_domain=request.shop_domain,
        access_token=request.access_token
    )

    if not result.get("success"):
        error_code = result.get("error_code")

        if error_code == "invalid_token":
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "Invalid access token. Please check your Shopify Admin API token.",
                    "error_code": "invalid_token",
                    "help": "You can find your access token in Shopify Admin > Apps > Develop apps"
                }
            )

        if error_code == "invalid_domain":
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "Invalid store domain. Please check your Shopify store URL.",
                    "error_code": "invalid_domain"
                }
            )

        raise HTTPException(
            status_code=400,
            detail={
                "error": result.get("error", "Could not connect to Shopify"),
                "error_code": error_code or "connection_failed"
            }
        )

    # Ensure a brand record exists and has Shopify credentials (required for Gmail and ticket pipeline)
    try:
        from src.services.shopify_service import encrypt_token
        from src.lib.supabase_client import supabase_update, supabase_insert
        shopify_fields = {
            "shopify_domain": result.get("shop_domain"),
            "shopify_access_token": encrypt_token(request.access_token),
            "shopify_shop_name": result.get("shop_name"),
            "shopify_connected": True,
            "tenant_id": tenant.tenant_id,
        }
        # Find existing brand owned by THIS tenant only — never touch another tenant's brand
        brands = supabase_select("brands", {"tenant_id": f"eq.{tenant.tenant_id}", "is_active": "is.true"})
        if brands:
            supabase_update("brands", {"id": f"eq.{brands[0]['id']}"}, shopify_fields)
            logger.info(f"[Settings] Shopify creds mirrored to brand {brands[0]['id']}")
        else:
            # No brand for this tenant — always create a new one (never reuse another tenant's brand)
            tenant_data = await auth_service.get_tenant(tenant.tenant_id)
            shop_name = result.get("shop_name") or result.get("shop_domain", "My Store")
            new_brand = {"name": shop_name, "is_active": True, **shopify_fields}
            support_email = (tenant_data or {}).get("support_email")
            if support_email:
                new_brand["support_email"] = support_email
            try:
                created = supabase_insert("brands", new_brand)
                logger.info(f"[Settings] Auto-created brand for tenant {tenant.tenant_id}: {created}")
            except Exception as brand_err:
                logger.warning(f"[Settings] Could not auto-create brand: {brand_err}")
    except Exception as e:
        logger.warning(f"[Settings] Could not mirror Shopify to brand: {e}")

    return {
        "success": True,
        "message": "Shopify store connected successfully",
        "shop_name": result.get("shop_name"),
        "shop_domain": result.get("shop_domain")
    }


@router.post("/shopify/test")
async def test_shopify_connection(tenant: TenantContext = Depends(get_current_tenant)):
    """
    Test the current Shopify connection.

    Useful for verifying the connection is still valid.
    """
    result = await shopify_service.test_connection(tenant.tenant_id)

    if not result.get("success"):
        return {
            "success": False,
            "connected": False,
            "error": result.get("error"),
            "error_code": result.get("error_code"),
            "action": "reconnect" if result.get("error_code") == "invalid_token" else None
        }

    return {
        "success": True,
        "connected": True,
        "shop_name": result.get("shop_name"),
        "shop_domain": result.get("shop_domain"),
        "plan": result.get("plan")
    }


@router.post("/shopify/disconnect")
async def disconnect_shopify(tenant: TenantContext = Depends(get_current_tenant)):
    """
    Disconnect the Shopify store.

    Removes the stored credentials. Pending actions will fail to execute.
    """
    result = await shopify_service.disconnect_store(tenant.tenant_id)

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))

    return {
        "success": True,
        "message": "Shopify store disconnected"
    }


# ==================== Account Settings Routes ====================

@router.get("/account")
async def get_account_settings(tenant: TenantContext = Depends(get_current_tenant)):
    """
    Get account settings.

    Returns all configurable account settings.
    """
    tenant_data = await auth_service.get_tenant(tenant.tenant_id)

    if not tenant_data:
        raise HTTPException(status_code=404, detail="Account not found")

    return {
        "success": True,
        "settings": {
            "email": tenant_data.get("email"),
            "company_name": tenant_data.get("company_name"),
            "support_email": tenant_data.get("support_email"),
            "auto_approve_threshold": tenant_data.get("auto_approve_threshold"),
            "shopify_connected": tenant_data.get("shopify_connected"),
            "shopify_shop_name": tenant_data.get("shopify_shop_name"),
            "created_at": tenant_data.get("created_at"),
            "last_login_at": tenant_data.get("last_login_at")
        }
    }


@router.patch("/account")
@router.put("/account")
async def update_account_settings(
    request: UpdateSettingsRequest,
    tenant: TenantContext = Depends(get_current_tenant)
):
    """
    Update account settings.

    Only updates fields that are provided.
    """
    updates = request.model_dump(exclude_none=True)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    # confidence_threshold lives in system_settings, not tenants table
    confidence_threshold = updates.pop("confidence_threshold", None)
    if confidence_threshold is not None:
        try:
            from src.lib.supabase_client import supabase_update, supabase_insert
            DEFAULT_STORE = "00000000-0000-0000-0000-000000000000"
            updated = supabase_update("system_settings", {"store_id": f"eq.{DEFAULT_STORE}"}, {
                "confidence_threshold": confidence_threshold / 100.0  # convert % to 0-1
            })
            if not updated:
                supabase_insert("system_settings", {
                    "store_id": DEFAULT_STORE,
                    "ai_mode": "active",
                    "confidence_threshold": confidence_threshold / 100.0,
                })
        except Exception as e:
            logger.warning(f"Could not save confidence_threshold: {e}")

    if updates:
        result = await auth_service.update_tenant(tenant.tenant_id, updates)
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))

    return {
        "success": True,
        "message": "Settings updated"
    }


# ==================== Knowledge Base Routes ====================

class UploadKnowledgeRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Name/title for this knowledge source")
    content: str = Field(..., min_length=10, description="Text content to add to knowledge base")


class KnowledgeSourceResponse(BaseModel):
    id: str
    name: str
    source_type: str
    status: str
    chunk_count: int
    created_at: str
    error_message: Optional[str] = None


@router.post("/knowledge-base/upload")
async def upload_knowledge(
    request: UploadKnowledgeRequest,
    tenant: TenantContext = Depends(get_current_tenant)
):
    """
    Upload text content to the knowledge base.

    The content will be chunked, embedded, and stored for RAG retrieval.
    This enables the AI to answer questions about your brand, products, and policies.
    """
    logger.info(f"[KB] Uploading knowledge for tenant {tenant.tenant_id}: {request.name}")

    result = await knowledge_base_service.upload_text(
        tenant_id=tenant.tenant_id,
        name=request.name,
        content=request.content
    )

    if not result.get("success"):
        raise HTTPException(
            status_code=400,
            detail={
                "error": result.get("error", "Failed to upload content"),
                "error_code": "upload_failed"
            }
        )

    return {
        "success": True,
        "message": f"Successfully processed {result.get('chunk_count')} chunks",
        "source_id": result.get("source_id"),
        "chunk_count": result.get("chunk_count")
    }


@router.get("/knowledge-base/sources")
async def get_knowledge_sources(tenant: TenantContext = Depends(get_current_tenant)):
    """
    Get all knowledge base sources for the tenant.

    Returns a list of uploaded content sources with their status and chunk counts.
    """
    sources = await knowledge_base_service.get_sources(tenant.tenant_id)

    return {
        "success": True,
        "sources": sources
    }


@router.delete("/knowledge-base/sources/{source_id}")
async def delete_knowledge_source(
    source_id: str,
    tenant: TenantContext = Depends(get_current_tenant)
):
    """
    Delete a knowledge base source and all its chunks.

    This permanently removes the content from the knowledge base.
    """
    result = await knowledge_base_service.delete_source(tenant.tenant_id, source_id)

    if not result.get("success"):
        raise HTTPException(
            status_code=404 if "not found" in result.get("error", "").lower() else 400,
            detail=result.get("error")
        )

    return {
        "success": True,
        "message": "Knowledge source deleted"
    }


# ==================== Gmail Settings Routes ====================

async def _get_tenant_brand_async(tenant_id: str) -> Optional[dict]:
    """
    Find the brand that belongs to this specific tenant — never returns another tenant's brand.
    Lookup order:
      1. Active brand with tenant_id match (primary)
      2. Inactive brand with tenant_id + gmail_connected (onboarding 409 edge case)
      3. Brand matching tenant's shopify_domain (pre-migration rows)
    NO global fallback — returns None if no owned brand found.
    """
    try:
        # Primary: explicit tenant ownership, active brands
        owned = supabase_select("brands", {"tenant_id": f"eq.{tenant_id}", "is_active": "is.true"})
        if owned:
            # Prefer the brand with Gmail connected if multiple
            gmail_brands = [b for b in owned if b.get("gmail_connected")]
            return gmail_brands[0] if gmail_brands else owned[0]

        # Fallback: inactive brand for this tenant that has Gmail connected
        # (happens after onboarding 409 flow deactivated the brand before Gmail was connected)
        inactive_gmail = supabase_select("brands", {
            "tenant_id": f"eq.{tenant_id}",
            "gmail_connected": "is.true",
        })
        if inactive_gmail:
            return inactive_gmail[0]

        # Tertiary: match via shopify_domain (rows created before tenant_id column existed)
        tenant_data = await auth_service.get_tenant(tenant_id)
        if tenant_data:
            shopify_domain = tenant_data.get("shopify_domain")
            if shopify_domain:
                brands = supabase_select("brands", {
                    "shopify_domain": f"eq.{shopify_domain}",
                    "is_active": "is.true",
                })
                if brands:
                    return brands[0]

        # Final fallback: any brand owned by this tenant, regardless of is_active or gmail_connected.
        # Catches the case where gmail_connected was just set to False (token revoked) and the
        # brand is technically inactive or has a null is_active column.
        any_brand = supabase_select("brands", {"tenant_id": f"eq.{tenant_id}"})
        if any_brand:
            return any_brand[0]

    except Exception:
        pass
    return None  # NO global fallback — prevents cross-tenant data leak


@router.get("/gmail/status")
async def get_gmail_status(tenant: TenantContext = Depends(get_current_tenant)):
    """Get Gmail connection status for this account (tenant-scoped)."""
    try:
        # Primary: active or inactive brand owned by tenant with gmail_connected=true
        gmail_brands = supabase_select("brands", {
            "tenant_id": f"eq.{tenant.tenant_id}",
            "gmail_connected": "is.true",
        })
        if gmail_brands:
            b = gmail_brands[0]
            return {
                "connected": True,
                "email": b.get("gmail_email"),
                "brand_id": b.get("id"),
                "last_polled_at": b.get("updated_at"),
            }

        # Fallback: match via shopify_domain (rows created before tenant_id migration)
        tenant_data = await auth_service.get_tenant(tenant.tenant_id)
        if tenant_data:
            shopify_domain = tenant_data.get("shopify_domain")
            if shopify_domain:
                domain_brands = supabase_select("brands", {
                    "shopify_domain": f"eq.{shopify_domain}",
                    "gmail_connected": "is.true",
                })
                if domain_brands:
                    b = domain_brands[0]
                    # Auto-link brand to this tenant
                    if not b.get("tenant_id"):
                        try:
                            supabase_update("brands", {"id": f"eq.{b['id']}"}, {"tenant_id": tenant.tenant_id})
                            logger.info(f"[Settings] Auto-linked brand {b['id']} to tenant {tenant.tenant_id}")
                        except Exception:
                            pass
                    return {
                        "connected": True,
                        "email": b.get("gmail_email"),
                        "brand_id": b.get("id"),
                        "last_polled_at": b.get("updated_at"),
                    }

        return {"connected": False, "email": None}
    except Exception as e:
        logger.error(f"Gmail status error: {e}")
        return {"connected": False, "email": None}


@router.get("/gmail/connect")
async def connect_gmail(tenant: TenantContext = Depends(get_current_tenant)):
    """
    Return the Google OAuth URL for Gmail connection.
    Frontend navigates to this URL to start the consent flow.
    """
    try:
        from src.services.brand_gmail_service import brand_gmail_service
        brand = await _get_tenant_brand_async(tenant.tenant_id)
        if not brand:
            raise HTTPException(
                status_code=400,
                detail="No brand found. Create a brand first before connecting Gmail."
            )
        auth_url = brand_gmail_service.get_auth_url(brand["id"])
        return {"auth_url": auth_url, "brand_id": brand["id"]}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Gmail connect error: {e}")
        raise HTTPException(status_code=500, detail="Failed to initiate Gmail OAuth")


@router.delete("/gmail/disconnect")
async def disconnect_gmail(tenant: TenantContext = Depends(get_current_tenant)):
    """Disconnect Gmail for this tenant's brand."""
    try:
        from src.services.brand_gmail_service import brand_gmail_service
        brand = await _get_tenant_brand_async(tenant.tenant_id)
        if not brand or not brand.get("gmail_connected"):
            return {"success": True, "message": "Gmail was not connected"}
        brand_gmail_service.disconnect(brand["id"])
        return {"success": True, "message": "Gmail disconnected"}
    except Exception as e:
        logger.error(f"Gmail disconnect error: {e}")
        raise HTTPException(status_code=500, detail="Failed to disconnect Gmail")


def _safe_select(table: str, params: dict) -> list:
    """Select rows, returning [] on any error (table missing, network, etc.)."""
    try:
        return supabase_select(table, params) or []
    except Exception:
        return []


@router.get("/gmail/queue-status")
async def get_gmail_queue_status(tenant: TenantContext = Depends(get_current_tenant)):
    """Get email send queue statistics."""
    try:
        from datetime import datetime, timezone, timedelta
        since_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        from_1h   = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

        queued   = _safe_select("send_tasks", {"status": "eq.queued"})
        failed   = _safe_select("send_tasks", {"status": "eq.failed"})
        sent_24h = _safe_select("send_tasks", {"status": "eq.sent", "sent_at": f"gte.{since_24h}"})
        hourly   = _safe_select("send_log",   {"sent_at": f"gte.{from_1h}"})

        brand = await _get_tenant_brand_async(tenant.tenant_id)

        return {
            "queued_count": len(queued),
            "sent_24h": len(sent_24h),
            "failed_count": len(failed),
            "hourly_count": len(hourly),
            "last_polled_at": brand.get("updated_at") if brand else None,
        }
    except Exception as e:
        logger.error(f"Queue status error: {e}")
        return {"queued_count": 0, "sent_24h": 0, "failed_count": 0, "hourly_count": 0, "last_polled_at": None}


# ==================== Aftership Integration ====================

class AftershipKeyRequest(BaseModel):
    aftership_api_key: str


@router.get("/aftership")
async def get_aftership_status(tenant: TenantContext = Depends(get_current_tenant)):
    """Return whether Aftership is configured for this brand."""
    try:
        brand = await _get_tenant_brand_async(tenant.tenant_id)
        if not brand:
            return {"connected": False}
        key = brand.get("aftership_api_key") or ""
        return {
            "connected": bool(key),
            "key_preview": f"...{key[-4:]}" if len(key) > 4 else ("set" if key else ""),
        }
    except Exception as e:
        logger.error(f"Aftership status error: {e}")
        return {"connected": False}


@router.post("/aftership")
async def save_aftership_key(body: AftershipKeyRequest, tenant: TenantContext = Depends(get_current_tenant)):
    """Save (or clear) the Aftership API key for this brand."""
    try:
        brand = await _get_tenant_brand_async(tenant.tenant_id)
        if not brand:
            raise HTTPException(status_code=404, detail="No brand found for this account")
        supabase_update("brands", {"id": f"eq.{brand['id']}"}, {
            "aftership_api_key": body.aftership_api_key.strip() or None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Aftership key save error: {e}")
        raise HTTPException(status_code=500, detail="Failed to save Aftership key")


@router.delete("/aftership")
async def remove_aftership_key(tenant: TenantContext = Depends(get_current_tenant)):
    """Remove the Aftership API key from this brand."""
    try:
        brand = await _get_tenant_brand_async(tenant.tenant_id)
        if not brand:
            raise HTTPException(status_code=404, detail="No brand found")
        supabase_update("brands", {"id": f"eq.{brand['id']}"}, {
            "aftership_api_key": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Aftership key remove error: {e}")
        raise HTTPException(status_code=500, detail="Failed to remove Aftership key")


# ==================== Health Check ====================

@router.get("/health")
async def settings_health():
    """Health check for settings routes."""
    return {"status": "ok", "service": "settings"}
