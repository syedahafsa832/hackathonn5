from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import Optional, List
import uuid

from ..models.message import Message
from .sentiment_analyzer import sentiment_analyzer


async def create_message(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    channel: str,
    direction: str,
    sender_identifier: str,
    content: str,
    delivery_status: str = "pending"
) -> Message:
    """
    Create a new message
    """
    # Analyze sentiment of the message content
    sentiment_score = sentiment_analyzer.analyze_sentiment(content)

    message = Message(
        conversation_id=conversation_id,
        channel=channel,
        direction=direction,
        sender_identifier=sender_identifier,
        content=content,
        delivery_status=delivery_status,
        sentiment_score=sentiment_score
    )

    db.add(message)
    await db.flush()

    return message


async def get_message_by_id(
    db: AsyncSession,
    message_id: uuid.UUID
) -> Optional[Message]:
    """
    Retrieve a message by its ID
    """
    result = await db.execute(
        select(Message).where(Message.id == message_id)
    )
    return result.scalar_one_or_none()


async def get_messages_by_conversation(
    db: AsyncSession,
    conversation_id: uuid.UUID
) -> List[Message]:
    """
    Retrieve all messages for a conversation
    """
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    return result.scalars().all()


async def get_messages_by_channel(
    db: AsyncSession,
    channel: str,
    direction: Optional[str] = None
) -> List[Message]:
    """
    Retrieve messages by channel and optionally direction
    """
    if direction:
        result = await db.execute(
            select(Message)
            .where(and_(Message.channel == channel, Message.direction == direction))
            .order_by(Message.created_at.desc())
        )
    else:
        result = await db.execute(
            select(Message)
            .where(Message.channel == channel)
            .order_by(Message.created_at.desc())
        )

    return result.scalars().all()


async def update_message_delivery_status(
    db: AsyncSession,
    message_id: uuid.UUID,
    status: str
) -> Message:
    """
    Update the delivery status of a message
    """
    message = await get_message_by_id(db, message_id)

    if not message:
        raise ValueError(f"Message with ID {message_id} not found")

    message.delivery_status = status

    await db.flush()

    return message


async def get_recent_messages(
    db: AsyncSession,
    limit: int = 10
) -> List[Message]:
    """
    Retrieve recent messages
    """
    result = await db.execute(
        select(Message)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


async def analyze_message_sentiment(
    content: str
) -> float:
    """
    Analyze sentiment of a message content
    """
    return sentiment_analyzer.analyze_sentiment(content)


async def get_messages_by_sentiment_range(
    db: AsyncSession,
    min_sentiment: float = -1.0,
    max_sentiment: float = 1.0
) -> List[Message]:
    """
    Retrieve messages within a sentiment range
    """
    result = await db.execute(
        select(Message)
        .where(and_(
            Message.sentiment_score >= min_sentiment,
            Message.sentiment_score <= max_sentiment
        ))
        .order_by(Message.created_at.desc())
    )
    return result.scalars().all()
