from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
import uuid

from ..models.ticket import Ticket
from ..models.conversation import Conversation


async def create_ticket(
    db: AsyncSession,
    customer_id: uuid.UUID,
    source_channel: str,
    subject: str,
    category: str,
    priority: str,
    description: str,
    conversation_id: Optional[uuid.UUID] = None
) -> Ticket:
    """
    Create a new ticket in the database
    """
    ticket = Ticket(
        customer_id=customer_id,
        conversation_id=conversation_id,
        source_channel=source_channel,
        category=category,
        priority=priority,
        subject=subject,
        description=description
    )

    db.add(ticket)
    await db.flush()  # Get the ticket ID without committing

    return ticket


async def get_ticket_by_id(
    db: AsyncSession,
    ticket_id: uuid.UUID
) -> Optional[Ticket]:
    """
    Retrieve a ticket by its ID
    """
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    return result.scalar_one_or_none()


async def update_ticket_status(
    db: AsyncSession,
    ticket_id: uuid.UUID,
    status: str,
    resolution_notes: Optional[str] = None
):
    """
    Update the status of a ticket
    """
    ticket = await get_ticket_by_id(db, ticket_id)

    if not ticket:
        raise ValueError(f"Ticket with ID {ticket_id} not found")

    ticket.status = status

    if resolution_notes is not None:
        ticket.resolution_notes = resolution_notes

    # Update the updated_at timestamp
    import datetime
    ticket.updated_at = datetime.datetime.now(datetime.timezone.utc)

    await db.flush()

    return ticket


async def escalate_ticket(
    db: AsyncSession,
    ticket_id: uuid.UUID,
    escalation_reason: str
):
    """
    Escalate a ticket to human agent
    """
    ticket = await get_ticket_by_id(db, ticket_id)

    if not ticket:
        raise ValueError(f"Ticket with ID {ticket_id} not found")

    ticket.status = "escalated"
    ticket.escalation_reason = escalation_reason

    # Update the updated_at timestamp
    import datetime
    ticket.updated_at = datetime.datetime.now(datetime.timezone.utc)

    await db.flush()

    # Kafka escalation logic removed for direct flow
    logger.info(f"Ticket {ticket_id} escalated for reason: {escalation_reason}")

    return ticket


async def reopen_ticket(
    db: AsyncSession,
    ticket_id: uuid.UUID,
    reopen_reason: str = "Customer sent follow-up message"
):
    """
    Reopen a closed/resolved ticket when customer sends follow-up
    """
    ticket = await get_ticket_by_id(db, ticket_id)

    if not ticket:
        raise ValueError(f"Ticket with ID {ticket_id} not found")

    # Only reopen if ticket is currently closed/resolved
    if ticket.status in ["closed", "resolved"]:
        old_status = ticket.status
        ticket.status = "open"
        ticket.reopened_reason = reopen_reason

        # Update the updated_at timestamp
        import datetime
        ticket.updated_at = datetime.datetime.now(datetime.timezone.utc)

        await db.flush()

        return ticket
    else:
        # Ticket is already open, no need to reopen
        return ticket
