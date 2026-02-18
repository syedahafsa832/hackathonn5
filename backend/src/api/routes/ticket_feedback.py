from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional
import uuid

from src.services.database import get_db
from src.services.ticket_feedback_service import create_ticket_feedback
from src.services.ticket_service import get_ticket_by_id
from src.models.ticket import Ticket

router = APIRouter()


class TicketFeedbackRequest(BaseModel):
    ticket_id: str = Field(..., description="UUID of the ticket")
    customer_rating: Optional[int] = Field(None, ge=1, le=5, description="Rating from 1-5 stars")
    was_helpful: Optional[bool] = Field(None, description="Was the resolution helpful?")
    feedback_comment: Optional[str] = Field(None, max_length=1000, description="Additional feedback comment")
    resolution_status: str = Field("resolved", description="Resolution status")


class TicketFeedbackResponse(BaseModel):
    id: str
    status: str
    message: str


@router.post("/submit", response_model=TicketFeedbackResponse)
async def submit_ticket_feedback(
    request: TicketFeedbackRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Submit feedback for a resolved ticket
    """
    try:
        # Validate ticket exists
        ticket_id = uuid.UUID(request.ticket_id)
        ticket = await get_ticket_by_id(db, ticket_id)

        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")

        # Validate rating if provided
        if request.customer_rating is not None and (request.customer_rating < 1 or request.customer_rating > 5):
            raise HTTPException(status_code=422, detail="Rating must be between 1 and 5")

        # Create feedback record
        feedback = await create_ticket_feedback(
            db=db,
            ticket_id=ticket_id,
            conversation_id=ticket.conversation_id,
            customer_rating=request.customer_rating,
            was_helpful=request.was_helpful,
            feedback_comment=request.feedback_comment,
            resolution_status=request.resolution_status
        )

        # If feedback is positive (rating >= 4), trigger learning process
        if request.customer_rating and request.customer_rating >= 4:
            # In the background, process this as a successful interaction for learning
            # This will be picked up by the learning system
            pass  # The process_successful_tickets function will pick this up

        await db.commit()

        return TicketFeedbackResponse(
            id=str(feedback.id),
            status="submitted",
            message=f"Feedback for ticket {ticket_id} submitted successfully"
        )

    except ValueError:  # Invalid UUID
        raise HTTPException(status_code=422, detail="Invalid ticket ID format")
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/ticket/{ticket_id}", response_model=TicketFeedbackResponse)
async def get_ticket_feedback(
    ticket_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Get feedback for a specific ticket
    """
    try:
        ticket_uuid = uuid.UUID(ticket_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid ticket ID format")

    # In a real implementation, we would return the feedback
    # For now, we'll just return a placeholder
    return TicketFeedbackResponse(
        id=ticket_id,
        status="not_implemented",
        message="Feedback retrieval endpoint - would return actual feedback data"
    )