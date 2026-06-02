"""
SaaS Actions Routes
===================
Action queue management with tenant isolation.
Core product endpoints for approve/reject workflow.
"""
import logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, List, Any

from src.services.actions_service import actions_service
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/actions", tags=["Actions"])


# ==================== Request/Response Models ====================

class CreateActionRequest(BaseModel):
    action_type: str = Field(..., pattern="^(refund|cancel_order|change_address)$")
    customer_email: str
    customer_name: Optional[str] = None
    order_id: Optional[str] = None
    message: Optional[str] = None
    extracted_data: Optional[dict] = None
    confidence: Optional[float] = 0.8
    ai_reasoning: Optional[str] = None


class ApproveActionRequest(BaseModel):
    """Optional request body for approve endpoint."""
    pass


class RejectActionRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class ActionResponse(BaseModel):
    id: str
    action_type: str
    status: str
    order_id: Optional[str]
    order_number: Optional[str]
    order_total: Optional[float]
    customer_email: str
    customer_name: Optional[str]
    confidence: Optional[float]
    risk_level: Optional[str]
    risk_factors: Optional[List[str]]
    ai_reasoning: Optional[str]
    original_message: Optional[str]
    extracted_data: Optional[dict]
    execution_result: Optional[dict]
    error_message: Optional[str]
    approved_by: Optional[str]
    rejection_reason: Optional[str]
    created_at: str
    approved_at: Optional[str]
    executed_at: Optional[str]


class ActionListResponse(BaseModel):
    success: bool
    actions: List[ActionResponse]
    count: int


class ActionStatsResponse(BaseModel):
    total: int
    pending: int
    executed: int
    rejected: int
    failed: int
    by_type: dict


# ==================== Action Queue Routes ====================

@router.get("/pending")
async def get_pending_actions(
    limit: int = 50,
    tenant: TenantContext = Depends(get_current_tenant)
):
    """
    Get pending actions for approval.

    Returns all pending actions for the authenticated tenant.
    This is the main endpoint for the Action Queue page.
    """
    actions = await actions_service.get_pending_actions(tenant.tenant_id, limit)

    return {
        "success": True,
        "actions": actions,
        "count": len(actions)
    }


@router.get("/history")
async def get_action_history(
    limit: int = 100,
    tenant: TenantContext = Depends(get_current_tenant)
):
    """
    Get completed/rejected actions.

    Returns action history for the History page.
    """
    actions = await actions_service.get_action_history(tenant.tenant_id, limit)

    return {
        "success": True,
        "actions": actions,
        "count": len(actions)
    }


@router.get("/stats")
async def get_action_stats(tenant: TenantContext = Depends(get_current_tenant)):
    """
    Get action statistics.

    Returns counts by status and type.
    """
    stats = await actions_service.get_stats(tenant.tenant_id)
    return {"success": True, **stats}


@router.get("/{action_id}")
async def get_action(
    action_id: str,
    tenant: TenantContext = Depends(get_current_tenant)
):
    """
    Get a single action by ID.

    Action must belong to the authenticated tenant.
    """
    action = await actions_service.get_action(tenant.tenant_id, action_id)

    if not action:
        raise HTTPException(status_code=404, detail="Action not found")

    return {"success": True, "action": action}


# ==================== Action Execution Routes ====================

@router.post("/{action_id}/approve")
async def approve_action(
    action_id: str,
    tenant: TenantContext = Depends(get_current_tenant)
):
    """
    Approve and execute an action.

    This is the core endpoint that:
    1. Validates the action belongs to tenant
    2. Connects to Shopify
    3. Executes the action (refund/cancel/address change)
    4. Updates status and logs result

    Returns execution result or error details.
    """
    result = await actions_service.approve_action(
        tenant_id=tenant.tenant_id,
        action_id=action_id,
        approved_by=tenant.email
    )

    if not result.get("success"):
        # Return error with appropriate status code
        error_code = result.get("error_code")

        if error_code in ["invalid_token", "missing_token"]:
            raise HTTPException(
                status_code=401,
                detail={
                    "error": result.get("error"),
                    "error_code": error_code,
                    "action": "reconnect_shopify"
                }
            )

        if error_code == "order_not_found":
            raise HTTPException(
                status_code=404,
                detail={
                    "error": result.get("error"),
                    "error_code": error_code
                }
            )

        if error_code in ["order_already_cancelled", "order_already_refunded", "order_already_fulfilled"]:
            raise HTTPException(
                status_code=409,  # Conflict
                detail={
                    "error": result.get("error"),
                    "error_code": error_code
                }
            )

        if error_code == "rate_limited":
            raise HTTPException(
                status_code=429,
                detail={
                    "error": result.get("error"),
                    "error_code": error_code,
                    "action": "retry"
                }
            )

        # Generic error
        raise HTTPException(
            status_code=400,
            detail={
                "error": result.get("error"),
                "error_code": error_code or "unknown_error"
            }
        )

    return result


@router.post("/{action_id}/reject")
async def reject_action(
    action_id: str,
    request: RejectActionRequest,
    tenant: TenantContext = Depends(get_current_tenant)
):
    """
    Reject an action.

    Marks the action as rejected with the provided reason.
    """
    result = await actions_service.reject_action(
        tenant_id=tenant.tenant_id,
        action_id=action_id,
        reason=request.reason,
        rejected_by=tenant.email
    )

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))

    return result


# ==================== Action Creation (Internal Use) ====================

@router.post("/create")
async def create_action(
    request: CreateActionRequest,
    tenant: TenantContext = Depends(get_current_tenant)
):
    """
    Create a new pending action.

    Primarily used by the AI system to stage actions for approval.
    Can also be used manually for testing.
    """
    result = await actions_service.create_action(
        tenant_id=tenant.tenant_id,
        action_type=request.action_type,
        customer_email=request.customer_email,
        customer_name=request.customer_name,
        order_id=request.order_id,
        message=request.message,
        extracted_data=request.extracted_data,
        confidence=request.confidence,
        ai_reasoning=request.ai_reasoning
    )

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))

    return result


# ==================== Health Check ====================

@router.get("/health")
async def actions_health():
    """Health check for actions routes."""
    return {"status": "ok", "service": "actions"}
