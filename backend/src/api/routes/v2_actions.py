"""
Actions API Routes (v2)
=======================
Multi-tenant action queue management with brand scoping.
"""

import logging
from typing import Optional, List
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field

from src.api.middleware.auth_middleware import (
    AuthenticatedContext,
    get_current_user,
    require_agent_or_admin,
    require_brand_access
)
from src.services.supabase_auth_service import UserRole
from src.lib.supabase_client import supabase_select, supabase_insert, supabase_update

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/actions", tags=["Actions"])


# ==================== Request/Response Models ====================

class CreateActionRequest(BaseModel):
    brand_id: str
    ticket_id: Optional[str] = None
    action_type: str = Field(..., pattern=r"^(refund|cancel_order|change_address|discount|exchange)$")
    customer_email: str
    customer_name: Optional[str] = None
    order_id: Optional[str] = None
    order_number: Optional[str] = None
    amount: Optional[float] = Field(None, ge=0)
    reason: Optional[str] = None
    extracted_data: Optional[dict] = None


class ApproveActionRequest(BaseModel):
    notes: Optional[str] = None


class RejectActionRequest(BaseModel):
    reason: str = Field(..., min_length=1)


class ActionResponse(BaseModel):
    id: str
    brand_id: str
    action_type: str
    status: str
    customer_email: str
    order_id: Optional[str] = None
    order_number: Optional[str] = None
    amount: Optional[float] = None
    ai_confidence: Optional[float] = None
    risk_level: str = "low"
    created_at: str


# ==================== Routes ====================

@router.get("")
async def list_actions(
    context: AuthenticatedContext = Depends(get_current_user),
    brand_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    action_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
):
    """
    List actions accessible to the user.

    Filters:
    - brand_id: Filter by brand
    - status: pending, approved, executed, rejected, failed
    - action_type: refund, cancel_order, change_address, discount, exchange
    """
    try:
        # Get user's brands
        if UserRole.is_admin(context.user.role):
            org_brands = supabase_select("brands", {
                "organization_id": f"eq.{context.user.organization_id}",
                "is_active": "eq.true"
            })
            brand_ids = [b["id"] for b in org_brands] if org_brands else []
        else:
            brand_ids = context.brand_ids

        if not brand_ids:
            return {"actions": [], "count": 0, "total": 0}

        # Apply brand filter
        if brand_id:
            if brand_id not in brand_ids:
                raise HTTPException(status_code=403, detail="Access denied to this brand")
            brand_filter = f"eq.{brand_id}"
        else:
            brand_filter = f"in.({','.join(brand_ids)})"

        filters = {
            "brand_id": brand_filter,
            "order": "created_at.desc",
            "limit": str(limit),
            "offset": str(offset)
        }

        if status:
            filters["status"] = f"eq.{status}"
        if action_type:
            filters["action_type"] = f"eq.{action_type}"

        actions = supabase_select("actions", filters)

        # Get total count
        count_filters = {k: v for k, v in filters.items() if k not in ["limit", "offset", "order"]}
        all_actions = supabase_select("actions", count_filters)
        total = len(all_actions) if all_actions else 0

        return {
            "actions": actions or [],
            "count": len(actions) if actions else 0,
            "total": total,
            "limit": limit,
            "offset": offset
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing actions: {e}")
        raise HTTPException(status_code=500, detail="Failed to list actions")


@router.get("/pending")
async def get_pending_actions(
    context: AuthenticatedContext = Depends(get_current_user),
    brand_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200)
):
    """Get all pending actions requiring approval"""
    try:
        # Get user's brands
        if UserRole.is_admin(context.user.role):
            org_brands = supabase_select("brands", {
                "organization_id": f"eq.{context.user.organization_id}",
                "is_active": "eq.true"
            })
            brand_ids = [b["id"] for b in org_brands] if org_brands else []
        else:
            brand_ids = context.brand_ids

        if not brand_ids:
            return {"actions": [], "count": 0}

        if brand_id:
            if brand_id not in brand_ids:
                raise HTTPException(status_code=403, detail="Access denied")
            brand_filter = f"eq.{brand_id}"
        else:
            brand_filter = f"in.({','.join(brand_ids)})"

        actions = supabase_select("actions", {
            "brand_id": brand_filter,
            "status": "eq.pending",
            "order": "created_at.asc",
            "limit": str(limit)
        })

        # Enrich with brand names
        if actions:
            brands = {b["id"]: b for b in supabase_select("brands", {
                "id": f"in.({','.join([a['brand_id'] for a in actions])})"
            }) or []}

            for action in actions:
                brand = brands.get(action["brand_id"], {})
                action["brand_name"] = brand.get("name", "Unknown")

        return {
            "actions": actions or [],
            "count": len(actions) if actions else 0
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting pending actions: {e}")
        raise HTTPException(status_code=500, detail="Failed to get pending actions")


@router.get("/stats")
async def get_action_stats(
    context: AuthenticatedContext = Depends(get_current_user),
    brand_id: Optional[str] = Query(None)
):
    """Get action statistics"""
    try:
        # Get user's brands
        if UserRole.is_admin(context.user.role):
            org_brands = supabase_select("brands", {
                "organization_id": f"eq.{context.user.organization_id}",
                "is_active": "eq.true"
            })
            brand_ids = [b["id"] for b in org_brands] if org_brands else []
        else:
            brand_ids = context.brand_ids

        if brand_id:
            if brand_id not in brand_ids:
                raise HTTPException(status_code=403, detail="Access denied")
            brand_ids = [brand_id]

        if not brand_ids:
            return {"stats": {}}

        brand_filter = f"in.({','.join(brand_ids)})"
        all_actions = supabase_select("actions", {"brand_id": brand_filter})
        actions = all_actions or []

        stats = {
            "total": len(actions),
            "by_status": {},
            "by_type": {},
            "by_risk_level": {},
            "total_amount": 0.0,
            "avg_confidence": None
        }

        confidences = []
        for a in actions:
            # By status
            status = a.get("status", "unknown")
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1

            # By type
            action_type = a.get("action_type", "unknown")
            stats["by_type"][action_type] = stats["by_type"].get(action_type, 0) + 1

            # By risk
            risk = a.get("risk_level", "low")
            stats["by_risk_level"][risk] = stats["by_risk_level"].get(risk, 0) + 1

            # Amounts
            if a.get("amount"):
                stats["total_amount"] += float(a["amount"])

            # Confidence
            if a.get("ai_confidence"):
                confidences.append(a["ai_confidence"])

        if confidences:
            stats["avg_confidence"] = sum(confidences) / len(confidences)

        return {"stats": stats}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting action stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to get stats")


@router.get("/{action_id}")
async def get_action(
    action_id: str,
    context: AuthenticatedContext = Depends(get_current_user)
):
    """Get a specific action"""
    try:
        actions = supabase_select("actions", {"id": f"eq.{action_id}"})
        if not actions:
            raise HTTPException(status_code=404, detail="Action not found")

        action = actions[0]

        # Verify access
        if action["brand_id"] not in context.brand_ids:
            if not UserRole.is_admin(context.user.role):
                raise HTTPException(status_code=403, detail="Access denied")

        # Get action logs
        logs = supabase_select("action_logs", {
            "action_id": f"eq.{action_id}",
            "order": "created_at.asc"
        })
        action["logs"] = logs or []

        # Get related ticket if exists
        if action.get("ticket_id"):
            tickets = supabase_select("tickets", {"id": f"eq.{action['ticket_id']}"})
            action["ticket"] = tickets[0] if tickets else None

        return {"action": action}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting action: {e}")
        raise HTTPException(status_code=500, detail="Failed to get action")


@router.post("")
async def create_action(
    request: CreateActionRequest,
    context: AuthenticatedContext = Depends(require_agent_or_admin)
):
    """Create a new action manually"""
    try:
        # Verify brand access
        if request.brand_id not in context.brand_ids:
            if not UserRole.is_admin(context.user.role):
                raise HTTPException(status_code=403, detail="Access denied to this brand")

        action_data = {
            "brand_id": request.brand_id,
            "ticket_id": request.ticket_id,
            "action_type": request.action_type,
            "customer_email": request.customer_email,
            "customer_name": request.customer_name,
            "order_id": request.order_id,
            "order_number": request.order_number,
            "amount": request.amount,
            "reason": request.reason,
            "extracted_data": request.extracted_data or {},
            "status": "pending",
            "risk_level": "low",
            "requires_approval": True
        }

        result = supabase_insert("actions", action_data)

        # Log creation
        supabase_insert("action_logs", {
            "action_id": result["id"],
            "brand_id": request.brand_id,
            "event_type": "created",
            "performed_by": context.user.user_id,
            "details": {"source": "manual"}
        })

        logger.info(f"Action created: {result['id']} by {context.user.email}")

        return {
            "success": True,
            "action": result,
            "message": "Action created successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating action: {e}")
        raise HTTPException(status_code=500, detail="Failed to create action")


@router.post("/{action_id}/approve")
async def approve_action(
    action_id: str,
    request: ApproveActionRequest = None,
    context: AuthenticatedContext = Depends(require_agent_or_admin)
):
    """Approve an action and execute it"""
    try:
        # Get action
        actions = supabase_select("actions", {"id": f"eq.{action_id}"})
        if not actions:
            raise HTTPException(status_code=404, detail="Action not found")

        action = actions[0]

        # Verify access
        if action["brand_id"] not in context.brand_ids:
            if not UserRole.is_admin(context.user.role):
                raise HTTPException(status_code=403, detail="Access denied")

        # Check status
        if action["status"] != "pending":
            raise HTTPException(status_code=400, detail=f"Action is {action['status']}, cannot approve")

        # Get brand for Shopify execution
        brands = supabase_select("brands", {"id": f"eq.{action['brand_id']}"})
        if not brands or not brands[0].get("shopify_connected"):
            raise HTTPException(status_code=400, detail="Brand not connected to Shopify")

        brand = brands[0]

        # Update to approved
        supabase_update("actions", {"id": f"eq.{action_id}"}, {
            "status": "approved",
            "approved_by": context.user.user_id,
            "approved_at": datetime.now(timezone.utc).isoformat()
        })

        # Log approval
        supabase_insert("action_logs", {
            "action_id": action_id,
            "brand_id": action["brand_id"],
            "event_type": "approved",
            "performed_by": context.user.user_id,
            "details": {"notes": request.notes if request else None}
        })

        # Execute the action
        execution_result = None
        execution_error = None

        try:
            from src.services.shopify_service import ShopifyClient

            client = ShopifyClient(
                brand["shopify_domain"],
                brand["shopify_access_token"]
            )

            if action["action_type"] == "refund":
                execution_result = await client.process_refund(
                    order_id=action["order_id"],
                    amount=action.get("amount"),
                    reason=action.get("reason", "Customer request"),
                    restock=action.get("extracted_data", {}).get("restock", True),
                    notify_customer=True
                )
            elif action["action_type"] == "cancel_order":
                execution_result = await client.cancel_order(
                    order_id=action["order_id"],
                    reason=action.get("reason", "Customer request"),
                    email_customer=True,
                    restock=True
                )
            elif action["action_type"] == "change_address":
                new_address = action.get("extracted_data", {}).get("new_address", {})
                execution_result = await client.update_shipping_address(
                    order_id=action["order_id"],
                    new_address=new_address
                )
            else:
                execution_result = {"success": True, "message": "No execution needed"}

            if execution_result.get("success"):
                supabase_update("actions", {"id": f"eq.{action_id}"}, {
                    "status": "executed",
                    "executed_at": datetime.now(timezone.utc).isoformat(),
                    "execution_result": execution_result
                })

                supabase_insert("action_logs", {
                    "action_id": action_id,
                    "brand_id": action["brand_id"],
                    "event_type": "executed",
                    "performed_by": context.user.user_id,
                    "details": execution_result
                })
            else:
                execution_error = execution_result.get("error", "Execution failed")

        except Exception as exec_error:
            execution_error = str(exec_error)

        if execution_error:
            supabase_update("actions", {"id": f"eq.{action_id}"}, {
                "status": "failed",
                "error_message": execution_error,
                "retry_count": action.get("retry_count", 0) + 1
            })

            supabase_insert("action_logs", {
                "action_id": action_id,
                "brand_id": action["brand_id"],
                "event_type": "failed",
                "performed_by": context.user.user_id,
                "details": {"error": execution_error}
            })

            logger.error(f"Action execution failed: {action_id} - {execution_error}")

            return {
                "success": False,
                "error": execution_error,
                "message": "Action approved but execution failed"
            }

        logger.info(f"Action approved and executed: {action_id} by {context.user.email}")

        return {
            "success": True,
            "execution_result": execution_result,
            "message": "Action approved and executed"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error approving action: {e}")
        raise HTTPException(status_code=500, detail="Failed to approve action")


@router.post("/{action_id}/reject")
async def reject_action(
    action_id: str,
    request: RejectActionRequest,
    context: AuthenticatedContext = Depends(require_agent_or_admin)
):
    """Reject an action"""
    try:
        # Get action
        actions = supabase_select("actions", {"id": f"eq.{action_id}"})
        if not actions:
            raise HTTPException(status_code=404, detail="Action not found")

        action = actions[0]

        # Verify access
        if action["brand_id"] not in context.brand_ids:
            if not UserRole.is_admin(context.user.role):
                raise HTTPException(status_code=403, detail="Access denied")

        # Check status
        if action["status"] != "pending":
            raise HTTPException(status_code=400, detail=f"Action is {action['status']}, cannot reject")

        # Update to rejected
        supabase_update("actions", {"id": f"eq.{action_id}"}, {
            "status": "rejected",
            "rejection_reason": request.reason,
            "rejected_by": context.user.user_id,
            "rejected_at": datetime.now(timezone.utc).isoformat()
        })

        # Log rejection
        supabase_insert("action_logs", {
            "action_id": action_id,
            "brand_id": action["brand_id"],
            "event_type": "rejected",
            "performed_by": context.user.user_id,
            "details": {"reason": request.reason}
        })

        logger.info(f"Action rejected: {action_id} by {context.user.email}")

        return {
            "success": True,
            "message": "Action rejected"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error rejecting action: {e}")
        raise HTTPException(status_code=500, detail="Failed to reject action")


@router.post("/{action_id}/retry")
async def retry_action(
    action_id: str,
    context: AuthenticatedContext = Depends(require_agent_or_admin)
):
    """Retry a failed action"""
    try:
        # Get action
        actions = supabase_select("actions", {"id": f"eq.{action_id}"})
        if not actions:
            raise HTTPException(status_code=404, detail="Action not found")

        action = actions[0]

        # Verify access
        if action["brand_id"] not in context.brand_ids:
            if not UserRole.is_admin(context.user.role):
                raise HTTPException(status_code=403, detail="Access denied")

        # Check status
        if action["status"] != "failed":
            raise HTTPException(status_code=400, detail=f"Action is {action['status']}, cannot retry")

        # Reset to pending
        supabase_update("actions", {"id": f"eq.{action_id}"}, {
            "status": "pending",
            "error_message": None
        })

        # Log retry
        supabase_insert("action_logs", {
            "action_id": action_id,
            "brand_id": action["brand_id"],
            "event_type": "retried",
            "performed_by": context.user.user_id,
            "details": {}
        })

        logger.info(f"Action retry: {action_id} by {context.user.email}")

        return {
            "success": True,
            "message": "Action reset to pending for retry"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrying action: {e}")
        raise HTTPException(status_code=500, detail="Failed to retry action")
