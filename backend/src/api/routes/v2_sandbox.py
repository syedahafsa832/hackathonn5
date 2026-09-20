"""
Luna Sandbox routes (/api/v2/sandbox/*).

Authentication is intentional and split by risk:
- Instant demos (list / get / resolve) are PUBLIC: they serve only static
  fixtures for a fictional sample store, take no tenant/brand/store id at all,
  and cannot read or change anything real. They are rate limited per IP.
- "Ask Luna" (the only route that can spend AI quota) REQUIRES a signed-in
  tenant, and is additionally limited server-side per tenant, per IP and
  globally - see sandbox_service.SandboxAIBudget.

This module imports no Shopify, Gmail, actions or database code.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from src.api.middleware.tenant_auth import get_current_tenant, TenantContext
from src.lib.rate_limiter import limiter
from src.services import sandbox_service as sandbox

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sandbox", tags=["Sandbox"])


class ResolveRequest(BaseModel):
    decision: str = Field(description="'approve' or 'reject'")


class AskRequest(BaseModel):
    message: str = Field(min_length=1, max_length=sandbox.ASK_MAX_CHARS * 2)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or "unknown"
    return request.client.host if request.client else "unknown"


def _raise(exc: sandbox.SandboxError):
    raise HTTPException(status_code=exc.status_code, detail={"error": exc.code, "message": exc.message})


@router.get("/scenarios")
@limiter.limit("60/minute")
async def list_scenarios(request: Request):
    from src.services.sandbox_data import STORE, SANDBOX_NOTICE
    return {"sandbox": True, "store": STORE, "notice": SANDBOX_NOTICE, "scenarios": sandbox.list_scenarios()}


@router.get("/scenarios/{scenario_id}")
@limiter.limit("60/minute")
async def get_scenario(request: Request, scenario_id: str):
    try:
        return sandbox.build_scenario(scenario_id)
    except sandbox.SandboxError as e:
        _raise(e)


@router.post("/scenarios/{scenario_id}/resolve")
@limiter.limit("60/minute")
async def resolve_scenario(request: Request, scenario_id: str, payload: ResolveRequest):
    try:
        return sandbox.resolve_scenario(scenario_id, payload.decision)
    except sandbox.SandboxError as e:
        _raise(e)


@router.get("/ask/status")
@limiter.limit("30/minute")
async def ask_status(request: Request, tenant: TenantContext = Depends(get_current_tenant)):
    return {
        "sandbox": True,
        "remaining": sandbox.sandbox_ai_budget.remaining(tenant.tenant_id),
        "limit": sandbox.sandbox_ai_budget.per_tenant,
    }


@router.post("/ask")
@limiter.limit("10/minute")
async def ask(request: Request, payload: AskRequest, tenant: TenantContext = Depends(get_current_tenant)):
    try:
        return await sandbox.ask_luna(payload.message, tenant.tenant_id, _client_ip(request))
    except sandbox.SandboxError as e:
        _raise(e)
