from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
import uuid

from src.services.database import get_db
from src.models.customer import Customer
from src.models.conversation import Conversation
from src.models.message import Message
from src.models.ticket import Ticket
from src.services.ticket_service import create_ticket
from src.services.customer_service import get_or_create_customer
from production.workers.message_processor import message_processor

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
    estimated_resolution: Optional[str] = None

class MessageInfo(BaseModel):
    id: str
    direction: str
    content: str
    channel: str
    created_at: str
    sender_identifier: str

class TicketDetails(BaseModel):
    id: str
    status: str
    subject: str
    category: str
    priority: str
    description: str
    created_at: str
    updated_at: str
    resolution_notes: Optional[str] = None
    conversation_history: List[MessageInfo] = []

@router.post("/submit", response_model=TicketResponse)
async def submit_support_request(
    request: WebFormSubmission,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Submit a new support ticket via web form
    """
    try:
        # Validate email format (basic validation)
        if "@" not in request.email or "." not in request.email:
            raise HTTPException(status_code=422, detail="Invalid email format")

        # Create or get customer
        customer = await get_or_create_customer(
            db=db,
            email=request.email,
            name=request.name,
            company=request.company
        )

        # Create ticket
        ticket = await create_ticket(
            db=db,
            customer_id=customer.id,
            source_channel="web_form",
            subject=request.subject,
            category=request.category,
            priority=request.priority,
            description=request.message
        )

        # Create conversation
        conversation = Conversation(
            customer_id=customer.id,
            initial_channel="web_form",
            status="open"
        )
        db.add(conversation)
        await db.flush()  # Get the conversation ID

        # Create message record
        message = Message(
            conversation_id=conversation.id,
            channel="web_form",
            direction="inbound",
            sender_identifier=request.email,
            content=request.message
        )
        db.add(message)

        # Associate ticket with conversation
        ticket.conversation_id = conversation.id

        await db.commit()

        # Direct call to message processor for immediate AI handling
        background_tasks.add_task(
            message_processor.process_message,
            "tickets_incoming",
            {
                "ticket_id": str(ticket.id),
                "customer_id": str(customer.id),
                "conversation_id": str(conversation.id),
                "channel": "web_form",
                "customer_email": request.email,
                "customer_name": request.name,
                "content": request.message,
                "subject": request.subject,
                "category": request.category,
                "action": "created"
            }
        )

        return TicketResponse(
            id=str(ticket.id),
            status="created",
            message=f"Ticket {ticket.id} created successfully",
            estimated_resolution=None
        )

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/ticket/{ticket_id}", response_model=TicketDetails)
async def get_ticket_status(
    ticket_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Get ticket status and details including conversation history
    """
    try:
        # Validate UUID format
        try:
            uuid.UUID(ticket_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid ticket ID format")

        # Query ticket
        result = await db.execute(
            Ticket.__table__.select().where(Ticket.id == uuid.UUID(ticket_id))
        )
        ticket_row = result.first()

        if not ticket_row:
            raise HTTPException(status_code=404, detail="Ticket not found")

        # Get conversation history
        from src.services.message_service import get_messages_by_conversation
        conversation_messages = await get_messages_by_conversation(db, ticket_row.conversation_id)

        # Format messages for response
        conversation_history = []
        for msg in conversation_messages:
            conversation_history.append(MessageInfo(
                id=str(msg.id),
                direction=msg.direction,
                content=msg.content,
                channel=msg.channel,
                created_at=msg.created_at.isoformat() if msg.created_at else "",
                sender_identifier=msg.sender_identifier
            ))

        return TicketDetails(
            id=str(ticket_row.id),
            status=ticket_row.status,
            subject=ticket_row.subject,
            category=ticket_row.category,
            priority=ticket_row.priority,
            description=ticket_row.description,
            created_at=ticket_row.created_at.isoformat() if ticket_row.created_at else "",
            updated_at=ticket_row.updated_at.isoformat() if ticket_row.updated_at else "",
            resolution_notes=ticket_row.resolution_notes,
            conversation_history=conversation_history
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
