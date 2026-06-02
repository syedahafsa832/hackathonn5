"""
Multi-Brand Actions API Routes
===============================
Endpoints for managing action approval queue across multiple brands.
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import logging
import uuid

router = APIRouter(prefix="/brand-actions", tags=["brand-actions"])
logger = logging.getLogger(__name__)


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
    brand_id: Optional[str] = Query(None, description="Filter by brand ID")
):
    """
    Get action statistics across all brands or for a specific brand.
    """
    try:
        from src.services.multi_brand_actions import multi_brand_actions

        stats = await multi_brand_actions.get_action_stats(brand_id=brand_id)
        return stats
    except Exception as e:
        logger.error(f"[BrandActions API] Error getting stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pending")
async def list_pending_actions(
    brand_id: Optional[str] = Query(None, description="Filter by brand ID"),
    status: Optional[str] = Query(None, description="Filter by status"),
    risk_level: Optional[str] = Query(None, description="Filter by risk level"),
    limit: int = Query(50, description="Max results")
):
    """
    List pending actions for approval queue.
    Returns actions sorted by creation date (newest first).
    """
    try:
        from src.services.multi_brand_actions import multi_brand_actions
        from src.lib.supabase_client import supabase_select

        filters = {}
        if brand_id:
            filters["brand_id"] = f"eq.{brand_id}"
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

    except Exception as e:
        logger.error(f"[BrandActions API] Error listing actions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/by-brand/{brand_id}")
async def list_actions_by_brand(
    brand_id: str,
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(50, description="Max results")
):
    """
    List all actions for a specific brand.
    """
    try:
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

    except Exception as e:
        logger.error(f"[BrandActions API] Error listing by brand: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{action_id}")
async def get_action(action_id: str):
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
async def detect_and_stage_action(request: StageActionRequest):
    """
    Detect action from message and stage for approval.
    Used by the AI agent when it detects an action request.
    """
    try:
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

    except Exception as e:
        logger.error(f"[BrandActions API] Error detecting action: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/manual")
async def create_manual_action(request: ManualActionRequest):
    """
    Create a manual action (for dashboard use).
    Allows support agents to create actions without AI detection.
    """
    try:
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
async def approve_action(action_id: str, request: ApproveActionRequest = None):
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

        from src.services.multi_brand_actions import multi_brand_actions

        approved_by = request.approved_by if request else "admin"
        result = await multi_brand_actions.approve_action(action_id, approved_by)

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
async def reject_action(action_id: str, request: RejectActionRequest):
    """
    Reject an action.
    """
    try:
        # Validate UUID
        try:
            uuid.UUID(action_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid action ID format")

        from src.services.multi_brand_actions import multi_brand_actions

        result = await multi_brand_actions.reject_action(
            action_id=action_id,
            rejection_reason=request.rejection_reason,
            rejected_by=request.rejected_by
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
async def get_action_logs(action_id: str):
    """
    Get audit logs for an action.
    """
    try:
        from src.lib.supabase_client import supabase_select

        logs = supabase_select("action_logs", {"action_id": f"eq.{action_id}"})
        logs = sorted(logs, key=lambda x: x.get("timestamp", ""), reverse=True)

        return {
            "logs": logs,
            "count": len(logs)
        }

    except Exception as e:
        logger.error(f"[BrandActions API] Error getting logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{action_id}")
async def delete_action(action_id: str):
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
