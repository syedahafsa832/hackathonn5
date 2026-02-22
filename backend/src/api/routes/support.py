from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
import uuid

from src.services.supabase_service import supabase_service
from src.workers.message_processor import message_processor

router = APIRouter()

# Pydantic models for request/response validation
class WebFormSubmission(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    email: str = Field(..., min_length=1, max_length=255)
    subject: str = Field(..., min_length=1, max_length=255)
    category: str = Field(..., pattern=r"^(technical|billing|sales|general)$")
    priority: str = Field(..., pattern=r"^(low|medium|high|critical)$")
    message: str = Field(..., min_length=1, max_length=5000)
    company: Optional[str] = None

class TicketResponse(BaseModel):
    id: str
    status: str
    message: str

@router.post("/submit", response_model=TicketResponse)
async def submit_support_request(
    request: WebFormSubmission,
    background_tasks: BackgroundTasks
):
    """
    Submit a new support ticket via web form
    """
    try:
        # Generate a temporary ID for tracking if needed, 
        # though the processor will create the formal record in Supabase.
        # However, the user wants the record stored in Supabase on every query.
        
        # We'll trigger the processor which handles customer resolution and ticket creation
        background_tasks.add_task(
            message_processor.process_message,
            "web_form_submission",
            {
                "channel": "web_form",
                "customer_email": request.email,
                "customer_name": request.name,
                "content": request.message,
                "subject": request.subject,
                "company": request.company
            }
        )

        return TicketResponse(
            id="pending", # ID will be assigned by Supabase async
            status="received",
            message="Your request has been received and is being processed by our AI."
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.get("/ticket/{ticket_id}")
async def get_ticket_status(ticket_id: str):
    """
    Get ticket status from Supabase
    """
    try:
        ticket = await supabase_service.get_ticket_by_id(ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        return ticket
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
