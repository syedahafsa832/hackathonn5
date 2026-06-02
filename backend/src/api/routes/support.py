from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from slowapi import Limiter
from slowapi.util import get_remote_address
import uuid
import logging

from src.services.supabase_service import supabase_service
from src.workers.message_processor import message_processor

logger = logging.getLogger(__name__)
router = APIRouter()
limiter = Limiter(key_func=get_remote_address)

# Pydantic models for request/response validation
class WebFormSubmission(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    email: str = Field(..., min_length=1, max_length=255)
    subject: str = Field(..., min_length=1, max_length=255)
    category: str = Field(..., pattern=r"^(technical|billing|sales|general)$")
    priority: str = Field(..., pattern=r"^(low|medium|high|critical)$")
    message: str = Field(..., min_length=1, max_length=5000)
    company: Optional[str] = None
    tenant_id: Optional[str] = None  # Optional tenant ID for multi-tenant SaaS

class TicketResponse(BaseModel):
    id: str
    status: str
    message: str
    email_sent: Optional[bool] = None

@router.post("/submit", response_model=TicketResponse)
@limiter.limit("10/minute")
async def submit_support_request(request: Request, web_form: WebFormSubmission):
    """
    Submit a new support ticket via web form.
    This endpoint processes the request synchronously through the AI pipeline
    and sends an email response if conditions are met.
    """
    try:
        logger.info(f"[WEBFORM] ========== NEW SUBMISSION ==========")
        logger.info(f"[WEBFORM] Name: {request.name}")
        logger.info(f"[WEBFORM] Email: {request.email}")
        logger.info(f"[WEBFORM] Subject: {request.subject}")
        logger.info(f"[WEBFORM] Category: {request.category}")
        logger.info(f"[WEBFORM] Priority: {request.priority}")
        logger.info(f"[WEBFORM] Message: {request.message[:100]}...")

        # Process synchronously to get the real ticket ID for the response
        result = await message_processor.process_message(
            "web_form_submission",
            {
                "channel": "web_form",
                "customer_email": request.email,
                "customer_name": request.name,
                "content": request.message,
                "subject": request.subject,
                "company": request.company,
                "tenant_id": request.tenant_id  # Pass tenant_id if provided
            }
        )

        # Extract ticket ID from result
        ticket_id = result.get("ticket_id", "pending") if result else "pending"
        status = result.get("status", "received") if result else "received"
        email_sent = result.get("email_sent", False) if result else False

        logger.info(f"[WEBFORM] Result - Ticket: {ticket_id}, Status: {status}, Email Sent: {email_sent}")
        logger.info(f"[WEBFORM] ========== COMPLETE ==========")

        return TicketResponse(
            id=str(ticket_id) if ticket_id else "pending",
            status=status,
            message="Your request has been received and is being processed by our AI.",
            email_sent=email_sent
        )

    except Exception as e:
        logger.error(f"[WEBFORM] ERROR: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.get("/ticket/{ticket_id}")
async def get_ticket_status(ticket_id: str):
    """
    Get ticket status from Supabase
    """
    try:
        # If ticket_id is "pending", return a message about async processing
        if ticket_id == "pending":
            return {"status": "processing", "message": "Ticket is being created. Please try again in a few seconds with your email."}

        ticket = await supabase_service.get_ticket_by_id(ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        return ticket
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.get("/ticket-by-email")
async def get_ticket_by_email(email: str):
    """
    Get the latest ticket for a given email address
    """
    try:
        from src.lib.supabase_client import supabase_select

        result = supabase_select(
            "tickets",
            {"customer_email": f"eq.{email}", "order": "created_at.desc", "limit": "1"}
        )

        if not result or len(result) == 0:
            raise HTTPException(status_code=404, detail="No tickets found for this email")

        return result[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/test-email")
async def test_email_send(email: str, subject: str = "Test Email", message: str = "This is a test"):
    """
    Debug endpoint to test email sending directly.
    """
    try:
        logger.info(f"[TEST-EMAIL] Testing email send to {email}")

        from production.channels.gmail_handler import gmail_handler

        result = await gmail_handler.send_reply(
            to_email=email,
            subject=subject,
            body=message
        )

        logger.info(f"[TEST-EMAIL] Result: {result}")
        return {"status": "success", "result": result}
    except Exception as e:
        logger.error(f"[TEST-EMAIL] Failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}
