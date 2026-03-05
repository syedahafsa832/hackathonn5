"""
Pending Actions API Routes
Human-in-the-Loop Approval Queue Endpoints
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List
from pydantic import BaseModel

router = APIRouter(prefix="/api/actions", tags=["actions"])


# ============== Request Models ==============

class ApproveRequest(BaseModel):
    approved_by: Optional[str] = "admin"


class RejectRequest(BaseModel):
    rejection_note: str
    rejected_by: Optional[str] = "admin"


# ============== Endpoints ==============

@router.get("/pending")
async def list_pending_actions(
    status: Optional[str] = Query(None, description="Filter by status: Pending, Approved, Rejected, Executed"),
    risk_score: Optional[str] = Query(None, description="Filter by risk: Low, Medium, High"),
    limit: int = Query(20, description="Number of records to return")
):
    """
    List all pending actions for the approval queue.
    """
    try:
        from src.lib.supabase_client import supabase_select

        filters = {}
        if status:
            filters["status"] = f"eq.{status}"
        if risk_score:
            filters["risk_score"] = f"eq.{risk_score}"

        actions = supabase_select("pending_actions", filters)

        # Sort by created_at descending and limit
        actions = sorted(actions, key=lambda x: x.get("created_at", ""), reverse=True)[:limit]

        return {
            "actions": actions,
            "count": len(actions)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pending/stats")
async def get_pending_stats():
    """
    Get summary statistics of pending actions.
    """
    try:
        from src.lib.supabase_client import supabase_select

        all_actions = supabase_select("pending_actions", {})

        stats = {
            "total": len(all_actions),
            "pending": len([a for a in all_actions if a.get("status") == "Pending"]),
            "approved": len([a for a in all_actions if a.get("status") == "Approved"]),
            "executed": len([a for a in all_actions if a.get("status") == "Executed"]),
            "rejected": len([a for a in all_actions if a.get("status") == "Rejected"]),
            "high_risk": len([a for a in all_actions if a.get("risk_score") == "High" and a.get("status") == "Pending"]),
            "by_type": {
                "refund": len([a for a in all_actions if a.get("action_type") == "Refund"]),
                "exchange": len([a for a in all_actions if a.get("action_type") == "Exchange"])
            }
        }

        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{action_id}")
async def get_action(action_id: str):
    """
    Get a specific pending action by ID.
    """
    try:
        from src.lib.supabase_client import supabase_select

        actions = supabase_select("pending_actions", {"id": f"eq.{action_id}"})

        if not actions:
            raise HTTPException(status_code=404, detail="Action not found")

        return actions[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/approve/{action_id}")
async def approve_action(action_id: str, request: ApproveRequest = None):
    """
    Approve and execute a pending action (Refund or Exchange).
    Triggers Shopify API to process the action.
    """
    try:
        from src.services.actions_manager import approve_pending_action

        approved_by = request.approved_by if request else "admin"

        result = await approve_pending_action(action_id, approved_by)

        if result.get("success"):
            return {
                "success": True,
                "message": "Action approved and executed",
                "action_id": action_id
            }
        else:
            raise HTTPException(status_code=400, detail=result.get("error", "Approval failed"))

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reject/{action_id}")
async def reject_action(action_id: str, request: RejectRequest):
    """
    Reject a pending action.
    """
    try:
        from src.services.actions_manager import reject_pending_action

        result = await reject_pending_action(
            action_id=action_id,
            rejection_note=request.rejection_note,
            rejected_by=request.rejected_by
        )

        if result.get("success"):
            return {
                "success": True,
                "message": "Action rejected",
                "action_id": action_id
            }
        else:
            raise HTTPException(status_code=400, detail=result.get("error", "Rejection failed"))

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{action_id}")
async def delete_action(action_id: str):
    """
    Delete a pending action (only if still pending).
    """
    try:
        from src.lib.supabase_client import supabase_select, supabase_update

        actions = supabase_select("pending_actions", {"id": f"eq.{action_id}"})

        if not actions:
            raise HTTPException(status_code=404, detail="Action not found")

        action = actions[0]

        if action["status"] != "Pending":
            raise HTTPException(status_code=400, detail=f"Cannot delete action with status: {action['status']}")

        # Soft delete - mark as cancelled (optional)
        supabase_update("pending_actions", {"id": f"eq.{action_id}"}, {
            "status": "Rejected",
            "rejection_note": "Deleted by admin"
        })

        return {"success": True, "message": "Action deleted"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
