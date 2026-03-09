"""
Returns & Exchanges API Routes
Frontend-facing endpoints for managing returns, refunds, and exchanges.
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List
from pydantic import BaseModel

router = APIRouter(prefix="/api/returns", tags=["returns"])


# ============== Request Models ==============

class ReturnEligibilityRequest(BaseModel):
    order_id: str
    email: str


class ExchangeRequest(BaseModel):
    order_id: str
    email: str
    preferred_size: Optional[str] = None


class CreateReturnRequest(BaseModel):
    order_id: str
    email: str
    items: List[str]  # List of item IDs or titles
    reason: str
    return_type: str  # "refund" or "exchange"
    exchange_size: Optional[str] = None


# ============== Endpoints ==============

@router.get("/eligibility")
async def check_eligibility(
    order_id: str = Query(..., description="Order number or ID"),
    email: str = Query(..., description="Customer email")
):
    """
    Check if an order is eligible for return.

    Returns:
    {
        "eligible": true/false,
        "reason": "...",
        "order": { ... },
        "items": [ ... ]
    }
    """
    try:
        from src.services.actions_manager import actions_manager
        result = await actions_manager.check_return_eligibility(order_id, email)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/exchange/suggest")
async def suggest_exchange(
    order_id: str = Query(..., description="Order number or ID"),
    email: str = Query(..., description="Customer email"),
    preferred_size: Optional[str] = Query(None, description="Preferred size (e.g., 'Large', 'Small')")
):
    """
    Suggest size exchanges for a return-eligible order.

    Returns:
    {
        "has_exchange": true/false,
        "pitch": "...",
        "suggestions": [ ... ]
    }
    """
    try:
        from src.services.actions_manager import actions_manager

        # First check eligibility
        eligibility = await actions_manager.check_return_eligibility(order_id, email)

        if not eligibility.get("eligible"):
            return {
                "has_exchange": False,
                "pitch": eligibility.get("reason", "Order not eligible for return"),
                "suggestions": [],
                "eligibility": eligibility
            }

        # Then get exchange suggestions
        result = await actions_manager.suggest_exchange(eligibility, preferred_size)
        result["eligibility"] = eligibility

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/initiate")
async def initiate_return(request: CreateReturnRequest):
    """
    Initiate a return request.

    Creates a support ticket with return request details.
    Returns the created ticket ID.
    """
    try:
        from src.services.supabase_service import supabase_service
        from datetime import datetime

        # Try to get customer name from order lookup
        customer_name = "Customer"
        try:
            from src.services.actions_manager import actions_manager
            eligibility = await actions_manager.check_return_eligibility(request.order_id, request.email)
            if eligibility.get("order", {}).get("customer"):
                customer_name = eligibility["order"]["customer"].get("first_name", "Customer")
        except Exception:
            pass  # Use default name if lookup fails

        # Create ticket for return request with all required fields
        ticket_data = {
            "customer_email": request.email,
            "customer_name": customer_name,
            "subject": f"Return Request - Order #{request.order_id}",
            "message": f"Return Type: {request.return_type}\n"
                      f"Items: {', '.join(request.items)}\n"
                      f"Reason: {request.reason}\n"
                      f"Exchange Size: {request.exchange_size or 'N/A'}",
            "intent": "return_request",
            "sentiment": "neutral",
            "risk_level": "low",
            "confidence_score": 100,
            "escalate": False,
            "status": "return_pending",
            "return_type": request.return_type,
            "return_reason": request.reason,
            "return_items": request.items,
            "exchange_size": request.exchange_size
        }

        ticket = await supabase_service.create_ticket(ticket_data)

        return {
            "success": True,
            "ticket_id": ticket.get("id") if ticket else None,
            "message": "Return request initiated successfully",
            "return_label": "Return label will be sent via email"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history")
async def get_return_history(
    email: str = Query(..., description="Customer email"),
    limit: int = Query(10, description="Number of records to return")
):
    """
    Get return history for a customer.

    Returns tickets with return_type set.
    """
    try:
        from src.lib.supabase_client import supabase_select

        returns = supabase_select("tickets", {
            "customer_email": f"eq.{email}",
            "intent": "eq.return_request"
        })

        # Sort by created_at descending and limit
        returns = sorted(returns, key=lambda x: x.get("created_at", ""), reverse=True)[:limit]

        return {
            "returns": returns,
            "count": len(returns)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{ticket_id}")
async def get_return_status(ticket_id: str):
    """
    Get the status of a specific return request.
    """
    try:
        from src.services.supabase_service import supabase_service

        ticket = await supabase_service.get_ticket_by_id(ticket_id)

        if not ticket:
            raise HTTPException(status_code=404, detail="Return request not found")

        # Map ticket status to return status
        status_map = {
            "return_pending": "pending_review",
            "return_approved": "approved",
            "return_rejected": "rejected",
            "return_completed": "completed",
            "escalated": "under_review"
        }

        return {
            "ticket_id": ticket_id,
            "status": status_map.get(ticket.get("status"), ticket.get("status")),
            "return_type": ticket.get("return_type"),
            "reason": ticket.get("return_reason"),
            "notes": ticket.get("ai_reply") or ticket.get("escalation_reason")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
