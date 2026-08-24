"""
Multi-Brand Actions API Routes
===============================
Endpoints for managing action approval queue across multiple brands.

SECURITY: every route here was previously registered with zero
authentication (no `Depends(...)` anywhere in this file) despite being
mounted live at /api/brand-actions/* (see main.py) and covering real
Shopify-executing actions (refund, cancel_order, change_address) plus
customer PII (email, name, order id). Fixed by requiring an authenticated
agent/admin on every route and enforcing brand ownership the same way
v2_actions.py already does for the parallel `actions` table - see
tests/test_brand_actions_security.py.
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import logging
import uuid

from src.api.middleware.auth_middleware import (
    AuthenticatedContext,
    require_agent_or_admin,
)

router = APIRouter(prefix="/brand-actions", tags=["brand-actions"])
logger = logging.getLogger(__name__)


def _require_brand_access(brand_id: Optional[str], context: AuthenticatedContext):
    """Raise 403 unless the authenticated caller has access to brand_id.

    context.brand_ids is already scoped to every brand in the caller's own
    org (see supabase_auth_service.get_user_context) - admin or not - so
    there is no separate "is_admin" bypass here, matching the fix applied to
    the same bug class in v2_actions.py (see test_actions_brand_isolation.py).
    """
    if not brand_id or brand_id not in context.brand_ids:
        raise HTTPException(status_code=403, detail="Access denied to this brand")


# ============== Request/Response Models ==============

class StageActionRequest(BaseModel):
    brand_id: str = Field(..., description="Brand ID")
    ticket_id: Optional[str] = Field(None, description="Related ticket ID")
    customer_email: str = Field(..., description="Customer email")
    customer_name: Optional[str] = Field(None, description="Customer name")
    message: str = Field(..., description="Customer message to analyze")
    ai_analysis: Optional[Dict[str, Any]] = Field(None, description="AI analysis results")


class ApproveActionRequest(BaseModel):
    approved_by: Optional[str] = Field("admin", description="Who approved the action")


class RejectActionRequest(BaseModel):
    rejection_reason: str = Field(..., description="Reason for rejection")
    rejected_by: Optional[str] = Field("admin", description="Who rejected the action")


class ManualActionRequest(BaseModel):
    brand_id: str = Field(..., description="Brand ID")
    action_type: str = Field(..., description="Action type: refund, cancel_order, change_address")
    order_id: str = Field(..., description="Order ID")
    customer_email: str = Field(..., description="Customer email")
    customer_name: Optional[str] = Field(None, description="Customer name")
    amount: Optional[float] = Field(None, description="Refund amount (for refunds)")
    new_address: Optional[Dict[str, str]] = Field(None, description="New address (for address changes)")
    reason: Optional[str] = Field(None, description="Reason for the action")


# ============== Endpoints ==============

@router.get("/stats")
async def get_action_stats(
    brand_id: Optional[str] = Query(None, description="Filter by brand ID"),
    context: AuthenticatedContext = Depends(require_agent_or_admin),
):
    """
    Get action statistics for a specific brand, or across every brand the
    caller has access to (never every brand on the platform).
    """
    try:
        from src.services.multi_brand_actions import multi_brand_actions

        if brand_id:
            _require_brand_access(brand_id, context)
            return await multi_brand_actions.get_action_stats(brand_id=brand_id)

        # No brand_id given - previously returned platform-wide stats across
        # every tenant's brands. Scope to only the caller's own brands.
        if not context.brand_ids:
            return {
                "total": 0, "pending": 0, "executed": 0, "rejected": 0, "failed": 0,
                "by_type": {"refund": 0, "cancel_order": 0, "change_address": 0},
                "by_risk": {"low": 0, "medium": 0, "high": 0},
            }

        merged: Dict[str, Any] = {
            "total": 0, "pending": 0, "executed": 0, "rejected": 0, "failed": 0,
            "by_type": {"refund": 0, "cancel_order": 0, "change_address": 0},
            "by_risk": {"low": 0, "medium": 0, "high": 0},
        }
        for bid in context.brand_ids:
            stats = await multi_brand_actions.get_action_stats(brand_id=bid)
            for key in ("total", "pending", "executed", "rejected", "failed"):
                merged[key] += stats.get(key, 0)
            for key in merged["by_type"]:
                merged["by_type"][key] += stats.get("by_type", {}).get(key, 0)
            for key in merged["by_risk"]:
                merged["by_risk"][key] += stats.get("by_risk", {}).get(key, 0)
        return merged
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[BrandActions API] Error getting stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pending")
async def list_pending_actions(
    brand_id: Optional[str] = Query(None, description="Filter by brand ID"),
    status: Optional[str] = Query(None, description="Filter by status"),
    risk_level: Optional[str] = Query(None, description="Filter by risk level"),
    limit: int = Query(50, description="Max results"),
    context: AuthenticatedContext = Depends(require_agent_or_admin),
):
    """
    List pending actions for approval queue.
    Returns actions sorted by creation date (newest first).
    """
    try:
        from src.lib.supabase_client import supabase_select

        if brand_id:
            _require_brand_access(brand_id, context)
            allowed_brand_ids = [brand_id]
        else:
            # No brand_id given - previously returned every tenant's actions
            # (including customer PII). Scope to only the caller's own brands.
            allowed_brand_ids = list(context.brand_ids or [])
            if not allowed_brand_ids:
                return {"actions": [], "count": 0}

        filters = {"brand_id": f"in.({','.join(allowed_brand_ids)})"}
        if status:
            filters["status"] = f"eq.{status}"
        else:
            filters["status"] = "eq.pending"
        if risk_level:
            filters["risk_level"] = f"eq.{risk_level}"

        actions = supabase_select("brand_actions", filters)

        # Sort by created_at descending
        actions = sorted(actions, key=lambda x: x.get("created_at", ""), reverse=True)[:limit]

        # Enrich with brand names
        from src.services.brand_manager import brand_manager
        brand_cache = {}
        enriched = []

        for action in actions:
            b_id = action.get("brand_id")
            if b_id and b_id not in brand_cache:
                brand = await brand_manager.get_brand(b_id)
                brand_cache[b_id] = {
                    "name": brand.get("name") if brand else "Unknown",
                    "logo_url": brand.get("logo_url") if brand else None,
                    "primary_color": brand.get("primary_color") if brand else "#000000"
                }

            brand_info = brand_cache.get(b_id, {"name": "Unknown"})
            enriched.append({
                "id": action.get("id"),
                "brand_id": b_id,
                "brand_name": brand_info.get("name"),
                "brand_logo": brand_info.get("logo_url"),
                "brand_color": brand_info.get("primary_color"),
                "ticket_id": action.get("ticket_id"),
                "action_type": action.get("action_type"),
                "status": action.get("status"),
                "order_id": action.get("order_id"),
                "customer_email": action.get("customer_email"),
                "customer_name": action.get("customer_name"),
                "confidence_score": action.get("confidence_score"),
                "risk_level": action.get("risk_level"),
                "risk_factors": action.get("risk_factors"),
                "ai_reasoning": action.get("ai_reasoning"),
                "extracted_data": action.get("extracted_data"),
                "created_at": action.get("created_at"),
                "updated_at": action.get("updated_at")
            })

        return {
            "actions": enriched,
            "count": len(enriched)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[BrandActions API] Error listing actions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/by-brand/{brand_id}")
async def list_actions_by_brand(
    brand_id: str,
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(50, description="Max results"),
    context: AuthenticatedContext = Depends(require_agent_or_admin),
):
    """
    List all actions for a specific brand.
    """
    try:
        _require_brand_access(brand_id, context)

        from src.lib.supabase_client import supabase_select

        filters = {"brand_id": f"eq.{brand_id}"}
        if status:
            filters["status"] = f"eq.{status}"

        actions = supabase_select("brand_actions", filters)
        actions = sorted(actions, key=lambda x: x.get("created_at", ""), reverse=True)[:limit]

        return {
            "actions": actions,
            "count": len(actions),
            "brand_id": brand_id
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[BrandActions API] Error listing by brand: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{action_id}")
async def get_action(
    action_id: str,
    context: AuthenticatedContext = Depends(require_agent_or_admin),
):
    """
    Get a specific action by ID.
    """
    try:
        # Validate UUID
        try:
            uuid.UUID(action_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid action ID format")

        from src.lib.supabase_client import supabase_select
        from src.services.brand_manager import brand_manager

        actions = supabase_select("brand_actions", {"id": f"eq.{action_id}"})
        if not actions:
            raise HTTPException(status_code=404, detail="Action not found")

        action = actions[0]
        _require_brand_access(action.get("brand_id"), context)

        # Get brand info
        brand = await brand_manager.get_brand(action.get("brand_id"))
        action["brand_name"] = brand.get("name") if brand else "Unknown"
        action["brand_logo"] = brand.get("logo_url") if brand else None

        return action

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[BrandActions API] Error getting action: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/detect")
async def detect_and_stage_action(
    request: StageActionRequest,
    context: AuthenticatedContext = Depends(require_agent_or_admin),
):
    """
    Detect action from message and stage for approval.
    Used by the AI agent when it detects an action request.
    """
    try:
        _require_brand_access(request.brand_id, context)

        from src.services.multi_brand_actions import multi_brand_actions

        result = await multi_brand_actions.detect_and_stage_action(
            brand_id=request.brand_id,
            ticket_id=request.ticket_id,
            customer_email=request.customer_email,
            customer_name=request.customer_name,
            message=request.message,
            ai_analysis=request.ai_analysis
        )

        if result is None:
            return {
                "detected": False,
                "message": "No actionable request detected"
            }

        return {
            "detected": True,
            **result
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[BrandActions API] Error detecting action: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/manual")
async def create_manual_action(
    request: ManualActionRequest,
    context: AuthenticatedContext = Depends(require_agent_or_admin),
):
    """
    Create a manual action (for dashboard use).
    Allows support agents to create actions without AI detection.
    """
    try:
        _require_brand_access(request.brand_id, context)

        from src.lib.supabase_client import supabase_insert
        from datetime import datetime, timezone

        # Validate action type
        valid_types = ["refund", "cancel_order", "change_address"]
        if request.action_type not in valid_types:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid action type. Must be one of: {valid_types}"
            )

        # Build extracted data
        extracted_data = {
            "order_id": request.order_id,
            "manual": True
        }
        if request.amount:
            extracted_data["amount"] = request.amount
        if request.new_address:
            extracted_data["new_address"] = request.new_address

        action_payload = {
            "brand_id": request.brand_id,
            "action_type": request.action_type,
            "status": "pending",
            "order_id": request.order_id,
            "customer_email": request.customer_email,
            "customer_name": request.customer_name,
            "confidence_score": 1.0,
            "risk_level": "low",
            "risk_factors": ["Manual action by support"],
            "extracted_data": extracted_data,
            "ai_reasoning": request.reason or f"Manual {request.action_type} by support",
            "original_message": "Manual action created from dashboard",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }

        result = supabase_insert("brand_actions", action_payload)

        return {
            "success": True,
            "action_id": result.get("id"),
            "message": "Action created successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[BrandActions API] Error creating manual action: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/approve/{action_id}")
async def approve_action(
    action_id: str,
    request: ApproveActionRequest = None,
    context: AuthenticatedContext = Depends(require_agent_or_admin),
):
    """
    Approve and execute an action.
    This triggers the Shopify API to perform the actual action.
    """
    try:
        # Validate UUID
        try:
            uuid.UUID(action_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid action ID format")

        from src.lib.supabase_client import supabase_select
        from src.services.multi_brand_actions import multi_brand_actions

        # Ownership check happens before any state-changing call - approving
        # executes a real Shopify refund/cancellation/address-change.
        actions = supabase_select("brand_actions", {"id": f"eq.{action_id}"})
        if not actions:
            raise HTTPException(status_code=404, detail="Action not found")
        action_brand_id = actions[0].get("brand_id")
        _require_brand_access(action_brand_id, context)

        approved_by = request.approved_by if request else context.user.email
        result = await multi_brand_actions.approve_action(action_id, approved_by, brand_id=action_brand_id)

        if result.get("success"):
            return result
        else:
            raise HTTPException(status_code=400, detail=result.get("error"))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[BrandActions API] Error approving action: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reject/{action_id}")
async def reject_action(
    action_id: str,
    request: RejectActionRequest,
    context: AuthenticatedContext = Depends(require_agent_or_admin),
):
    """
    Reject an action.
    """
    try:
        # Validate UUID
        try:
            uuid.UUID(action_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid action ID format")

        from src.lib.supabase_client import supabase_select
        from src.services.multi_brand_actions import multi_brand_actions

        actions = supabase_select("brand_actions", {"id": f"eq.{action_id}"})
        if not actions:
            raise HTTPException(status_code=404, detail="Action not found")
        action_brand_id = actions[0].get("brand_id")
        _require_brand_access(action_brand_id, context)

        result = await multi_brand_actions.reject_action(
            action_id=action_id,
            rejection_reason=request.rejection_reason,
            rejected_by=request.rejected_by or context.user.email,
            brand_id=action_brand_id,
        )

        if result.get("success"):
            return result
        else:
            raise HTTPException(status_code=400, detail=result.get("error"))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[BrandActions API] Error rejecting action: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/logs/{action_id}")
async def get_action_logs(
    action_id: str,
    context: AuthenticatedContext = Depends(require_agent_or_admin),
):
    """
    Get audit logs for an action.
    """
    try:
        from src.lib.supabase_client import supabase_select

        # Logs don't carry brand_id themselves - resolve ownership via the
        # parent action, same as get_action/approve/reject above.
        actions = supabase_select("brand_actions", {"id": f"eq.{action_id}"})
        if not actions:
            raise HTTPException(status_code=404, detail="Action not found")
        _require_brand_access(actions[0].get("brand_id"), context)

        logs = supabase_select("action_logs", {"action_id": f"eq.{action_id}"})
        logs = sorted(logs, key=lambda x: x.get("timestamp", ""), reverse=True)

        return {
            "logs": logs,
            "count": len(logs)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[BrandActions API] Error getting logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{action_id}")
async def delete_action(
    action_id: str,
    context: AuthenticatedContext = Depends(require_agent_or_admin),
):
    """
    Delete a pending action.
    Only works for actions that are still pending.
    """
    try:
        # Validate UUID
        try:
            uuid.UUID(action_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid action ID format")

        from src.lib.supabase_client import supabase_select, supabase_update

        actions = supabase_select("brand_actions", {"id": f"eq.{action_id}"})
        if not actions:
            raise HTTPException(status_code=404, detail="Action not found")

        action = actions[0]
        _require_brand_access(action.get("brand_id"), context)

        if action["status"] != "pending":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot delete action with status: {action['status']}"
            )

        # Soft delete - mark as rejected
        supabase_update("brand_actions", {"id": f"eq.{action_id}"}, {
            "status": "rejected",
            "rejection_reason": "Deleted by admin"
        })

        return {"success": True, "message": "Action deleted"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[BrandActions API] Error deleting action: {e}")
        raise HTTPException(status_code=500, detail=str(e))
