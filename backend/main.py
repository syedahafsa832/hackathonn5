from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import os
import asyncio
import logging
import traceback

# 1. Configure Logging First
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 2. IMMEDIATE Health Check — must be registered before any import can fail
app = FastAPI(
    title="Customer Success AI Agent API",
    description="Production Multi-Channel Backend (Supabase + AI)",
    version="2.0.0",
)

@app.get("/health")
async def health_check():
    """Immediately returns status ok for Railway/Render."""
    return {"status": "ok"}

@app.get("/debug/order/{order_id}")
async def debug_order(order_id: str):
    """Debug endpoint to test order lookup from Shopify."""
    from src.services.tools import v3_tools
    result = await v3_tools.get_order_status(order_id)
    return result

@app.get("/status")
async def status():
    return {"message": "Customer Success AI Agent API - Supabase Mode Active"}

# 3. Global Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 4. Safe Imports — wrapped so a failing import doesn't kill the health check
message_processor = None
EmailPoller = None
whatsapp_handler = None

try:
    logger.info("Importing src.lib.supabase_client...")
    from src.lib.supabase_client import supabase
    
    logger.info("Importing src.workers.message_processor...")
    from src.workers.message_processor import message_processor
    
    logger.info("Importing src.channels.email_poller...")
    from src.channels.email_poller import EmailPoller
    
    logger.info("Importing src.services.whatsapp_handler...")
    from src.services.whatsapp_handler import WhatsAppHandler
    
    logger.info("Initializing WhatsAppHandler...")
    whatsapp_handler = WhatsAppHandler()
    
    logger.info("✓ All core imports succeeded.")
except Exception as e:
    logger.error(f"CRITICAL INITIALIZATION FAIL: {e}")
    logger.error(traceback.format_exc())

# 5. Router Registration — also wrapped for safety
try:
    logger.info("Registering API routers...")
    from src.api.routes.admin import router as api_admin_router
    from src.api.routes.support import router as support_router
    from src.api.routes.tickets import router as tickets_router
    from src.api.routes.auth import router as auth_router
    from src.api.routes.shopify_auth import router as shopify_auth_router
    from src.api.routes.webhooks.shopify import router as shopify_webhook_router
    from src.api.routes.webhooks.aftership import router as aftership_webhook_router
    from src.api.routes.returns import router as returns_router
    from src.api.routes.actions import router as actions_router
    from src.api.routes.agentic import router as agentic_router
    
    # Priority routers first
    app.include_router(shopify_auth_router) # Support /install and /new-install at root
    app.include_router(api_admin_router, prefix="/api")
    app.include_router(support_router, prefix="/support", tags=["support"])
    app.include_router(tickets_router, prefix="/api")
    app.include_router(auth_router)
    app.include_router(shopify_webhook_router, prefix="/api/webhooks")
    app.include_router(aftership_webhook_router, prefix="/api/webhooks/aftership")
    app.include_router(returns_router, prefix="/api")
    app.include_router(actions_router, prefix="/api")
    app.include_router(agentic_router, prefix="/api")

    # AI-Mode endpoint for Decision Hub - returns pending tickets directly
    @app.get("/api/ai-mode")
    async def ai_mode_endpoint():
        """Decision Hub endpoint - returns pending tickets from Supabase"""
        try:
            from src.lib.supabase_client import supabase_select

            # Get pending tickets from database
            tickets = supabase_select("tickets", {"status": "eq.pending"})

            if not tickets or len(tickets) == 0:
                return {
                    "tickets": [],
                    "count": 0,
                    "source": "database"
                }

            # Transform database tickets to frontend format
            transformed_tickets = []
            for ticket in tickets:
                # Get customer info if available
                customer_id = ticket.get("customer_id")
                customer_name = "Unknown Customer"
                customer_email = ""
                ltv = 0

                if customer_id:
                    customers = supabase_select("customers", {"id": f"eq.{customer_id}"})
                    if customers:
                        customer_name = customers[0].get("name", "Unknown")
                        customer_email = customers[0].get("email", "")
                        ltv = customers[0].get("ltv", 0) or 0

                transformed_tickets.append({
                    "id": ticket.get("id", ""),
                    "ticket_id": ticket.get("id", ""),
                    "customer_name": customer_name,
                    "customer_email": customer_email,
                    "order_id": ticket.get("order_id", ""),
                    "sentiment": ticket.get("sentiment", "neutral"),
                    "sentiment_score": ticket.get("sentiment_score", 5),
                    "status": ticket.get("status", "pending"),
                    "vip_status": "Regular",
                    "ltv": ltv,
                    "intent": ticket.get("intent", "other"),
                    "requested_item": ticket.get("requested_item"),
                    "message_content": ticket.get("description", ""),
                    "content": ticket.get("description", ""),
                    "channel": ticket.get("source_channel", "email"),
                    "created_at": ticket.get("created_at", ""),
                    "createdAt": ticket.get("created_at", ""),
                    "ai_reasoning": ticket.get("ai_reasoning", "No AI analysis available"),
                    "revenue_at_stake": ticket.get("revenue_at_stake", 0),
                })

            return {
                "tickets": transformed_tickets,
                "count": len(transformed_tickets),
                "source": "database"
            }

        except Exception as e:
            logger.error(f"Error in /api/ai-mode: {e}")
            return {
                "tickets": [],
                "count": 0,
                "error": str(e)
            }

    logger.info("✓ Routers registered (shopify_auth, api, support, tickets, auth).")
except Exception as e:
    logger.error(f"Failed to register routers: {e}")
    logger.error(traceback.format_exc())

# 6. WhatsApp Webhook Endpoints
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
        raise HTTPException(status_code=503, detail="Services not initialized")
    try:
        payload = await request.json()
        logger.info(f"Incoming WhatsApp Webhook: {payload}")
        processed = await whatsapp_handler.meta_handler.process_webhook(payload)
        if processed:
            asyncio.create_task(message_processor.process_message("whatsapp_incoming", processed))
            return {"status": "accepted"}
        return {"status": "ignored"}
    except Exception as e:
        logger.error(f"Error in WhatsApp webhook: {e}")
        return {"status": "error", "detail": str(e)}

@app.post("/api/messages")
async def simple_message_submit(request: Request):
    """Legacy support for the simple static index.html webform."""
    if not message_processor:
        raise HTTPException(status_code=503, detail="Services not initialized")
    try:
        payload = await request.json()
        # payload structure in static/index.html: { customer_name, customer_email, content }
        await message_processor.process_message("web_form_simple", {
            "channel": "web_form",
            "customer_email": payload.get("customer_email"),
            "customer_name": payload.get("customer_name"),
            "content": payload.get("content"),
            "subject": "Web Support Message"
        })
        return {"status": "received"}
    except Exception as e:
        logger.error(f"Error in simple message submit: {e}")
        return {"status": "error", "message": str(e)}

# 7. Startup Event
@app.on_event("startup")
async def startup_event():
    """Initialize background polls."""
    if message_processor and EmailPoller:
        logger.info("Initiating Production Background Workers...")
        asyncio.create_task(message_processor.start())
        ep = EmailPoller(
            poll_interval=int(os.getenv("EMAIL_POLL_INTERVAL", "30")),
            processor=message_processor
        )
        asyncio.create_task(ep.start())
        logger.info("✓ Background threads active.")

# 8. Test Routes for Debugging
@app.get("/auth/test")
async def auth_test():
    return {"status": "Direct /auth/test works"}

# 9. Static File Hosting (must be last — catch-all mount)
static_path = "static" if os.path.exists("static") else "web-form/build"
if os.path.exists(static_path):
    logger.info(f"Mounting static files from {static_path} at /web")
    app.mount("/web", StaticFiles(directory=static_path, html=True), name="static")
    
    from fastapi.responses import RedirectResponse
    @app.get("/")
    async def root_redirect():
        return RedirectResponse(url="/web/")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
