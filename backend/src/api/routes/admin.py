from fastapi import APIRouter, HTTPException, Depends, Query, Request
from typing import Optional, List, Dict
from datetime import datetime, timezone
import uuid
import csv
import io
from fastapi.responses import StreamingResponse
import json
import logging

from src.services.supabase_service import supabase_service
from src.lib.supabase_client import supabase_update, supabase_select, supabase_insert

logger = logging.getLogger(__name__)
router = APIRouter(tags=["admin"])

# 1. AI CONTROL CENTER (/api/ai-mode)
# Using POST, PATCH, and PUT to be resilient to dashboard implementation
@router.api_route("/ai-mode", methods=["POST", "PATCH", "PUT"])
async def update_ai_mode(
    request: Request,
    mode: Optional[str] = Query(None),
    store_id: str = Query("00000000-0000-0000-0000-000000000000")
):
    """Update operational mode for the store (active, paused, manual)."""
    try:
        logger.info(f"AI Mode Update Request: {request.method}")
        
        # Try to get data from JSON body first
        payload = {}
        try:
            body = await request.body()
            if body:
                payload = await request.json()
        except Exception:
            logger.info("Empty or invalid JSON body, falling back to query parameters.")

        # Priority: JSON payload > Query Parameter > Default
        final_mode = payload.get("mode") or mode
        final_store_id = payload.get("store_id") or store_id
        
        if not final_mode or final_mode not in ["active", "paused", "manual"]:
            raise HTTPException(status_code=400, detail=f"Invalid or missing mode. Received: {final_mode}")
            
        # Robust Upsert: Try update, if it returns empty, it means row doesn't exist -> Insert
        update_result = supabase_update("system_settings", {"store_id": f"eq.{final_store_id}"}, {"ai_mode": final_mode})
        
        if not update_result:
            logger.info(f"No settings found for store {final_store_id}. Initializing new record.")
            supabase_insert("system_settings", {
                "store_id": final_store_id,
                "ai_mode": final_mode,
                "confidence_threshold": 0.75,
                "data_retention_days": 180
            })

        await supabase_service.log_audit(final_store_id, "mode_change", "admin", {"new_mode": final_mode})
        
        return {"status": "success", "mode": final_mode}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating AI mode: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/ai-mode")
async def get_ai_mode(store_id: str = Query("00000000-0000-0000-0000-000000000000")):
    """Get current AI mode for the store."""
    try:
        settings = await supabase_service.get_system_settings(store_id)
        return {"mode": settings.get("ai_mode", "active"), "store_id": store_id}
    except Exception as e:
        logger.error(f"Error fetching AI mode: {e}")
        return {"mode": "active", "store_id": store_id, "error": str(e)}

# 2. TICKET TAKEOVER SYSTEM (/api/tickets/:id/takeover)
@router.post("/tickets/{id}/takeover")
async def takeover_ticket(id: str, request: Request):
    """Prevent further AI auto-replies for a specific ticket."""
    try:
        payload = await request.json()
        user_id = payload.get("user_id", "admin")
        
        ticket = await supabase_service.get_ticket_by_id(id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
            
        store_id = ticket.get("store_id")

        supabase_insert("conversation_overrides", {
            "conversation_id": id,
            "overridden_by": user_id,
            "active": True
        })

        await supabase_service.update_ticket(id, {"status": "human_managing"})
        await supabase_service.log_audit(store_id, "takeover", user_id, {"ticket_id": id})

        return {"status": "success", "message": "Human takeover active. AI disabled for this ticket."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/tickets/{id}/release")
async def release_ticket(id: str):
    """Release human takeover and return to AI control."""
    try:
        supabase_update("conversation_overrides", 
            {"conversation_id": f"eq.{id}", "active": "eq.true"}, 
            {"active": False}
        )
        # Reset ticket status to open so AI can pick it up again
        await supabase_service.update_ticket(id, {"status": "open"})
        return {"status": "success", "message": "Ticket released back to AI control."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 2.5 SEND DRAFT (/api/tickets/:id/send-draft)
@router.post("/tickets/{id}/send-draft")
@router.post("/tickets/{id}/send-draft/")
async def send_draft(id: str, request: Request):
    """Manually approve and send an AI-generated draft."""
    try:
        logger.info(f"Send Draft Request for Ticket: {id}")
        
        # Robust body parsing
        payload = {}
        try:
            body = await request.body()
            if body:
                payload = await request.json()
        except Exception:
            logger.info("Empty or invalid JSON body in send-draft, using stored draft only.")

        body_override = payload.get("reply_body")
        
        # 1. Fetch ticket
        ticket = await supabase_service.get_ticket_by_id(id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
            
        draft_content = body_override or ticket.get("ai_draft")
        if not draft_content:
            logger.warning(f"Draft content missing for ticket {id}")
            raise HTTPException(status_code=400, detail="No draft content found to send. The AI may not have generated a response yet.")

        # 2. Send via Gmail handler
        from production.channels.gmail_handler import gmail_handler
        await gmail_handler.send_reply(
            to_email=ticket.get("customer_email"),
            subject=f"Re: {ticket.get('subject')}",
            body=draft_content
        )

        # 3. Update ticket
        await supabase_service.update_ticket(id, {
            "ai_reply": draft_content,
            "status": "auto_resolved",
            "ai_draft": None # Clear draft
        })

        return {"status": "success", "message": "Draft sent successfully."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sending draft: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# 3. GDPR DELETE (/api/gdpr/delete)
@router.delete("/gdpr/delete")
async def gdpr_delete(email: str = Query(...), store_id: str = Query("00000000-0000-0000-0000-000000000000")):
    """GDPR Right to Erasure."""
    try:
        await supabase_service.delete_customer_data(email, store_id)
        await supabase_service.log_audit(store_id, "erasure", "admin", {"target_email": email})
        return {"status": "success", "message": f"Data for {email} deleted."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 4. DATA EXPORT (/api/export)
@router.get("/export")
async def export_data(
    store_id: str = Query("00000000-0000-0000-0000-000000000000"),
    format: str = Query("json", regex="^(json|csv)$"),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """Scoped data export (JSON/CSV)."""
    try:
        params = {"store_id": f"eq.{store_id}"}
        if start_date: params["created_at"] = f"gte.{start_date}"
        if end_date: params["created_at"] = f"lte.{end_date}"
        
        tickets = supabase_select("tickets", params)
        await supabase_service.log_audit(store_id, "export", "admin", {"format": format})

        if format == "json":
            return tickets
            
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=[
            "id", "customer_email", "customer_name", "subject", "message", "ai_reply", "confidence_score", "status", "created_at"
        ])
        writer.writeheader()
        for t in tickets:
            writer.writerow({
                "id": t.get("id"),
                "customer_email": t.get("customer_email"),
                "customer_name": t.get("customer_name"),
                "subject": t.get("subject"),
                "message": t.get("message"),
                "ai_reply": t.get("ai_reply") or t.get("ai_draft"),
                "confidence_score": t.get("confidence_score"),
                "status": t.get("status"),
                "created_at": t.get("created_at")
            })
        
        output.seek(0)
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode()),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=export_{store_id}.csv"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 5. DATA RETENTION SETTINGS (/api/retention)
@router.get("/retention")
async def get_retention(store_id: str = Query("00000000-0000-0000-0000-000000000000")):
    settings = await supabase_service.get_system_settings(store_id)
    return {"data_retention_days": settings.get("data_retention_days", 180)}

@router.post("/retention")
async def update_retention(request: Request):
    try:
        payload = await request.json()
        store_id = payload.get("store_id", "00000000-0000-0000-0000-000000000000")
        days = payload.get("days", 180)
        supabase_update("system_settings", {"store_id": f"eq.{store_id}"}, {"data_retention_days": days})
        return {"status": "success", "days": days}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 6. AUDIT LOGS (/api/audit-logs)
@router.get("/audit-logs")
async def get_audit_logs(store_id: str = Query("00000000-0000-0000-0000-000000000000")):
    """Get searchable, filterable audit logs."""
    logs = supabase_select("audit_logs", {"store_id": f"eq.{store_id}", "order": "created_at.desc"})
    return logs

# 7. SYNC ORDERS FROM SHOPIFY (/api/sync-orders)
@router.post("/sync-orders")
async def sync_orders_from_shopify(store_id: str = Query("00000000-0000-0000-0000-000000000000")):
    """Trigger sync of orders from Shopify to populate customer_email, customer_name, and order_items."""
    try:
        from src.services.shopify_sync import shopify_sync_service
        import asyncio
        await shopify_sync_service.sync_all_orders(store_id)
        return {"status": "success", "message": "Orders synced from Shopify"}
    except Exception as e:
        logger.error(f"Order sync failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
