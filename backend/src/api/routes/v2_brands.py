"""
Brands API Routes (v2)
======================
Uses v1 tenant JWT auth and the actual brands table schema.
Replaces the old version that referenced non-existent columns
(organization_id, slug, ai_auto_respond, etc.).
"""

import asyncio
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from datetime import datetime, timezone

from src.api.middleware.tenant_auth import get_current_tenant, TenantContext
from src.lib.supabase_client import supabase_select, supabase_insert, supabase_update, supabase_delete, supabase_rpc
from src.services.shopify_service import encrypt_token
from src.agent import reply_style_presets
from src.services import reply_style_service
from src.services import shopify_import_service
from src.services import shopify_scope_service
from src.services.supabase_service import supabase_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/brands", tags=["Brands v2"])

SAFE_COLUMNS = {"id", "name", "shopify_shop_name", "shopify_domain", "shopify_connected",
                "support_email", "is_active", "gmail_email", "gmail_connected",
                "return_policy_days", "auto_approve_threshold", "created_at", "updated_at",
                "tenant_id", "exclude_digital_products", "refund_notes", "final_sale_tags",
                "agent_name", "email_signature",
                "reply_style_mode", "reply_style_preset", "reply_style_profile",
                "reply_style_reasoning", "reply_style_learn_automatically",
                "reply_style_use_uploaded_only", "reply_style_last_generated_at"}


def _strip_secrets(brand: dict) -> dict:
    return {k: v for k, v in brand.items() if k in SAFE_COLUMNS}


def _get_owned_brand(brand_id: str, tenant_id: str) -> dict:
    """Fetch a brand and verify it belongs to the current tenant.
    Raises 404 (not 403) for a brand that exists but belongs to someone
    else — an unauthorized caller shouldn't be able to distinguish 'not
    yours' from 'doesn't exist' by probing IDs."""
    brands = supabase_select("brands", {"id": f"eq.{brand_id}", "tenant_id": f"eq.{tenant_id}"})
    if not brands:
        raise HTTPException(status_code=404, detail="Brand not found")
    return brands[0]


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
    exclude_digital_products: Optional[bool] = None
    refund_notes: Optional[str] = None
    final_sale_tags: Optional[list[str]] = None
    agent_name: Optional[str] = Field(None, max_length=20)
    email_signature: Optional[str] = Field(None, max_length=500)


class ConnectShopifyRequest(BaseModel):
    shop_domain: str = Field(..., min_length=3)
    access_token: str = Field(..., min_length=10)


class TestReplyRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)


class ExcludedIdsRequest(BaseModel):
    ids: list[int] = Field(default_factory=list)


class UpdateReplyStyleRequest(BaseModel):
    mode: Optional[str] = Field(None, pattern="^(preset|learned|disabled)$")
    preset: Optional[str] = None
    learn_automatically: Optional[bool] = None
    use_uploaded_only: Optional[bool] = None


class AddReplyExampleRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000)


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
        from src.services.plan_service import check_limit, build_limit_error
        brand_limit = check_limit(tenant.tenant_id, "brands", email=tenant.email)
        if not brand_limit["allowed"]:
            raise HTTPException(status_code=402, detail=build_limit_error("brands", brand_limit))

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
    except HTTPException:
        raise
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
        brand = _get_owned_brand(brand_id, tenant.tenant_id)
        return {"brand": _strip_secrets(brand)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting brand: {e}")
        raise HTTPException(status_code=500, detail="Failed to get brand")


@router.get("/{brand_id}/feedback")
async def list_brand_feedback(
    brand_id: str,
    rating: Optional[str] = None,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Recent customer feedback (rating + optional written comment) for this
    brand — powers the dashboard's feedback view and the testimonials/trust
    section (rating=positive filter, real comments only, never fabricated)."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        params = {
            "brand_id": f"eq.{brand_id}",
            "order": "created_at.desc",
            "limit": "50",
        }
        if rating:
            params["rating"] = f"eq.{rating}"
        feedback = supabase_select("chat_feedback", params)
        return {"feedback": feedback or []}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing brand feedback: {e}")
        raise HTTPException(status_code=500, detail="Failed to list feedback")


@router.patch("/{brand_id}")
async def update_brand(
    brand_id: str,
    request: UpdateBrandRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Update brand settings."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        updates = request.model_dump(exclude_none=True)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        result = supabase_update("brands", {"id": f"eq.{brand_id}"}, updates)
        return {"success": True, "brand": _strip_secrets(result) if result else None}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating brand: {e}")
        raise HTTPException(status_code=500, detail="Failed to update brand")


@router.get("/{brand_id}/refund-policy/excluded-products")
async def list_excluded_products(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """List Shopify product IDs excluded from refund eligibility for this brand."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        rows = supabase_select("refund_policy_excluded_products", {"brand_id": f"eq.{brand_id}"})
        return {"ids": [r["shopify_product_id"] for r in rows]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing excluded products: {e}")
        raise HTTPException(status_code=500, detail="Failed to list excluded products")


@router.put("/{brand_id}/refund-policy/excluded-products")
async def replace_excluded_products(
    brand_id: str,
    request: ExcludedIdsRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Replace the full list of Shopify product IDs excluded from refund eligibility.
    Runs atomically via a Postgres function (see migration 024) so a failure
    partway through can never leave the list half-written."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        result = supabase_rpc("replace_refund_policy_excluded_products", {
            "p_brand_id": brand_id,
            "p_product_ids": request.ids,
        })
        if result is not True:
            raise HTTPException(status_code=500, detail="Failed to update excluded products")
        return {"success": True, "ids": request.ids}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating excluded products: {e}")
        raise HTTPException(status_code=500, detail="Failed to update excluded products")


@router.get("/{brand_id}/refund-policy/excluded-collections")
async def list_excluded_collections(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """List Shopify collection IDs excluded from refund eligibility for this brand."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        rows = supabase_select("refund_policy_excluded_collections", {"brand_id": f"eq.{brand_id}"})
        return {"ids": [r["shopify_collection_id"] for r in rows]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing excluded collections: {e}")
        raise HTTPException(status_code=500, detail="Failed to list excluded collections")


@router.put("/{brand_id}/refund-policy/excluded-collections")
async def replace_excluded_collections(
    brand_id: str,
    request: ExcludedIdsRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Replace the full list of Shopify collection IDs excluded from refund eligibility.
    Runs atomically via a Postgres function (see migration 024) so a failure
    partway through can never leave the list half-written."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        result = supabase_rpc("replace_refund_policy_excluded_collections", {
            "p_brand_id": brand_id,
            "p_collection_ids": request.ids,
        })
        if result is not True:
            raise HTTPException(status_code=500, detail="Failed to update excluded collections")
        return {"success": True, "ids": request.ids}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating excluded collections: {e}")
        raise HTTPException(status_code=500, detail="Failed to update excluded collections")


@router.delete("/{brand_id}")
async def delete_brand(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Soft-delete a brand (marks inactive)."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        supabase_update("brands", {"id": f"eq.{brand_id}"}, {"is_active": False})
        return {"success": True, "message": "Brand deactivated"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting brand: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete brand")


async def _connect_shopify_credentials(brand_id: str, tenant_id: str, shop_domain: str, access_token: str) -> dict:
    """Core of connecting a Shopify store to a brand — shared by the manual
    access-token endpoint below and the OAuth callback (shopify_auth.py),
    so the domain-conflict-claim logic only exists once. Never raises
    HTTPException (callers translate the returned shape appropriately —
    one into an HTTP error response, the other into a redirect).

    If another brand already owns this domain (unique constraint), we claim
    that brand for this tenant and deactivate the newly-created placeholder
    brand."""
    from src.services.shopify_service import ShopifyClient

    # ShopifyClient._normalize_domain() strips a pasted http(s):// scheme,
    # trailing slash, and appends .myshopify.com — do that once here and
    # reuse client_shopify.shop_domain everywhere below, instead of a
    # second hand-rolled normalization that didn't strip the scheme and
    # let "https://store.myshopify.com" get stored verbatim.
    client_shopify = ShopifyClient(shop_domain, access_token)
    shop_domain = client_shopify.shop_domain
    validation = await client_shopify.validate_connection()

    if not validation.get("success"):
        return {"success": False, "status_code": 400, "error": validation.get("error", "Failed to connect to Shopify"), "error_code": "connection_failed"}

    shopify_fields = {
        "shopify_domain": shop_domain,
        "shopify_access_token": encrypt_token(access_token),
        "shopify_shop_name": validation.get("shop_name"),
        "shopify_connected": True,
        "tenant_id": tenant_id,
    }

    active_brand_id = brand_id
    try:
        supabase_update("brands", {"id": f"eq.{brand_id}"}, shopify_fields)
    except Exception as upd_err:
        err_str = str(upd_err)
        if "409" in err_str or "23505" in err_str or "conflict" in err_str.lower():
            # Domain unique constraint — the domain is already connected to some
            # brand. Only claim it if that brand has no owner yet (a genuinely
            # unclaimed placeholder). If it already belongs to a different,
            # real tenant, claiming it would silently hijack that tenant's
            # brand (their Shopify connection, Gmail connection, tickets, the
            # works) onto this caller's account - confirmed as a real incident
            # during testing, not a hypothetical. Reject instead.
            existing = supabase_select("brands", {"shopify_domain": f"eq.{shop_domain}"})
            if not existing:
                raise
            existing_brand = existing[0]
            existing_tenant_id = existing_brand.get("tenant_id")
            if existing_tenant_id and existing_tenant_id != tenant_id:
                logger.warning(
                    f"[v2/brands] Rejected Shopify connect: domain {shop_domain} already "
                    f"belongs to tenant {existing_tenant_id}, not requesting tenant {tenant_id}"
                )
                return {"success": False, "status_code": 409, "error": "This Shopify store is already connected to a different tResolv account.", "error_code": "domain_taken"}
            active_brand_id = existing_brand["id"]
            supabase_update("brands", {"id": f"eq.{active_brand_id}"}, {
                "tenant_id": tenant_id,
                "shopify_access_token": encrypt_token(access_token),
                "shopify_connected": True,
                "is_active": True,
            })
            # Deactivate the empty placeholder that was just created
            if active_brand_id != brand_id:
                supabase_update("brands", {"id": f"eq.{brand_id}"}, {"is_active": False})
            logger.info(f"[v2/brands] Claimed unowned brand {active_brand_id} for tenant {tenant_id}")
        else:
            raise

    # Best-effort: record which scopes this token actually has, right
    # now, so onboarding/import can show a precise message instead of
    # discovering a missing permission mid-import.
    await shopify_scope_service.check_and_store_scopes(active_brand_id, client_shopify)

    await supabase_service.log_onboarding_event(active_brand_id, "shopify_connected", {
        "shop_domain": shop_domain,
    })

    return {
        "success": True,
        "shop_name": validation.get("shop_name"),
        "shop_domain": shop_domain,
        "brand_id": active_brand_id,  # May differ from URL brand_id after 409 resolution
        "client": client_shopify,  # reused by callers that want get_counts() without reconnecting
    }


@router.get("/{brand_id}/shopify/oauth/start")
async def shopify_oauth_start(
    brand_id: str,
    shop: str = Query(..., description="Store domain, e.g. mybrand or mybrand.myshopify.com"),
    return_to: str = Query("onboarding", description="Dashboard page to return to after connecting: 'onboarding' or 'settings'"),
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Authenticated: returns the Shopify OAuth authorization URL for this
    brand. The frontend navigates the browser to it directly (a full-page
    redirect can't carry the Authorization header, so the actual OAuth
    callback below has no auth dependency — the signed state proves which
    brand/tenant initiated it, same pattern as the Gmail OAuth flow)."""
    _get_owned_brand(brand_id, tenant.tenant_id)
    from src.services.shopify_oauth import get_authorize_url
    try:
        auth_url = get_authorize_url(brand_id, shop, return_to)
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"auth_url": auth_url}


@router.post("/{brand_id}/shopify/connect")
async def connect_shopify(
    brand_id: str,
    request: ConnectShopifyRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Connect a Shopify store to a brand via a pasted Admin API access
    token (manual fallback — the primary path is the OAuth flow above)."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        result = await _connect_shopify_credentials(brand_id, tenant.tenant_id, request.shop_domain, request.access_token)
        if not result.get("success"):
            raise HTTPException(status_code=result.get("status_code", 400), detail=result.get("error"))
        return {
            "success": True,
            "shop_name": result.get("shop_name"),
            "shop_domain": result.get("shop_domain"),
            "brand_id": result.get("brand_id"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error connecting Shopify: {e}")
        raise HTTPException(status_code=500, detail="Failed to connect Shopify")


@router.post("/{brand_id}/shopify/import")
async def start_shopify_import(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Kick off the background import of products/policies/pages into the
    brand's knowledge base. Fire-and-forget - poll import-status for progress."""
    try:
        brand = _get_owned_brand(brand_id, tenant.tenant_id)
        if not brand.get("shopify_connected"):
            raise HTTPException(status_code=400, detail="Connect Shopify before importing.")

        if shopify_import_service.get_import_status(brand_id) == "running":
            return {"success": True, "status": "running"}

        granted = brand.get("shopify_granted_scopes")
        if granted is None:
            # Brand connected before scope tracking existed - check live now
            # rather than starting an import that's blind to what will fail.
            client = shopify_import_service._get_client_for_brand(brand)
            if client:
                result = await shopify_scope_service.check_and_store_scopes(brand_id, client)
                granted = result.get("granted_scopes")
            granted = granted or []

        missing = shopify_scope_service.missing_scopes(granted, shopify_scope_service.IMPORT_SCOPES)
        if len(missing) == len(shopify_scope_service.IMPORT_SCOPES):
            # Neither read_products nor read_content is granted - every
            # resource the importer knows how to fetch would 403. Don't run
            # a doomed import; tell the merchant exactly what's missing.
            shopify_scope_service.set_blocked(brand_id, missing)
            return {
                "success": True,
                "status": "blocked_missing_scopes",
                "missing_scopes": missing,
                "message": "Your Shopify connection works, but additional permissions are required to import products and store content.",
                "reason": "These permissions allow tResolv to understand your products, policies, and store information so Luna can answer customers accurately.",
            }
        shopify_scope_service.clear_blocked(brand_id)

        await supabase_service.log_onboarding_event(brand_id, "shopify_import_started", {})
        asyncio.create_task(shopify_import_service.run_shopify_import(brand_id))
        return {"success": True, "status": "running"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting Shopify import: {e}")
        raise HTTPException(status_code=500, detail="Failed to start import")


@router.get("/{brand_id}/shopify/import-status")
async def get_shopify_import_status(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Poll target for onboarding's import-progress screen."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        sources = supabase_select("knowledge_base_sources", {
            "brand_id": f"eq.{brand_id}",
            "source_type": f"eq.{shopify_import_service.SOURCE_TYPE}",
            "order": "created_at.asc",
        })
        blocked_scopes = shopify_scope_service.get_blocked(brand_id)
        status = "blocked_missing_scopes" if blocked_scopes else shopify_import_service.get_import_status(brand_id)
        missing_scopes = blocked_scopes if blocked_scopes else shopify_import_service.get_missing_scopes(brand_id)
        return {
            "status": status,
            "missing_scopes": missing_scopes,
            "report": shopify_import_service.get_import_report(brand_id),
            "sources": [
                {
                    "name": s.get("name"),
                    "status": s.get("status"),
                    "chunk_count": s.get("chunk_count"),
                    "metadata": s.get("metadata"),
                }
                for s in (sources or [])
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting import status: {e}")
        raise HTTPException(status_code=500, detail="Failed to get import status")


@router.get("/{brand_id}/shopify/health")
async def get_shopify_health(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Shopify connection health: which store/app is connected, what scopes
    that token actually has, and what's missing — the single place to answer
    'why isn't this working' without digging through logs."""
    try:
        brand = _get_owned_brand(brand_id, tenant.tenant_id)
        if not brand.get("shopify_connected"):
            return {"connected": False}

        granted = brand.get("shopify_granted_scopes")
        app_name = brand.get("shopify_app_name")
        checked_at = brand.get("shopify_scopes_checked_at")

        if granted is None:
            client = shopify_import_service._get_client_for_brand(brand)
            if client:
                result = await shopify_scope_service.check_and_store_scopes(brand_id, client)
                granted, app_name, checked_at = (
                    result.get("granted_scopes"), result.get("app_name"), result.get("checked_at")
                )

        granted = granted or []
        missing = shopify_scope_service.missing_scopes(granted, list(shopify_scope_service.REQUIRED_SCOPES.keys()))

        return {
            "connected": True,
            "domain": brand.get("shopify_domain"),
            "app_name": app_name,
            "granted_scopes": granted,
            "missing_scopes": missing,
            "missing_scope_labels": [shopify_scope_service.REQUIRED_SCOPES.get(s, s) for s in missing],
            "status": "needs_permission_update" if missing else "healthy",
            "checked_at": checked_at,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting Shopify health for brand {brand_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get Shopify connection health")


@router.post("/{brand_id}/shopify/disconnect")
async def disconnect_shopify(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Disconnect Shopify from a brand."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        supabase_update("brands", {"id": f"eq.{brand_id}"}, {
            "shopify_domain": None,
            "shopify_access_token": None,
            "shopify_shop_name": None,
            "shopify_connected": False,
            "shopify_granted_scopes": None,
            "shopify_app_name": None,
            "shopify_scopes_checked_at": None,
        })
        shopify_scope_service.clear_blocked(brand_id)
        return {"success": True, "message": "Shopify disconnected"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error disconnecting Shopify: {e}")
        raise HTTPException(status_code=500, detail="Failed to disconnect Shopify")


# ==================== Reply Style ====================
# Wording/tone personalization — separate from Identity (agent_name,
# email_signature, both already covered by the generic PATCH above).
# Reply Style never affects facts, refund eligibility, or business logic.

@router.get("/{brand_id}/reply-style")
async def get_reply_style(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Current Reply Style state for the settings page: mode, active preset
    or learned profile, reasoning, learning controls, and the preset catalog."""
    try:
        brand = _get_owned_brand(brand_id, tenant.tenant_id)

        # Best-effort opportunistic regeneration check — never blocks the response.
        try:
            await reply_style_service.regenerate_if_due(brand_id)
            brand = _get_owned_brand(brand_id, tenant.tenant_id)
        except Exception as e:
            logger.warning(f"[ReplyStyle] regenerate_if_due check failed: {e}")

        active_style = reply_style_service.get_active_style(brand)
        approved_count = reply_style_service.count_eligible_approved_replies(brand_id)

        return {
            "mode": brand.get("reply_style_mode") or "preset",
            "preset": brand.get("reply_style_preset"),
            "learned_profile": brand.get("reply_style_profile"),
            "reasoning": brand.get("reply_style_reasoning"),
            "active_style": active_style,
            "learn_automatically": brand.get("reply_style_learn_automatically", True),
            "use_uploaded_only": brand.get("reply_style_use_uploaded_only", False),
            "last_generated_at": brand.get("reply_style_last_generated_at"),
            "approved_reply_count": approved_count,
            "eligible_for_learning": approved_count >= reply_style_service.MIN_APPROVED_REPLIES_TO_LEARN,
            "min_replies_required": reply_style_service.MIN_APPROVED_REPLIES_TO_LEARN,
            "presets": reply_style_presets.list_presets(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting reply style: {e}")
        raise HTTPException(status_code=500, detail="Failed to get reply style")


@router.patch("/{brand_id}/reply-style")
async def update_reply_style(
    brand_id: str,
    request: UpdateReplyStyleRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Update mode/preset/learning controls. Switching to 'learned' this way
    requires a profile to already exist — use switch-to-learned for the
    guided first transition, this endpoint is for toggling back and forth
    afterwards or changing controls."""
    try:
        brand = _get_owned_brand(brand_id, tenant.tenant_id)

        if request.mode == "learned" and not brand.get("reply_style_profile"):
            raise HTTPException(status_code=400, detail="No learned profile available yet.")
        if request.preset and request.preset not in reply_style_presets.PRESETS:
            raise HTTPException(status_code=400, detail="Unknown preset.")

        updates = {}
        if request.mode is not None:
            updates["reply_style_mode"] = request.mode
        if request.preset is not None:
            updates["reply_style_preset"] = request.preset
        if request.learn_automatically is not None:
            updates["reply_style_learn_automatically"] = request.learn_automatically
        if request.use_uploaded_only is not None:
            updates["reply_style_use_uploaded_only"] = request.use_uploaded_only

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        result = supabase_update("brands", {"id": f"eq.{brand_id}"}, updates)
        return {"success": True, "brand": _strip_secrets(result) if result else None}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating reply style: {e}")
        raise HTTPException(status_code=500, detail="Failed to update reply style")


@router.post("/{brand_id}/reply-style/regenerate")
async def regenerate_reply_style(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Manual regenerate — bypasses the 15-new-replies/7-day triggers but
    still requires the minimum approved-reply count."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        result = await reply_style_service.generate_learned_profile(brand_id, force=False)
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error regenerating reply style: {e}")
        raise HTTPException(status_code=500, detail="Failed to regenerate reply style")


@router.post("/{brand_id}/reply-style/switch-to-learned")
async def switch_reply_style_to_learned(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Full replacement of the active preset with the learned profile — no
    blending, no confidence comparison, per spec."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        result = reply_style_service.switch_to_learned(brand_id)
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error switching to learned style: {e}")
        raise HTTPException(status_code=500, detail="Failed to switch to learned style")


@router.get("/{brand_id}/reply-style/examples")
async def list_reply_examples(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Optional uploaded example replies — seed data for faster personalization."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        rows = supabase_select("reply_style_examples", {
            "brand_id": f"eq.{brand_id}", "order": "created_at.desc",
        })
        return {"examples": rows or []}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing reply examples: {e}")
        raise HTTPException(status_code=500, detail="Failed to list examples")


@router.post("/{brand_id}/reply-style/examples")
async def add_reply_example(
    brand_id: str,
    request: AddReplyExampleRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        result = supabase_insert("reply_style_examples", {
            "brand_id": brand_id,
            "content": request.content.strip(),
        })
        return {"success": True, "example": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding reply example: {e}")
        raise HTTPException(status_code=500, detail="Failed to add example")


@router.delete("/{brand_id}/reply-style/examples/{example_id}")
async def delete_reply_example(
    brand_id: str,
    example_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        supabase_delete("reply_style_examples", {"id": f"eq.{example_id}", "brand_id": f"eq.{brand_id}"})
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting reply example: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete example")


# ==================== Test Luna (onboarding activation) ====================

@router.post("/{brand_id}/test-reply")
async def test_reply(
    brand_id: str,
    request: TestReplyRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Runs a sample question through the real agent so a merchant can see an
    actual generated reply during onboarding, before any real customer email
    arrives. Same code path production replies use - not a canned response."""
    try:
        brand = _get_owned_brand(brand_id, tenant.tenant_id)

        from src.agent.customer_success_agent import customer_success_agent
        # Chat-mode formatting is driven by customer_info["channel"]="chat"
        # inside the prompt builder - no text prefix, so it can never leak
        # into a stored action's original_message if this test message
        # happens to trigger real staging.
        result = await customer_success_agent.process_customer_query(
            query=f"Customer: {request.message}",
            customer_info={"name": "Test Customer", "email": "test@example.com", "channel": "chat"},
            tenant_id=brand.get("tenant_id"),
            store_id=brand_id,
        )

        await supabase_service.log_onboarding_event(brand_id, "test_reply_generated", {
            "question": request.message,
        })

        return {
            "success": True,
            "question": request.message,
            "reply": result.get("reply_body"),
            "confidence_score": result.get("confidence_score"),
            # True only when every configured AI model (all Mistral + Groq keys)
            # was out of quota for this request — lets the onboarding UI show a
            # clear "AI is at capacity" notice instead of presenting the generic
            # customer-facing fallback copy as if it were a real Luna reply.
            "provider_outage": result.get("provider_outage", False),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating test reply: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate test reply")
