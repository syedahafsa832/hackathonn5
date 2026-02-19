from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import os
import asyncio
import logging
import uuid
from typing import List, Dict, Any
from pydantic import BaseModel
from datetime import datetime, timedelta

# 1. Configure Logging First
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 2. Explicit Imports (Fixing NameErrors)
# High priority: Load models and services before app startup
try:
    from src.services.database import engine, sync_engine, Base
    from src.models.customer import Customer
    from src.models.conversation import Conversation
    from src.models.message import Message
    from src.models.ticket import Ticket
    from src.models.customer_identifier import CustomerIdentifier
    from src.models.knowledge_base import KnowledgeBase
    
    # Message processor and channels (Relocated to src)
    from src.workers.message_processor import message_processor
    from src.channels.email_poller import EmailPoller
except ImportError as e:
    logger.error(f"CRITICAL IMPORT FAIL: {e}")
    # Define placeholders to prevent crashes in the main loop
    message_processor = None
    EmailPoller = None

# 3. App Instance & IMMEDIATE Health Check
app = FastAPI(
    title="Customer Success AI Agent API",
    description="Resilient Production Backend",
    version="1.2.0",
)

@app.get("/health")
async def health_check():
    """Immediately returns status ok regardless of background errors."""
    return {"status": "ok"}

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
        
        if message_processor:
            asyncio.create_task(message_processor.process_message("web_incoming", message_data))
            return {"status": "received", "customer_email": payload.customer_email}
        else:
            raise HTTPException(status_code=503, detail="Message processor not available")
    except Exception as e:
        logger.error(f"Error receiving web message: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
async def root():
    return {"message": "Customer Success AI Agent API - Multi-Channel Active"}

# 7. Startup Event: Resilient Table Creation & Worker Startup
@app.on_event("startup")
async def startup_event():
    """
    Force create tables (blindly) and launch background workers.
    """
    # A. Resilient Table Creation
    def force_create_tables():
        try:
            logger.info("Attempting blind table creation...")
            # Import models again inside the function as a safety measure
            from src.models.customer import Customer
            from src.models.conversation import Conversation
            from src.models.message import Message
            from src.models.ticket import Ticket
            from src.models.customer_identifier import CustomerIdentifier
            from src.models.knowledge_base import KnowledgeBase
            
            Base.metadata.create_all(bind=sync_engine)
            logger.info("✓ Database schema updated (if connection was available).")
        except Exception as e:
            logger.warning(f"Database 'Force Create' skipped or failed: {e}. Moving on.")

    # Run DB init in thread to prevent blocking the event loop
    await asyncio.to_thread(force_create_tables)

    # B. Initialize background workers
    try:
        if message_processor and EmailPoller:
            logger.info("Initiating multi-channel background workers...")
            
            # Start message processor
            asyncio.create_task(message_processor.start())
            logger.info("✓ Unified Message Processor started.")
            
            # Start email poller (non-blocking)
            ep = EmailPoller(
                poll_interval=int(os.getenv("EMAIL_POLL_INTERVAL", "30")),
                processor=message_processor
            )
            asyncio.create_task(ep.start())
            logger.info("✓ Email Poller started.")
        else:
            logger.error("Cannot start workers: essential modules failing imports.")
            
    except Exception as e:
        logger.error(f"Worker Startup Error: {e}")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
