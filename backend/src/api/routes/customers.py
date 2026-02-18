from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional
import uuid

from src.services.database import get_db
from src.models.customer import Customer
from src.services.customer_service import get_customer_by_identifier
from src.services.conversation_service import get_conversations_by_customer
from src.services.message_service import get_messages_by_conversation

router = APIRouter()

# Pydantic models for response validation
class CustomerDetails(BaseModel):
    id: str
    email: str
    phone: Optional[str] = None
    name: str
    company: Optional[str] = None
    created_at: str
    last_interaction: Optional[str] = None
    conversation_count: int = 0
    ticket_count: int = 0

class ConversationHistory(BaseModel):
    id: str
    customer_id: str
    initial_channel: str
    status: str
    created_at: str
    updated_at: str
    messages: list

@router.get("/lookup")
async def lookup_customer(
    identifier: str,
    type: str = "email",
    db: AsyncSession = Depends(get_db)
):
    """
    Look up customer by identifier (email or phone)
    """
    try:
        # Validate identifier type
        if type not in ["email", "phone"]:
            raise HTTPException(
                status_code=400,
                detail="Invalid identifier type. Use 'email' or 'phone'."
            )

        # Validate identifier value
        if not identifier or len(identifier.strip()) == 0:
            raise HTTPException(
                status_code=400,
                detail="Identifier cannot be empty"
            )

        # Attempt to find customer
        customer = await get_customer_by_identifier(db, identifier, type)

        if not customer:
            raise HTTPException(
                status_code=404,
                detail=f"No customer found with {type} '{identifier}'"
            )

        # Get customer's conversations to calculate metrics
        conversations = await get_conversations_by_customer(db, customer.id)
        conversation_count = len(conversations)

        # For simplicity, we'll use the last conversation's updated time as last interaction
        last_interaction = None
        if conversations:
            # Sort conversations by updated_at to get the most recent
            latest_conv = max(conversations, key=lambda c: c.updated_at or c.created_at)
            last_interaction = latest_conv.updated_at.isoformat() if latest_conv.updated_at else latest_conv.created_at.isoformat()

        # For ticket count, we'd normally join with tickets table, but for now we'll estimate
        # In a real implementation, you'd query the tickets table
        ticket_count = sum(len(await get_messages_by_conversation(db, conv.id)) for conv in conversations)

        return CustomerDetails(
            id=str(customer.id),
            email=customer.email,
            phone=customer.phone,
            name=customer.name,
            company=customer.company,
            created_at=customer.created_at.isoformat() if customer.created_at else "",
            last_interaction=last_interaction,
            conversation_count=conversation_count,
            ticket_count=ticket_count
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error while looking up customer: {str(e)}"
        )


@router.get("/{customer_id}/conversations")
async def get_customer_conversations(
    customer_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Get all conversations for a specific customer
    """
    try:
        # Validate UUID format
        customer_uuid = uuid.UUID(customer_id)

        # Get customer to verify existence
        customer_result = await db.execute(
            Customer.__table__.select().where(Customer.id == customer_uuid)
        )
        customer = customer_result.first()

        if not customer:
            raise HTTPException(
                status_code=404,
                detail=f"Customer with ID {customer_id} not found"
            )

        # Get customer's conversations
        conversations = await get_conversations_by_customer(db, customer_uuid)

        conversation_list = []
        for conv in conversations:
            # Get messages for each conversation
            messages = await get_messages_by_conversation(db, conv.id)
            message_list = []
            for msg in messages:
                message_list.append({
                    "id": str(msg.id),
                    "channel": msg.channel,
                    "direction": msg.direction,
                    "content": msg.content[:100] + "..." if len(msg.content) > 100 else msg.content,  # Truncate long content
                    "created_at": msg.created_at.isoformat() if msg.created_at else ""
                })

            conversation_list.append({
                "id": str(conv.id),
                "initial_channel": conv.initial_channel,
                "status": conv.status,
                "created_at": conv.created_at.isoformat() if conv.created_at else "",
                "updated_at": conv.updated_at.isoformat() if conv.updated_at else "",
                "messages": message_list
            })

        return {
            "customer_id": str(customer.id),
            "conversations": conversation_list
        }

    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid customer ID format. Must be a valid UUID."
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error while fetching customer conversations: {str(e)}"
        )
