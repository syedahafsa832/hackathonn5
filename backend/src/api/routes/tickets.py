from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List
from src.services.supabase_service import supabase_service
from pydantic import BaseModel

router = APIRouter(prefix="/api/tickets", tags=["tickets"])

class TicketUpdate(BaseModel):
    status: Optional[str] = None
    escalate: Optional[bool] = None
    escalation_reason: Optional[str] = None
    ai_reply: Optional[str] = None

@router.get("")
async def list_tickets(status: Optional[str] = Query(None)):
    """List all tickets with optional status filtering."""
    try:
        tickets = await supabase_service.get_tickets(status=status)
        return tickets
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{ticket_id}")
async def get_ticket(ticket_id: str):
    """Fetch a single ticket by UUID."""
    try:
        ticket = await supabase_service.get_ticket_by_id(ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        return ticket
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/{ticket_id}")
async def update_ticket(ticket_id: str, updates: TicketUpdate):
    """Update a ticket status or metadata."""
    try:
        # Filter out unset fields
        update_data = updates.dict(exclude_unset=True)
        if not update_data:
            raise HTTPException(status_code=400, detail="No updates provided")
        
        result = await supabase_service.update_ticket(ticket_id, update_data)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
