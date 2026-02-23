from fastapi import APIRouter, HTTPException, Depends, Query, Request
from typing import Optional, List, Dict
from datetime import datetime, timezone
import uuid
import csv
import io
from fastapi.responses import StreamingResponse
import json

from src.services.supabase_service import supabase_service
from src.lib.supabase_client import supabase_update, supabase_select

router = APIRouter(prefix="/admin", tags=["admin"])

# 1. HUMAN TAKEOVER SYSTEM
@router.post("/conversations/{id}/takeover")
async def takeover_conversation(id: str, request: Request):
    """Prevent further AI auto-replies for a specific conversation."""
    try:
        payload = await request.json()
        user_id = payload.get("user_id", "admin")
        
        # 1. Look up the ticket
        ticket = await supabase_service.get_ticket_by_id(id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Conversation not found")
            
        store_id = ticket.get("store_id")

        # 2. Insert record into conversation_overrides
        from src.lib.supabase_client import supabase_insert
        supabase_insert("conversation_overrides", {
            "conversation_id": id,
            "overridden_by": user_id,
            "active": True
        })

        # 3. Update ticket status
        await supabase_service.update_ticket(id, {"status": "human_managing"})

        # 4. Log takeover event
        await supabase_service.log_audit(store_id, "takeover", user_id, {"conversation_id": id})

        return {"status": "success", "message": "Human takeover active. AI auto-replies disabled."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/conversations/{id}/release")
async def release_conversation(id: str, request: Request):
    """Release human takeover and allow AI to respond again (if mode is active)."""
    try:
        # Deactivate all active overrides for this conversation
        supabase_update("conversation_overrides", 
            {"conversation_id": f"eq.{id}", "active": "eq.true"}, 
            {"active": False}
        )
        return {"status": "success", "message": "Conversation released to AI control."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 2. GDPR-ALIGNED DATA HANDLING
@router.delete("/customer/{email}")
async def erase_customer_data(email: str, store_id: str = Query(...)):
    """GDPR Right to Erasure implementation."""
    try:
        # In a real app, verify user belongs to store_id here
        await supabase_service.delete_customer_data(email, store_id)
        await supabase_service.log_audit(store_id, "erasure", "system", {"target_email": email})
        return {"status": "success", "message": f"Data for {email} has been erased/anonymized."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 3. DATA EXPORT SYSTEM
@router.get("/export/conversations")
async def export_conversations(
    store_id: str = Query(...),
    format: str = Query("json", regex="^(json|csv)$"),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """Generate scoped data export for a store."""
    try:
        # 1. Fetch data
        params = {"store_id": f"eq.{store_id}"}
        if start_date: params["created_at"] = f"gte.{start_date}"
        if end_date: params["created_at"] = f"lte.{end_date}"
        
        tickets = supabase_select("tickets", params)
        
        # 2. Log export action
        await supabase_service.log_audit(store_id, "export", "admin", {"format": format})

        if format == "json":
            return tickets
            
        # 3. Generate CSV
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=[
            "id", "customer_email", "customer_name", "subject", 
            "message", "ai_reply", "confidence_score", "status", "created_at"
        ])
        writer.writeheader()
        for t in tickets:
            # Only export necessary fields
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

# 4. MODE CONTROL
@router.post("/settings/ai-mode")
async def update_ai_mode(request: Request):
    """Update operational mode for the store."""
    try:
        payload = await request.json()
        store_id = payload.get("store_id")
        mode = payload.get("mode") # active, paused, manual
        
        if mode not in ["active", "paused", "manual"]:
            raise HTTPException(status_code=400, detail="Invalid mode")
            
        supabase_update("system_settings", {"store_id": f"eq.{store_id}"}, {"ai_mode": mode})
        await supabase_service.log_audit(store_id, "mode_change", "admin", {"new_mode": mode})
        
        return {"status": "success", "message": f"AI mode set to {mode}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
