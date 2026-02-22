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

# 3. Explicit Imports
try:
    from src.lib.supabase_client import supabase
    
    # Message processor and channels
    from src.workers.message_processor import message_processor
    from src.channels.email_poller import EmailPoller
    
    # WhatsApp Handler for Webhook Verification (Keep if needed)
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

@app.post("/webhooks/whatsapp")
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

# Alias for legacy or specific local tests
@app.post("/whatsapp")
async def whatsapp_webhook_legacy(request: Request):
    return await whatsapp_webhook(request)

# 5. Router Registration
from src.api.routes.support import router as support_router
from src.api.routes.tickets import router as tickets_router

app.include_router(support_router, prefix="/support", tags=["support"])
app.include_router(tickets_router) # /api/tickets is already prefixed in the router

# 6. Meta WhatsApp Webhook Endpoints
@app.get("/webhooks/whatsapp")
async def verify_whatsapp(request: Request):
    """Handle Meta dynamic verification challenge."""
    if not whatsapp_handler:
        raise HTTPException(status_code=503, detail="WhatsApp handler not available")
    return await whatsapp_handler.meta_handler.verify_webhook(request)

@app.post("/webhooks/whatsapp")
async def whatsapp_webhook(request: Request):
    """Receive incoming JSON payloads from Meta WhatsApp API."""
    if not message_processor or not whatsapp_handler:
        raise HTTPException(status_code=530, detail="Services not initialized")
    
    try:
        payload = await request.json()
        logger.info(f"Incoming WhatsApp Webhook: {payload}")
        processed = await whatsapp_handler.meta_handler.process_webhook(payload)
        
        if processed:
            # Route to message processor for Supabase insertion & AI
            asyncio.create_task(message_processor.process_message("whatsapp_incoming", processed))
            return {"status": "accepted"}
        
        return {"status": "ignored"}
    except Exception as e:
        logger.error(f"Error in WhatsApp webhook: {e}")
        return {"status": "error", "detail": str(e)}

@app.get("/status")
async def status():
    return {"message": "Customer Success AI Agent API - Supabase Mode Active"}

# 7. Startup Event: Database Initialization & Worker Startup
@app.on_event("startup")
async def startup_event():
    """Initialize background polls."""
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

# 8. Static File Hosting
static_path = "static" if os.path.exists("static") else "web-form/build"
if os.path.exists(static_path):
    logger.info(f"Mounting static files from {static_path}")
    app.mount("/", StaticFiles(directory=static_path, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
