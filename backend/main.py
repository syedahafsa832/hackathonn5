from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import os
import asyncio
import logging
import uuid
from typing import List, Dict, Any
from pydantic import BaseModel
from datetime import datetime, timedelta

# 1. Configure Logging First (Bulletproof priority)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 2. IMMEDIATE Health Check (Master Blueprint priority for Railway)
# This app instance is created before imports that might fail or hang
app = FastAPI(
    title="Customer Success AI Agent API",
    description="Clean Slate Production Backend - Multi-Channel",
    version="1.1.0",
)

@app.get("/health")
async def health_check():
    """Immediately returns status ok without blocking or dependencies."""
    return {"status": "ok"}

# 3. Import logic after app and health check are defined
try:
    from src.services.database import engine, sync_engine, Base
    from src.models.customer import Customer
    from src.models.conversation import Conversation
    from src.models.message import Message
    from src.models.ticket import Ticket
    from src.models.customer_identifier import CustomerIdentifier
    from src.models.knowledge_base import KnowledgeBase
    from production.workers.message_processor import message_processor
    from production.channels.email_poller import EmailPoller
except ImportError as e:
    logger.error(f"Import error during startup: {e}")
    # Don't crash yet, let health check survive

# 4. Global Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 5. API Models
class WebMessage(BaseModel):
    customer_email: str
    customer_name: str = "Anonymous"
    content: str
    channel: str = "web_form"
    metadata: Dict[str, Any] = {}

# 6. Web Form Integration Endpoint
@app.post("/api/messages")
async def receive_web_message(payload: WebMessage):
    """
    Bridge Web Form data to the Unified Message Processor.
    Consistent with Gmail channel logic.
    """
    try:
        message_data = {
            "channel": payload.channel,
            "customer_email": payload.customer_email,
            "customer_name": payload.customer_name,
            "content": payload.content,
            "metadata": payload.metadata,
            "timestamp": datetime.now().isoformat()
        }
        
        # Route directly to Mistral Processor
        asyncio.create_task(message_processor.process_message("web_incoming", message_data))
        
        return {"status": "received", "customer_email": payload.customer_email}
    except Exception as e:
        logger.error(f"Error receiving web message: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
async def root():
    return {"message": "Customer Success AI Agent API - Clean Slate Multi-Channel"}

# 7. Threaded Database Initialization & Worker Startup
@app.on_event("startup")
async def startup_event():
    """
    Launches initialization and workers in separate tasks/threads.
    Ensures Railway health check stays green.
    """
    # A. Initializing Database in Thread (Non-blocking)
    async def db_init_task():
        try:
            logger.info(f"DEBUG: Attempting to create tables for models: {[c.__tablename__ for c in [Customer, Conversation, Message, Ticket, CustomerIdentifier]]}")
            # Use to_thread to keep the event loop free for health checks
            await asyncio.to_thread(Base.metadata.create_all, sync_engine)
            logger.info("✓ Database schema initialized in background thread.")
        except Exception as e:
            logger.error(f"Background DB Initialization FAILED: {e}")

    asyncio.create_task(db_init_task())

    # B. Initialize background workers
    try:
        logger.info("Initiating multi-channel background workers...")
        
        # Start message processor
        asyncio.create_task(message_processor.start())
        logger.info("✓ Unified Message Processor started.")
        
        # Start email poller
        email_poller = EmailPoller(
            poll_interval=int(os.getenv("EMAIL_POLL_INTERVAL", "30")),
            processor=message_processor
        )
        asyncio.create_task(email_poller.start())
        logger.info("✓ Email Poller started.")
            
    except Exception as e:
        logger.error(f"CRITICAL ERROR IN WORKER STARTUP: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
