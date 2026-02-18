from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import aliased
from typing import Optional, List
from ..models.ticket_feedback import TicketFeedback, SuccessfulQAPair
from ..models.ticket import Ticket
from ..models.message import Message
from ..models.conversation import Conversation
import uuid


async def create_ticket_feedback(
    db: AsyncSession,
    ticket_id: uuid.UUID,
    conversation_id: uuid.UUID,
    customer_rating: Optional[int] = None,
    was_helpful: Optional[bool] = None,
    feedback_comment: Optional[str] = None,
    resolution_status: str = "resolved"
) -> TicketFeedback:
    """
    Create feedback for a resolved ticket.
    """
    feedback = TicketFeedback(
        ticket_id=ticket_id,
        conversation_id=conversation_id,
        customer_rating=customer_rating,
        was_helpful=was_helpful,
        feedback_comment=feedback_comment,
        resolution_status=resolution_status
    )

    db.add(feedback)
    await db.flush()  # Get the ID
    return feedback


async def get_ticket_feedback(db: AsyncSession, ticket_id: uuid.UUID) -> Optional[TicketFeedback]:
    """
    Get feedback for a specific ticket.
    """
    result = await db.execute(
        select(TicketFeedback).where(TicketFeedback.ticket_id == ticket_id)
    )
    return result.scalar_one_or_none()


async def store_successful_qa(
    db: AsyncSession,
    question: str,
    answer: str,
    rating: int,
    ticket_id: Optional[uuid.UUID] = None,
    category: Optional[str] = None,
    channel: Optional[str] = None
) -> SuccessfulQAPair:
    """
    Store a successful Q&A pair for future reference.
    """
    qa_pair = SuccessfulQAPair(
        original_question=question,
        ai_response=answer,
        customer_rating=rating,
        ticket_id=ticket_id,
        category=category,
        channel=channel
    )

    db.add(qa_pair)
    await db.flush()  # Get the ID
    return qa_pair


async def search_successful_qa_pairs(
    db: AsyncSession,
    question: str,
    limit: int = 3,
    min_rating: int = 4
) -> List[SuccessfulQAPair]:
    """
    Find similar questions that were successfully resolved.
    Uses PostgreSQL full-text search for similarity.
    """
    # Use PostgreSQL full-text search to find similar questions
    # This uses the tsvector and plainto_tsquery functions
    result = await db.execute(
        select(SuccessfulQAPair)
        .where(
            func.to_tsvector('english', SuccessfulQAPair.original_question)
            .op('@@')(func.plainto_tsquery('english', question))
        )
        .where(SuccessfulQAPair.customer_rating >= min_rating)
        .where(SuccessfulQAPair.is_active == True)
        .order_by(
            SuccessfulQAPair.customer_rating.desc(),
            SuccessfulQAPair.times_reused.desc(),
            SuccessfulQAPair.created_at.desc()
        )
        .limit(limit)
    )

    qa_pairs = result.scalars().all()

    # Update usage count for retrieved pairs
    for qa_pair in qa_pairs:
        qa_pair.times_reused = qa_pair.times_reused + 1
        qa_pair.last_used_at = func.now()

    await db.flush()

    return qa_pairs


async def get_successful_tickets_for_learning(
    db: AsyncSession,
    limit: int = 100
) -> List[dict]:
    """
    Find tickets that were resolved with high ratings for learning.
    """
    Message2 = aliased(Message)
    
    result = await db.execute(
        select(
            Ticket.id,
            Message.content.label('question'),
            Message2.content.label('answer'),
            TicketFeedback.customer_rating,
            Ticket.category,
            Message.channel
        )
        .join(TicketFeedback, Ticket.id == TicketFeedback.ticket_id)
        .join(Conversation, Ticket.conversation_id == Conversation.id)
        .join(Message, Conversation.id == Message.conversation_id)
        .join(Message2, Conversation.id == Message2.conversation_id)
        .where(TicketFeedback.customer_rating >= 4)
        .where(TicketFeedback.resolution_status == 'resolved')
        .where(Message.direction == 'inbound')  # The customer's question
        .where(Message2.direction == 'outbound')  # The AI's response
        .where(~Ticket.id.in_(  # Exclude tickets already processed
            select(SuccessfulQAPair.ticket_id).where(SuccessfulQAPair.ticket_id.is_not(None))
        ))
        .order_by(TicketFeedback.created_at.desc())
        .limit(limit)
    )

    return [row._asdict() for row in result.all()]


async def process_successful_tickets(
    db: AsyncSession,
    limit: int = 50
) -> int:
    """
    Process resolved tickets with high ratings to extract successful Q&A pairs.
    """
    successful_tickets = await get_successful_tickets_for_learning(db, limit)

    processed_count = 0

    for ticket_data in successful_tickets:
        await store_successful_qa(
            db=db,
            question=ticket_data['question'],
            answer=ticket_data['answer'],
            rating=ticket_data['customer_rating'],
            ticket_id=ticket_data['id'],
            category=ticket_data['category'],
            channel=ticket_data['channel']
        )
        processed_count += 1

    return processed_count