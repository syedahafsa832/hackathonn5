import hmac
import hashlib
import base64
import json
import logging
import os
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from src.lib.supabase_client import supabase_select, supabase_insert, supabase_update

logger = logging.getLogger(__name__)
router = APIRouter(tags=["webhooks"])

# Production config may name this variable either way; check both rather than
# assuming the repo's .env reflects what's actually deployed.
AFTERSHIP_WEBHOOK_SECRET = os.getenv("AFTERSHIP_WEBHOOK_SECRET") or os.getenv("AFTERSHIP_SECRET")

if not AFTERSHIP_WEBHOOK_SECRET:
    logger.warning(
        "Neither AFTERSHIP_WEBHOOK_SECRET nor AFTERSHIP_SECRET is set — "
        "AfterShip webhook signature verification is disabled; incoming webhooks will be accepted unverified."
    )

def verify_aftership_webhook(data: bytes, signature_header: str) -> bool:
    """Verify AfterShip webhook signature (HMAC-SHA256)."""
    if not AFTERSHIP_WEBHOOK_SECRET or not signature_header:
        # Secret not configured (already logged once at module load) or caller sent no signature.
        return True
    
    computed_signature = hmac.new(
        AFTERSHIP_WEBHOOK_SECRET.encode('utf-8'),
        data,
        digestmod=hashlib.sha256
    ).digest()
    
    return hmac.compare_digest(base64.b64encode(computed_signature).decode(), signature_header)

async def process_aftership_event(event: str, msg: dict):
    """
    Update local order shipping status based on AfterShip real-time updates.
    """
    try:
        tracking_number = msg.get("tracking_number")
        if not tracking_number:
            return

        tag = msg.get("tag") # AfterShip status tag: InTransit, Delivered, Exception, etc.
        checkpoint = msg.get("checkpoints", [{}])[-1] if msg.get("checkpoints") else {}
        
        # 1. Idempotency & Logging
        # AfterShip doesn't always send a stable "id". A PID-based fallback would be
        # process-scoped — wrong across restarts (fails to dedupe true replays) and
        # across multi-worker deployments (can collide for two distinct events on
        # different workers). Derive a stable key from the event content instead.
        event_id = msg.get("id")
        if not event_id:
            checkpoint_ts = checkpoint.get("checkpoint_time") or checkpoint.get("created_at") or ""
            content_hash = hashlib.sha256(
                f"{tracking_number}:{checkpoint_ts}:{tag}".encode("utf-8")
            ).hexdigest()[:16]
            event_id = f"as_{content_hash}"
        existing = supabase_select("webhook_events", {"event_id": f"eq.{event_id}"})
        if existing:
            return

        supabase_insert("webhook_events", {
            "event_id": event_id,
            "source": "aftership",
            "payload": msg
        })

        # 2. Update Order Mirror
        # Multiple orders might share a tracking number if shipped together
        orders = supabase_select("orders", {"tracking_number": f"eq.{tracking_number}"})
        if orders:
            for order in orders:
                supabase_update("orders", {"id": f"eq.{order['id']}"}, {
                    "shipping_status": tag.lower() if tag else "unknown",
                    "last_updated": "now()"
                })
            logger.info(f"✓ Updated shipment status for {len(orders)} order(s) [Tracking: {tracking_number}]")
        else:
            logger.info(f"AfterShip update for unknown tracking number: {tracking_number}")

    except Exception as e:
        logger.error(f"Error processing AfterShip webhook: {e}")

@router.post("/")
async def aftership_webhook(request: Request, background_tasks: BackgroundTasks):
    """Main endpoint for AfterShip callbacks."""
    data = await request.body()
    signature = request.headers.get('aftership-signature') or request.headers.get('x-aftership-signature')
    
    if not verify_aftership_webhook(data, signature):
        logger.warning("Invalid AfterShip webhook signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(data)
        event = payload.get("event")
        msg = payload.get("msg", {})
        
        background_tasks.add_task(process_aftership_event, event, msg)
        return {"status": "accepted"}
    except Exception as e:
        logger.error(f"Malformed AfterShip webhook payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid payload")
