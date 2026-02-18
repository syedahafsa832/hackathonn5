from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional, List
import uuid

from ..models.conversation import Conversation
from ..models.message import Message
from .sentiment_analyzer import sentiment_analyzer


async def create_conversation(
    db: AsyncSession,
    customer_id: uuid.UUID,
    initial_channel: str
) -> Conversation:
    """
    Create a new conversation
    """
    conversation = Conversation(
        customer_id=customer_id,
        initial_channel=initial_channel,
        status="open"
    )

    db.add(conversation)
    await db.flush()

    return conversation


async def get_conversation_by_id(
    db: AsyncSession,
    conversation_id: uuid.UUID
) -> Optional[Conversation]:
    """
    Retrieve a conversation by its ID
    """
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    return result.scalar_one_or_none()


async def get_conversations_by_customer(
    db: AsyncSession,
    customer_id: uuid.UUID
) -> List[Conversation]:
    """
    Retrieve all conversations for a customer
    """
    result = await db.execute(
        select(Conversation).where(Conversation.customer_id == customer_id)
    )
    return result.scalars().all()


async def add_message_to_conversation(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    channel: str,
    direction: str,
    sender_identifier: str,
    content: str
) -> Message:
    """
    Add a message to a conversation and analyze sentiment
    """
    # Analyze sentiment of the message content
    sentiment_score = sentiment_analyzer.analyze_sentiment(content)

    message = Message(
        conversation_id=conversation_id,
        channel=channel,
        direction=direction,
        sender_identifier=sender_identifier,
        content=content,
        sentiment_score=sentiment_score
    )

    db.add(message)
    await db.flush()

    # Update conversation's updated_at timestamp
    conversation = await get_conversation_by_id(db, conversation_id)
    if conversation:
        import datetime
        conversation.updated_at = datetime.datetime.now(datetime.timezone.utc)
        await db.flush()

    return message


async def get_messages_by_conversation(
    db: AsyncSession,
    conversation_id: uuid.UUID
) -> List[Message]:
    """
    Retrieve all messages in a conversation
    """
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    return result.scalars().all()


async def update_conversation_status(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    status: str
) -> Conversation:
    """
    Update the status of a conversation
    """
    conversation = await get_conversation_by_id(db, conversation_id)

    if not conversation:
        raise ValueError(f"Conversation with ID {conversation_id} not found")

    conversation.status = status

    # Update the updated_at timestamp
    import datetime
    conversation.updated_at = datetime.datetime.now(datetime.timezone.utc)

    await db.flush()

    return conversation


async def close_conversation(
    db: AsyncSession,
    conversation_id: uuid.UUID
) -> Conversation:
    """
    Close a conversation
    """
    return await update_conversation_status(db, conversation_id, "closed")


async def escalate_conversation(
    db: AsyncSession,
    conversation_id: uuid.UUID
) -> Conversation:
    """
    Mark a conversation as escalated
    """
    return await update_conversation_status(db, conversation_id, "escalated")
