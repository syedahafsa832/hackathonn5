from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from .middleware.cors import add_cors_middleware
from .middleware.logging import setup_logging_middleware
from .routes import setup_routes
from sqlalchemy.ext.asyncio import AsyncSession
from src.services.database import get_db, db_session
from src.services.conversation_service import get_conversation_by_id, get_conversations_by_customer
from src.services.message_service import get_messages_by_conversation
from src.services.customer_service import get_customer_by_id
from src.models.conversation import Conversation
from src.models.message import Message
from src.models.customer import Customer
import logging
import uuid
from typing import List
from pydantic import BaseModel
from datetime import datetime, timedelta

# Configure basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastAPI app instance
app = FastAPI(
    title="Customer Success AI Agent API",
    description="API for the multi-channel customer success agent system",
    version="1.0.0",
)

# Add middleware
add_cors_middleware(app)
setup_logging_middleware(app)

# Setup API routes
setup_routes(app)

@app.on_event("startup")
async def startup_event():
    """
    Launch background workers internally within the FastAPI process.
    This allows the system to operate as a monolith on Render Web Services.
    """
    import asyncio
    import os
    try:
        from production.channels.email_poller import EmailPoller
        from production.workers.message_processor import UnifiedMessageProcessor
        
        logger.info("Initiating background workers within main process...")
        
        # Initialize and start email poller
        email_poller = EmailPoller(poll_interval=int(os.getenv("EMAIL_POLL_INTERVAL", "30")))
        asyncio.create_task(email_poller.start())
        
        # Initialize and start message processor
        message_processor = UnifiedMessageProcessor()
        asyncio.create_task(message_processor.start())
        
        logger.info("Internal background tasks (Email Poller, Message Processor) started successfully.")
    except ImportError as e:
        logger.error(f"Failed to import background workers: {e}. Ensure PYTHONPATH includes the project root.")
    except Exception as e:
        logger.error(f"Error starting internal background workers: {e}")

@app.get("/")
async def root():
    return {"message": "Customer Success AI Agent API"}

@app.get("/health")
async def health_check():
    return {"status": "ok"}

class MessageInfo(BaseModel):
    id: str
    channel: str
    direction: str
    content_preview: str
    created_at: str
    sentiment_score: float = None

class ConversationDetail(BaseModel):
    id: str
    initial_channel: str
    status: str
    created_at: str
    updated_at: str
    messages: List[MessageInfo]

@app.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: str):
    """Get full conversation history with cross-channel context."""
    try:
        # Validate conversation_id format
        try:
            uuid.UUID(conversation_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid conversation ID format")

        async with db_session() as db:
            # Get conversation details
            result = await db.execute(
                Conversation.__table__.select().where(Conversation.id == uuid.UUID(conversation_id))
            )
            conversation = result.first()

            if not conversation:
                raise HTTPException(status_code=404, detail="Conversation not found")

            # Get messages for this conversation
            messages = await get_messages_by_conversation(db, uuid.UUID(conversation_id))

            # Format messages
            message_list = []
            for msg in messages:
                message_list.append(MessageInfo(
                    id=str(msg.id),
                    channel=msg.channel,
                    direction=msg.direction,
                    content_preview=msg.content[:100] + "..." if len(msg.content) > 100 else msg.content,
                    created_at=msg.created_at.isoformat() if msg.created_at else "",
                    sentiment_score=float(msg.sentiment_score) if msg.sentiment_score else None
                ))

            return ConversationDetail(
                id=str(conversation.id),
                initial_channel=conversation.initial_channel,
                status=conversation.status,
                created_at=conversation.created_at.isoformat() if conversation.created_at else "",
                updated_at=conversation.updated_at.isoformat() if conversation.updated_at else "",
                messages=message_list
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting conversation {conversation_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

class ChannelMetrics(BaseModel):
    channel: str
    total_conversations: int
    total_messages: int
    avg_sentiment: float
    escalation_count: int
    avg_response_time: float
    resolution_rate: float

class MetricsResponse(BaseModel):
    timestamp: str
    metrics: List[ChannelMetrics]

@app.get("/metrics/channels", response_model=MetricsResponse)
async def get_channel_metrics():
    """Get performance metrics by channel."""
    try:
        from sqlalchemy import func, select
        async with db_session() as db:
            # Query database for channel metrics:
            # - Total conversations per channel (last 24h)
            # - Average sentiment per channel
            # - Escalation count per channel
            # - Average response time per channel

            # Get conversation counts by channel
            conv_result = await db.execute(
                select(
                    Conversation.initial_channel,
                    func.count(Conversation.id).label('count')
                ).where(
                    Conversation.created_at > datetime.utcnow() - timedelta(hours=24)
                ).group_by(Conversation.initial_channel)
            )
            conv_counts = {row.initial_channel: row.count for row in conv_result.all()}

            # Get message counts and average sentiment by channel
            msg_result = await db.execute(
                select(
                    Message.channel,
                    func.count(Message.id).label('count'),
                    func.avg(Message.sentiment_score).label('avg_sentiment')
                ).group_by(Message.channel)
            )
            msg_stats = {}
            for row in msg_result.all():
                msg_stats[row.channel] = {
                    'count': row.count,
                    'avg_sentiment': float(row.avg_sentiment) if row.avg_sentiment else 0.0
                }

            # Create metrics for each channel
            channels = ['whatsapp', 'web_form']
            metrics = []

            for channel in channels:
                conv_count = conv_counts.get(channel, 0)
                msg_count = msg_stats.get(channel, {}).get('count', 0)
                avg_sentiment = msg_stats.get(channel, {}).get('avg_sentiment', 0.0)

                # Calculate additional metrics
                escalation_count = 0  # This would come from escalation logs in a real implementation
                avg_response_time = 0.0  # This would come from response time logs
                resolution_rate = 0.0  # This would come from ticket resolution data

                metrics.append(ChannelMetrics(
                    channel=channel,
                    total_conversations=conv_count,
                    total_messages=msg_count,
                    avg_sentiment=avg_sentiment,
                    escalation_count=escalation_count,
                    avg_response_time=avg_response_time,
                    resolution_rate=resolution_rate
                ))

            return MetricsResponse(
                timestamp=datetime.utcnow().isoformat(),
                metrics=metrics
            )

    except Exception as e:
        logger.error(f"Error getting channel metrics: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

logger.info("FastAPI application initialized successfully")
