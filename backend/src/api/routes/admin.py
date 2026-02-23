from fastapi import APIRouter, HTTPException, Depends, Query, Request
from typing import Optional, List, Dict
from datetime import datetime, timezone
import uuid
import csv
import io
from fastapi.responses import StreamingResponse
import json

from src.services.supabase_service import supabase_service
from src.lib.supabase_client import supabase_update, supabase_select, supabase_insert

router = APIRouter(tags=["admin"])

# 1. AI CONTROL CENTER (/api/ai-mode)
@router.post("/ai-mode/")
@router.post("/ai-mode")
async def update_ai_mode(request: Request):
    """Update operational mode for the store (active, paused, manual)."""
    try:
        payload = await request.json()
        store_id = payload.get("store_id", "00000000-0000-0000-0000-000000000000")
        mode = payload.get("mode") # active, paused, manual
        
        if mode not in ["active", "paused", "manual"]:
            raise HTTPException(status_code=400, detail="Invalid mode")
            
        supabase_update("system_settings", {"store_id": f"eq.{store_id}"}, {"ai_mode": mode})
        await supabase_service.log_audit(store_id, "mode_change", "admin", {"new_mode": mode})
        
        return {"status": "success", "mode": mode}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/ai-mode/")
@router.get("/ai-mode")
async def get_ai_mode(store_id: str = Query("00000000-0000-0000-0000-000000000000")):
    """Get current AI mode for the store."""
    settings = await supabase_service.get_system_settings(store_id)
    return {"mode": settings.get("ai_mode", "active"), "store_id": store_id}

# 2. TICKET TAKEOVER SYSTEM (/api/tickets/:id/takeover)
@router.post("/tickets/{id}/takeover/")
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

@router.post("/tickets/{id}/release/")
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

# 3. GDPR DELETE (/api/gdpr/delete)
@router.delete("/gdpr/delete/")
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
@router.get("/export/")
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
@router.get("/retention/")
@router.get("/retention")
async def get_retention(store_id: str = Query("00000000-0000-0000-0000-000000000000")):
    settings = await supabase_service.get_system_settings(store_id)
    return {"data_retention_days": settings.get("data_retention_days", 180)}

@router.post("/retention/")
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
@router.get("/audit-logs/")
@router.get("/audit-logs")
async def get_audit_logs(store_id: str = Query("00000000-0000-0000-0000-000000000000")):
    """Get searchable, filterable audit logs."""
    logs = supabase_select("audit_logs", {"store_id": f"eq.{store_id}", "order": "created_at.desc"})
    return logs
