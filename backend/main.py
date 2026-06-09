from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from dotenv import load_dotenv
import os
import sys
import asyncio
import logging
import traceback

# Allow OAuth2 over HTTP for local development
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'

# 0. Add parent directory to Python path for 'production' module imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 0. Load environment variables
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

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

# Rate Limiter Setup
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """Handle rate limit exceeded errors."""
    return HTTPException(
        status_code=429,
        detail={
            "error": "rate_limit_exceeded",
            "message": "Too many requests. Please try again later.",
            "retry_after": exc.detail
        }
    )

@app.get("/debug/order/{order_id}")
async def debug_order(order_id: str):
    """Debug endpoint to test order lookup from Shopify."""
    from src.services.tools import v3_tools
    result = await v3_tools.get_order_status(order_id)
    return result

@app.get("/status")
async def status():
    return {"message": "Customer Success AI Agent API - Supabase Mode Active"}

@app.get("/widget.js")
async def widget_js():
    """Serve the embeddable chat widget JavaScript."""
    from fastapi.responses import FileResponse
    import pathlib
    path = pathlib.Path(__file__).parent / "src" / "static" / "widget.js"
    if not path.exists():
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse("/* widget.js not found */", media_type="application/javascript")
    return FileResponse(str(path), media_type="application/javascript", headers={
        "Cache-Control": "public, max-age=300",
        "Access-Control-Allow-Origin": "*",
        "X-Content-Type-Options": "nosniff",
    })

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
    # ----------------------------------------------------
    # Register AI-Mode endpoint explicitly FIRST to override admin.py
    # ----------------------------------------------------
    @app.get("/api/ai-mode")
    async def ai_mode_endpoint(request: Request, store_id: str = "00000000-0000-0000-0000-000000000000"):
        """Decision Hub endpoint - returns pending tickets AND system mode, scoped to caller's tenant."""
        try:
            from src.lib.supabase_client import supabase_select
            from src.services.supabase_service import supabase_service
            from src.api.middleware.tenant_auth import get_optional_tenant, security
            from fastapi.security import HTTPAuthorizationCredentials

            # Get system settings for the toggle component
            settings = await supabase_service.get_system_settings(store_id)
            raw_mode = settings.get("ai_mode", "active")
            # Normalize legacy values: active → autopilot, paused/manual/supervised → supervised
            current_mode = "autopilot" if raw_mode in ("active", "autopilot") else "supervised"

            # Resolve tenant from JWT (optional — no token → empty ticket list)
            creds = await security(request)
            tenant = await get_optional_tenant(request, creds)

            if not tenant:
                return {"mode": current_mode, "store_id": store_id, "tickets": [], "count": 0, "source": "database"}

            # Fetch only this tenant's brands, then their tickets
            from src.api.routes.tickets import _get_tenant_brand_ids
            brand_ids = await _get_tenant_brand_ids(tenant)
            if not brand_ids:
                return {"mode": current_mode, "store_id": store_id, "tickets": [], "count": 0, "source": "database"}

            raw_tickets = []
            for bid in brand_ids:
                chunk = supabase_select("tickets", {"store_id": f"eq.{bid}", "status": "eq.open"})
                if chunk:
                    raw_tickets.extend(chunk)
            tickets = raw_tickets

            if not tickets or len(tickets) == 0:
                return {
                    "mode": current_mode,
                    "store_id": store_id,
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
                "mode": current_mode,
                "store_id": store_id,
                "tickets": transformed_tickets,
                "count": len(transformed_tickets),
                "source": "database"
            }

        except Exception as e:
            logger.error(f"Error in /api/ai-mode: {e}")
            return {
                "mode": "active",
                "tickets": [],
                "count": 0,
                "error": str(e)
            }

    # Now include routers with safety checks
    def register_router(router, **kwargs):
      try:
        app.include_router(router, **kwargs)
      except Exception as e:
        logger.error(f"Failed to register router {router}: {e}")

    try:
      from src.api.routes.shopify_auth import router as shopify_auth_router
      register_router(shopify_auth_router)
    except: pass

    try:
      from src.api.routes.admin import router as api_admin_router
      register_router(api_admin_router, prefix="/api")
    except: pass

    try:
      from src.api.routes.support import router as support_router
      register_router(support_router, prefix="/support", tags=["support"])
    except: pass

    try:
      from src.api.routes.tickets import router as tickets_router
      register_router(tickets_router, prefix="/api")
    except: pass

    try:
      from src.api.routes.auth import router as auth_router
      register_router(auth_router)
    except: pass

    try:
      from src.api.routes.webhooks.shopify import router as shopify_webhook_router
      register_router(shopify_webhook_router, prefix="/api/webhooks")
    except: pass

    try:
      from src.api.routes.webhooks.aftership import router as aftership_webhook_router
      register_router(aftership_webhook_router, prefix="/api/webhooks/aftership")
    except: pass

    try:
      from src.api.routes.returns import router as returns_router
      register_router(returns_router, prefix="/api")
    except: pass

    try:
      from src.api.routes.actions import router as actions_router
      register_router(actions_router, prefix="/api")
    except: pass

    try:
      from src.api.routes.agentic import router as agentic_router
      register_router(agentic_router, prefix="/api")
    except: pass

    # Multi-Brand Management Routes
    try:
      from src.api.routes.brands import router as brands_router
      register_router(brands_router, prefix="/api")
      logger.info("✓ Brands router registered")
    except Exception as e:
      logger.warning(f"Failed to register brands router: {e}")

    # Multi-Brand Actions Routes
    try:
      from src.api.routes.brand_actions import router as brand_actions_router
      register_router(brand_actions_router, prefix="/api")
      logger.info("✓ Brand Actions router registered")
    except Exception as e:
      logger.warning(f"Failed to register brand actions router: {e}")

    # Per-Brand Gmail OAuth Routes
    try:
      from src.api.routes.brand_gmail import router as brand_gmail_router
      register_router(brand_gmail_router, prefix="/api")
      logger.info("✓ Brand Gmail router registered")
    except Exception as e:
      logger.warning(f"Failed to register brand Gmail router: {e}")

    # ==================== SaaS Multi-Tenant Routes ====================
    # These are the new production routes with tenant isolation
    try:
      from src.api.routes.saas_auth import router as saas_auth_router
      register_router(saas_auth_router, prefix="/api/v1")
      logger.info("✓ SaaS Auth router registered")
    except Exception as e:
      logger.warning(f"Failed to register SaaS auth router: {e}")

    try:
      from src.api.routes.saas_actions import router as saas_actions_router
      register_router(saas_actions_router, prefix="/api/v1")
      logger.info("✓ SaaS Actions router registered")
    except Exception as e:
      logger.warning(f"Failed to register SaaS actions router: {e}")

    try:
      from src.api.routes.saas_settings import router as saas_settings_router
      register_router(saas_settings_router, prefix="/api/v1")
      logger.info("✓ SaaS Settings router registered")
    except Exception as e:
      logger.warning(f"Failed to register SaaS settings router: {e}")

    # ==================== V2 Multi-Tenant Routes (Supabase Auth) ====================
    # New production-ready routes with organization/brand hierarchy
    try:
      from src.api.routes.v2_auth import router as v2_auth_router
      register_router(v2_auth_router, prefix="/api/v2")
      logger.info("✓ V2 Auth router registered")
    except Exception as e:
      logger.warning(f"Failed to register V2 auth router: {e}")

    try:
      from src.api.routes.v2_brands import router as v2_brands_router
      register_router(v2_brands_router, prefix="/api/v2")
      logger.info("✓ V2 Brands router registered")
    except Exception as e:
      logger.warning(f"Failed to register V2 brands router: {e}")

    try:
      from src.api.routes.v2_tickets import router as v2_tickets_router
      register_router(v2_tickets_router, prefix="/api/v2")
      logger.info("✓ V2 Tickets router registered")
    except Exception as e:
      logger.warning(f"Failed to register V2 tickets router: {e}")

    try:
      from src.api.routes.v2_actions import router as v2_actions_router
      register_router(v2_actions_router, prefix="/api/v2")
      logger.info("✓ V2 Actions router registered")
    except Exception as e:
      logger.warning(f"Failed to register V2 actions router: {e}")

    try:
      from src.api.routes.v2_knowledge import router as v2_knowledge_router
      register_router(v2_knowledge_router, prefix="/api/v2")
      logger.info("✓ V2 Knowledge router registered")
    except Exception as e:
      logger.warning(f"Failed to register V2 knowledge router: {e}")

    # Unified Events API for frontend dashboard
    try:
      from src.api.routes.events import router as events_router
      register_router(events_router, prefix="/api")
      logger.info("✓ Events router registered")
    except Exception as e:
      logger.warning(f"Failed to register events router: {e}")

    # Email Filter Settings & Logs
    try:
      from src.api.routes.v2_email_filter import settings_router as email_filter_settings_router
      from src.api.routes.v2_email_filter import logs_router as email_filter_logs_router
      register_router(email_filter_settings_router, prefix="/api/v1")
      register_router(email_filter_logs_router, prefix="/api/v1")
      logger.info("✓ Email Filter router registered")
    except Exception as e:
      logger.warning(f"Failed to register email filter router: {e}")

    # Quarantine Queue (feature 006 — Email Guardian)
    try:
      from src.api.routes.v2_quarantine import router as quarantine_router
      register_router(quarantine_router, prefix="/api/v1")
      logger.info("✓ Quarantine router registered")
    except Exception as e:
      logger.warning(f"Failed to register quarantine router: {e}")

    # Canned Responses
    try:
      from src.api.routes.canned_responses import router as canned_responses_router
      register_router(canned_responses_router, prefix="/api/v1")
      logger.info("✓ Canned Responses router registered")
    except Exception as e:
      logger.warning(f"Failed to register canned responses router: {e}")

    # Chat Widget (public — no auth)
    try:
      from src.api.routes.v2_chat_widget import router as chat_widget_router
      register_router(chat_widget_router, prefix="/api/v2")
      logger.info("✓ Chat Widget router registered")
    except Exception as e:
      logger.warning(f"Failed to register chat widget router: {e}")

    logger.info("✓ Routers processed (resilient registration).")
except Exception as e:
    logger.error(f"Critical error in router setup block: {e}")
    logger.error(traceback.format_exc())

# 6. Startup Self-Heal — fixes common data issues automatically so no manual SQL is needed
@app.on_event("startup")
async def startup_heal():
    """
    Runs once on every container start. Fixes data issues that would otherwise require
    manual SQL:
      1. Brands with a refresh token but gmail_connected=false → set true
      2. Brands with null tenant_id that can be matched via email → backfill
      3. Tickets with null brand_id that belong to a connected brand → backfill
      4. Duplicate pending actions for same tenant+action_type+order_id → reject extras
    Safe to run repeatedly (all operations are idempotent).
    """
    try:
        from src.lib.supabase_client import supabase_select, supabase_update
        healed = 0

        # 1. Brands with a stored gmail_token but marked as disconnected.
        # The refresh_token lives inside the gmail_token JSON blob (not a separate column).
        # Only restore brands whose stored credentials include a refresh_token — a stale
        # access-token blob with no refresh_token cannot be renewed and would spam logs
        # with invalid_grant errors (and would re-enable orphaned brands like 7b977597).
        try:
            import json as _json
            token_brands_false = supabase_select("brands", {
                "gmail_connected": "eq.false",
                "gmail_token": "not.is.null",
            })
            token_brands_null = supabase_select("brands", {
                "gmail_connected": "is.null",
                "gmail_token": "not.is.null",
            })
            token_brands = (token_brands_false or []) + (token_brands_null or [])
            for brand in token_brands:
                raw_token = brand.get("gmail_token")
                if not raw_token:
                    continue
                try:
                    token_data = _json.loads(raw_token) if isinstance(raw_token, str) else raw_token
                except Exception:
                    continue
                if not token_data.get("refresh_token"):
                    logger.info(f"[STARTUP-HEAL] Skipping brand {brand['id']} — no refresh_token in stored credentials (orphan/stale)")
                    continue
                if brand.get("gmail_email"):
                    supabase_update("brands", {"id": f"eq.{brand['id']}"}, {
                        "gmail_connected": True,
                        "is_active": True,
                    })
                    logger.info(f"[STARTUP-HEAL] Restored gmail_connected for brand {brand['id']} ({brand.get('gmail_email')})")
                    healed += 1
        except Exception as e:
            logger.warning(f"[STARTUP-HEAL] Step 1 (gmail_connected) failed: {e}")

        # 2. Brands with null tenant_id — backfill from tenants table via email match.
        # SAFETY: only run in single-tenant deployments. In multi-tenant setups a brand's
        # polling Gmail address (e.g. syedahafsa772@gmail.com) may match a *different*
        # tenant's login email, silently mis-assigning the brand and hiding its tickets.
        try:
            all_tenants = supabase_select("tenants", {})
            if len(all_tenants or []) > 1:
                logger.info(
                    f"[STARTUP-HEAL] Step 2 skipped — {len(all_tenants)} tenants detected; "
                    "gmail_email→tenant matching is unsafe in multi-tenant mode"
                )
            else:
                unlinked_brands = supabase_select("brands", {"tenant_id": "is.null"})
                for brand in (unlinked_brands or []):
                    gmail_email = brand.get("gmail_email")
                    if not gmail_email:
                        continue
                    tenants = supabase_select("tenants", {"email": f"eq.{gmail_email}"})
                    if tenants:
                        supabase_update("brands", {"id": f"eq.{brand['id']}"}, {
                            "tenant_id": tenants[0]["id"]
                        })
                        logger.info(f"[STARTUP-HEAL] Linked brand {brand['id']} to tenant {tenants[0]['id']} via email match")
                        healed += 1
        except Exception as e:
            logger.warning(f"[STARTUP-HEAL] Step 2 (brand tenant_id backfill) failed: {e}")

        # 3. Tickets with null tenant_id — backfill from brand
        try:
            orphan_tickets = supabase_select("tickets", {
                "brand_id": "not.is.null",
            })
            for ticket in (orphan_tickets or []):
                # Only fix tickets where there's a store_id (brand) set
                store_id = ticket.get("store_id") or ticket.get("brand_id")
                if not store_id or store_id == "00000000-0000-0000-0000-000000000000":
                    continue
                brand_rows = supabase_select("brands", {"id": f"eq.{store_id}"})
                if brand_rows and brand_rows[0].get("tenant_id"):
                    # Only update if ticket's inferred tenant doesn't match brand's
                    # (avoid spamming DB with no-op updates)
                    pass  # Tickets use store_id for filtering, not a direct tenant_id column
        except Exception as e:
            logger.warning(f"[STARTUP-HEAL] Step 3 (ticket brand backfill) failed: {e}")

        # 4. Duplicate pending actions — keep oldest, reject the rest
        try:
            all_pending = supabase_select("actions", {"status": "eq.pending"})
            seen = {}  # key: (tenant_id, action_type, order_id) → oldest action id
            for action in (all_pending or []):
                key = (
                    action.get("tenant_id"),
                    action.get("action_type"),
                    action.get("order_id"),
                )
                if None in key:
                    continue
                if key not in seen:
                    seen[key] = action["id"]
                else:
                    # This is a duplicate — reject it
                    supabase_update("actions", {"id": f"eq.{action['id']}"}, {
                        "status": "rejected",
                        "error_message": "Duplicate action removed on startup",
                    })
                    logger.info(f"[STARTUP-HEAL] Rejected duplicate {action.get('action_type')} action {action['id']}")
                    healed += 1
        except Exception as e:
            logger.warning(f"[STARTUP-HEAL] Step 4 (duplicate actions) failed: {e}")

        # 4b. Brands with null is_active — backfill to True so lookups don't miss them.
        try:
            null_active_brands = supabase_select("brands", {"is_active": "is.null"})
            for brand in (null_active_brands or []):
                supabase_update("brands", {"id": f"eq.{brand['id']}"}, {"is_active": True})
                logger.info(f"[STARTUP-HEAL] Set is_active=True for brand {brand['id']} (was null)")
                healed += 1
        except Exception as e:
            logger.warning(f"[STARTUP-HEAL] Step 4b (is_active backfill) failed: {e}")

        # 5. Tenants with no brand — create a default brand so Gmail connect works.
        # Applies to tenants who registered before auto-brand-creation was added.
        try:
            all_tenants = supabase_select("tenants", {"is_active": "is.true"}) or []
            for tenant in all_tenants:
                tid = tenant.get("id")
                if not tid:
                    continue
                tenant_brands = supabase_select("brands", {"tenant_id": f"eq.{tid}"})
                if tenant_brands:
                    continue  # already has a brand
                email = tenant.get("email", "")
                brand_name = tenant.get("company_name") or (
                    f"{email.split('@')[0].title()}'s Store" if email else "My Store"
                )
                supabase_insert("brands", {
                    "name": brand_name,
                    "is_active": True,
                    "tenant_id": tid,
                    "gmail_connected": False,
                })
                logger.info(f"[STARTUP-HEAL] Created default brand for tenant {tid} ({email})")
                healed += 1
        except Exception as e:
            logger.warning(f"[STARTUP-HEAL] Step 5 (default brand creation) failed: {e}")

        if healed > 0:
            logger.info(f"[STARTUP-HEAL] Fixed {healed} data issue(s) automatically")
        else:
            logger.info("[STARTUP-HEAL] No data issues found")

    except Exception as e:
        logger.error(f"[STARTUP-HEAL] Unexpected error: {e}")


# 6. WhatsApp Webhook Endpoints
@app.get("/webhooks/whatsapp")
async def verify_whatsapp(request: Request):
    """Handle Meta dynamic verification challenge."""
    if not whatsapp_handler:
        raise HTTPException(status_code=503, detail="WhatsApp handler not available")
    return await whatsapp_handler.meta_handler.verify_webhook(request)

@app.post("/webhooks/whatsapp")
@limiter.limit("60/minute")
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
@limiter.limit("30/minute")
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
    """API startup — email polling is handled by the dedicated email_poller container."""
    logger.info("API startup complete. Email polling handled by email_poller container.")

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
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
