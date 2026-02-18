from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import os
import asyncio
import logging
import uuid
from typing import List
from pydantic import BaseModel
from datetime import datetime, timedelta
from src.services.database import engine, Base
import src.models.customer
import src.models.conversation
import src.models.message
import src.models.ticket
import src.models.customer_identifier
import src.models.knowledge_base


# 1. Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 2. Create FastAPI app instance
app = FastAPI(
    title="Customer Success AI Agent API",
    description="Clean Slate Production Backend",
    version="1.0.0",
)

# 3. IMMEDIATE Health Check (Master Blueprint priority)
@app.get("/health")
async def health_check():
    """Immediately returns status ok without blocking."""
    return {"status": "ok"}

@app.get("/")
async def root():
    return {"message": "Customer Success AI Agent API - Clean Slate"}

# 4. Background Workers (Hardened Startup)
@app.on_event("startup")
async def startup_event():
    """
    Launch background workers internally within the FastAPI process.
    Pre-loads heavy models, connects to DB, and runs auto-migrations.
    """
    try:
        # 1. Database Auto-Migration (Master Blueprint priority for fresh DBs)
        try:
            logger.info("Starting database auto-migration...")
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("✓ Database schema verified/created successfully.")
        except Exception as e:
            logger.error(f"Critical error during database auto-migration: {str(e)}")
            # We continue startup as Railway/Render health checks might still pass

        # 2. Pre-load Sentence Transformers
        from sentence_transformers import SentenceTransformer
        logger.info("Loading Sentence Transformer model...")
        model = SentenceTransformer('all-MiniLM-L6-v2')
        logger.info("✓ Sentence Transformer model loaded.")

        # 3. Initialize background tasks
        from production.channels.email_poller import EmailPoller
        from production.workers.message_processor import message_processor
        
        logger.info("Initiating background workers within main process...")
        
        # Start message processor (Non-blocking)
        asyncio.create_task(message_processor.start())
        logger.info("✓ Message Processor task created.")
        
        # Start email poller (Non-blocking)
        email_poller = EmailPoller(
            poll_interval=int(os.getenv("EMAIL_POLL_INTERVAL", "30")),
            processor=message_processor
        )
        asyncio.create_task(email_poller.start())
        logger.info("✓ Email Poller task created.")

            
    except Exception as e:
        logger.error(f"CRITICAL ERROR IN STARTUP: {str(e)}")
        # Don't crash the server, allow /health to remain alive if possible

# 5. Middlewares and Routes
from src.api.middleware.cors import add_cors_middleware
from src.api.middleware.logging import setup_logging_middleware
from src.api.routes import setup_routes
from src.services.database import db_session
from src.services.message_service import get_messages_by_conversation

add_cors_middleware(app)
setup_logging_middleware(app)
setup_routes(app)

# 6. Data Models for API
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

# 7. Core Endpoints
@app.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: str):
    """Get full conversation history with cross-channel context."""
    try:
        # Validate conversation_id format
        try:
            val_uuid = uuid.UUID(conversation_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid conversation ID format")

        async with db_session() as db:
            from src.models.conversation import Conversation
            from sqlalchemy import select
            
            result = await db.execute(
                select(Conversation).where(Conversation.id == val_uuid)
            )
            conversation = result.scalar_one_or_none()

            if not conversation:
                raise HTTPException(status_code=404, detail="Conversation not found")

            messages = await get_messages_by_conversation(db, val_uuid)

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

# 8. Start script
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"Starting server on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
