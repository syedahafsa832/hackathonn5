import hmac
import hashlib
import base64
import json
import logging
import os
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from src.lib.supabase_client import supabase_select, supabase_insert

logger = logging.getLogger(__name__)
router = APIRouter(tags=["webhooks"])

SHOPIFY_WEBHOOK_SECRET = os.getenv("SHOPIFY_WEBHOOK_SECRET")

def verify_shopify_webhook(data: bytes, hmac_header: str) -> bool:
    """Verify that the webhook came from Shopify."""
    if not SHOPIFY_WEBHOOK_SECRET:
        return True # Default to True for dev if secret not set
    
    digest = hmac.new(
        SHOPIFY_WEBHOOK_SECRET.encode('utf-8'),
        data,
        digestmod=hashlib.sha256
    ).digest()
    computed_hmac = base64.b64encode(digest).decode()
    return hmac.compare_digest(computed_hmac, hmac_header)

async def process_shopify_event(topic: str, payload: dict, event_id: str):
    """Async processing of Shopify webhook events."""
    try:
        # 1. Idempotency Check
        existing = supabase_select("webhook_events", {"event_id": f"eq.{event_id}"})
        if existing:
            logger.info(f"Duplicate Shopify event {event_id}. Skipping.")
            return

        # 2. Store Event
        supabase_insert("webhook_events", {
            "event_id": event_id,
            "source": "shopify",
            "payload": payload
        })

        # 3. Handle specific topics
        if topic == "products/update":
            # Trigger sync for single product
            from src.services.shopify_sync import shopify_sync_service
            await shopify_sync_service.sync_single_product(payload, store_id="00000000-0000-0000-0000-000000000000")
        
        elif topic == "orders/create":
            # Logic to create order in our DB mirror
            pass

    except Exception as e:
        logger.error(f"Error processing Shopify webhook [{topic}]: {e}")

@router.post("/shopify")
async def shopify_webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.body()
    hmac_header = request.headers.get('X-Shopify-Hmac-Sha256')
    topic = request.headers.get('X-Shopify-Topic')
    event_id = request.headers.get('X-Shopify-Webhook-Id')

    if not verify_shopify_webhook(data, hmac_header):
        logger.warning("Invalid Shopify webhook signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(data)
    background_tasks.add_task(process_shopify_event, topic, payload, event_id)
    
    return {"status": "received"}
