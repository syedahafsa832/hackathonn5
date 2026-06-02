"""Canned Responses — pre-written reply templates for common questions."""
import logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from src.api.middleware.tenant_auth import get_current_tenant, TenantContext
from src.lib.supabase_client import supabase_select, supabase_insert, supabase_update, supabase_delete

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/canned-responses", tags=["Canned Responses"])


class CannedResponseCreate(BaseModel):
    title: str
    trigger_keywords: str  # comma-separated
    response_text: str


@router.get("")
async def list_canned_responses(tenant: TenantContext = Depends(get_current_tenant)):
    rows = supabase_select("canned_responses", {"tenant_id": f"eq.{tenant.tenant_id}", "order": "created_at.desc"})
    return {"success": True, "items": rows or []}


@router.post("")
async def create_canned_response(body: CannedResponseCreate, tenant: TenantContext = Depends(get_current_tenant)):
    try:
        row = supabase_insert("canned_responses", {
            "tenant_id": tenant.tenant_id,
            "title": body.title,
            "trigger_keywords": body.trigger_keywords,
            "response_text": body.response_text,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        return {"success": True, "item": row}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{item_id}")
async def delete_canned_response(item_id: str, tenant: TenantContext = Depends(get_current_tenant)):
    rows = supabase_select("canned_responses", {"id": f"eq.{item_id}", "tenant_id": f"eq.{tenant.tenant_id}"})
    if not rows:
        raise HTTPException(status_code=404, detail="Not found")
    supabase_delete("canned_responses", {"id": f"eq.{item_id}"})
    return {"success": True}
