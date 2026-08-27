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
    """Verify that the webhook came from Shopify.

    Fails closed: with no SHOPIFY_WEBHOOK_SECRET configured, every request
    is rejected rather than accepted. The previous "accept everything when
    unset" default meant anyone could POST an unsigned payload to this
    endpoint and have it processed as a genuine Shopify event whenever the
    secret wasn't set - not just in dev, in any deployment that forgot to
    set it. No legitimate flow currently depends on the old permissive
    default (there is no code that auto-registers Shopify webhooks against
    this endpoint), so there is nothing working today that this could break.
    """
    if not SHOPIFY_WEBHOOK_SECRET:
        logger.error("SHOPIFY_WEBHOOK_SECRET is not set — rejecting all Shopify webhook requests")
        return False

    if not hmac_header:
        return False

    digest = hmac.new(
        SHOPIFY_WEBHOOK_SECRET.encode('utf-8'),
        data,
        digestmod=hashlib.sha256
    ).digest()
    computed_hmac = base64.b64encode(digest).decode()
    return hmac.compare_digest(computed_hmac, hmac_header)

def _resolve_store_id_for_shop(shop_domain: str) -> "str | None":
    """Map a webhook's shop domain to the one brand that owns it.

    Every previous handler in this file hardcoded store_id to the all-zero
    placeholder UUID regardless of which shop actually sent the webhook -
    every tenant's product-update events were silently attributed to the
    same fake store. Shopify always sends X-Shopify-Shop-Domain, and every
    brand's own real domain is already stored (shopify_auth.py's OAuth
    callback sets it on connect), so this is the same shop_domain match
    tickets.py/brands.py already use elsewhere - never guessed from the
    payload body, always from the header Shopify itself sets.
    """
    if not shop_domain:
        return None
    brands = supabase_select("brands", {"shopify_domain": f"eq.{shop_domain}"})
    if not brands:
        return None
    return brands[0]["id"]


async def process_shopify_event(topic: str, payload: dict, event_id: str, shop_domain: str):
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

        # 3. Handle specific topics — every topic that writes tenant-owned
        # data must resolve its own store_id from the shop domain first.
        # Fail closed (skip, don't process) when no matching brand is
        # found, rather than falling back to a shared placeholder that
        # would misattribute this shop's data to every other tenant using
        # that same placeholder.
        if topic == "products/update":
            store_id = _resolve_store_id_for_shop(shop_domain)
            if not store_id:
                logger.warning(f"[ShopifyWebhook] No brand found for shop domain '{shop_domain}' — skipping products/update")
                return
            from src.services.shopify_sync import shopify_sync_service
            await shopify_sync_service.sync_single_product(payload, store_id=store_id)

        elif topic == "orders/create":
            # Not yet implemented — intentionally a no-op, not a bug this
            # audit needs to fix (nothing here writes or misattributes
            # data since it does nothing at all).
            pass

    except Exception as e:
        logger.error(f"Error processing Shopify webhook [{topic}]: {e}")

@router.post("/shopify")
async def shopify_webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.body()
    hmac_header = request.headers.get('X-Shopify-Hmac-Sha256')
    topic = request.headers.get('X-Shopify-Topic')
    event_id = request.headers.get('X-Shopify-Webhook-Id')
    shop_domain = request.headers.get('X-Shopify-Shop-Domain', '')

    if not verify_shopify_webhook(data, hmac_header):
        logger.warning("Invalid Shopify webhook signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(data)
    background_tasks.add_task(process_shopify_event, topic, payload, event_id, shop_domain)

    return {"status": "received"}
