from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.staticfiles import StaticFiles
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

# 2. IMMEDIATE Health Check
app = FastAPI(
    title="Customer Success AI Agent API",
    description="Production Multi-Channel Backend (Meta WhatsApp + Email + Web)",
    version="1.3.1",
)

@app.get("/health")
async def health_check():
    """Immediately returns status ok for Railway/Render."""
    return {"status": "ok"}

# 3. Explicit Imports (Corrected paths)
try:
    from src.services.database import engine, sync_engine, Base
    from src.models.customer import Customer
    from src.models.conversation import Conversation
    from src.models.message import Message
    from src.models.ticket import Ticket
    from src.models.customer_identifier import CustomerIdentifier
    from src.models.knowledge_base import KnowledgeBase
    
    # Message processor and channels
    from src.workers.message_processor import message_processor
    from src.channels.email_poller import EmailPoller
    
    # WhatsApp Handler for Webhook Verification
    from src.services.whatsapp_handler import WhatsAppHandler
    whatsapp_handler = WhatsAppHandler()
except ImportError as e:
    logger.error(f"CRITICAL IMPORT FAIL: {e}")
    message_processor = None
    EmailPoller = None
    whatsapp_handler = None

# 4. Global Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 5. Meta WhatsApp Webhook Endpoints
@app.get("/webhooks/whatsapp")
async def verify_whatsapp(request: Request):
    """Handle Meta dynamic verification challenge."""
    if not whatsapp_handler:
        raise HTTPException(status_code=503, detail="WhatsApp handler not available")
    return await whatsapp_handler.meta_handler.verify_webhook(request)

@app.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    """Receive incoming JSON payloads from Meta WhatsApp API."""
    if not message_processor or not whatsapp_handler:
        raise HTTPException(status_code=530, detail="Services not initialized")
    
    try:
        payload = await request.json()
        logger.info(f"Incoming WhatsApp Webhook: {payload}")
        
        # Process the meta payload into our internal format
        processed = await whatsapp_handler.meta_handler.process_webhook(payload)
        
        if processed:
            # Route to message processor
            asyncio.create_task(message_processor.process_message("whatsapp_incoming", processed))
            return {"status": "accepted"}
        
        return {"status": "ignored", "reason": "non-message event"}
    except Exception as e:
        logger.error(f"Error in WhatsApp webhook: {e}")
        return {"status": "error", "detail": str(e)}

# 6. Web Form Integration Endpoint
class WebMessage(BaseModel):
    customer_email: str
    customer_name: str = "Anonymous"
    content: str
    channel: str = "web_form"
    metadata: Dict[str, Any] = {}

@app.post("/api/messages")
async def receive_web_message(payload: WebMessage):
    """Bridge Web Form data to the Unified Message Processor."""
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

@app.get("/status")
async def status():
    return {"message": "Customer Success AI Agent API - Production Ready"}

@app.get("/debug/whatsapp")
async def debug_whatsapp():
    """Diagnostic for WhatsApp settings."""
    token = os.getenv("META_VERIFY_TOKEN")
    return {
        "meta_verify_token_configured": token is not None,
        "token_preview": f"{token[:3]}..." if token else "Not set",
        "hardcoded_fallback": "my_verify_token_12345",
        "callback_url_path": "/webhooks/whatsapp"
    }

@app.get("/debug/email")
async def debug_email():
    """Diagnostic for Email delivery."""
    resend_key = os.getenv("RESEND_API_KEY")
    return {
        "resend_active": resend_key is not None,
        "smtp_server": os.getenv("SMTP_SERVER", "smtp.gmail.com")
    }

# 7. Startup Event: Database Initialization & Worker Startup
@app.on_event("startup")
async def startup_event():
    """Ensure tables exist and start background polls."""
    # A. Force Table Creation (Wait for DB)
    def force_create():
        try:
            logger.info("Running Base.metadata.create_all(engine)...")
            Base.metadata.create_all(bind=sync_engine)
            logger.info("✓ Database tables verified/created.")
        except Exception as e:
            logger.warning(f"Table creation check failed: {e}. If the DB is spinning up, this is expected.")

    await asyncio.to_thread(force_create)

    # B. Initialize background workers
    if message_processor and EmailPoller:
        logger.info("Initiating Production Background Workers...")
        
        # Start Message Processor
        asyncio.create_task(message_processor.start())
        
        # Start Email Poller (Non-blocking)
        ep = EmailPoller(
            poll_interval=int(os.getenv("EMAIL_POLL_INTERVAL", "30")),
            processor=message_processor
        )
        asyncio.create_task(ep.start())
        logger.info("✓ Background threads active.")

# 8. Static File Hosting (at the bottom)
# Mount the static directory to server the web form from the root URL
# We check both "static" and "backend/static" just in case, but Dockerfile copies backend to /app
static_path = "static" if os.path.exists("static") else "backend/static"
if os.path.exists(static_path):
    logger.info(f"Mounting static files from {static_path}")
    app.mount("/", StaticFiles(directory=static_path, html=True), name="static")
else:
    logger.warning("Static directory not found. Web form may not be served.")

if __name__ == "__main__":
    import uvicorn
    # Respect $PORT for Railway
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
