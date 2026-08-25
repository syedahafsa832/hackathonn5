"""
Events API Routes
Unified event stream for frontend dashboard
"""
import logging
from typing import Optional
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query, Request, Depends
from pydantic import BaseModel

from src.api.middleware.tenant_auth import get_current_tenant, TenantContext

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/events", tags=["events"])


# ==================== Models ====================

class EventResponse(BaseModel):
    id: str
    type: str
    timestamp: str
    customer: dict
    metadata: dict
    lifecycle: Optional[dict] = None


# ==================== Routes ====================

@router.get("")
async def list_events(
    request: Request,
    type: Optional[str] = Query(None, description="Filter by event type"),
    status: Optional[str] = Query(None, description="Filter by status"),
    since: Optional[str] = Query(None, description="Events after this timestamp"),
    limit: int = Query(50, ge=1, le=200),
    brand_id: Optional[str] = Query(None, description="Filter by brand"),
    tenant: TenantContext = Depends(get_current_tenant),
):
    """
    Get unified event stream for dashboard.
    Returns all events in chronological order, scoped to the authenticated
    tenant's own brands only — never all tenants' tickets/actions.
    """
    try:
        from src.lib.supabase_client import supabase_select
        from src.api.routes.tickets import _get_tenant_brand_ids

        owned_brand_ids = await _get_tenant_brand_ids(tenant)
        if not owned_brand_ids:
            return []

        if brand_id:
            if brand_id not in owned_brand_ids:
                return []
            scoped_brand_ids = [brand_id]
        else:
            scoped_brand_ids = owned_brand_ids

        # Get tickets as base events, scoped to this tenant's own brands
        tickets = supabase_select("tickets", {"brand_id": f"in.({','.join(scoped_brand_ids)})"})

        if not tickets:
            return []

        events = []

        for ticket in tickets:
            # Email received event
            events.append({
                "id": f"evt-{ticket.get('id')}-email",
                "type": "email_received",
                "timestamp": ticket.get("created_at"),
                "customer": {
                    "email": ticket.get("customer_email", ""),
                    "name": ticket.get("customer_name"),
                },
                "metadata": {
                    "channel": ticket.get("source_channel", "email"),
                    "subject": ticket.get("subject"),
                    "message_preview": ticket.get("description", "")[:100] if ticket.get("description") else None,
                },
                "lifecycle": {
                    "child_events": [f"evt-{ticket.get('id')}-ai"],
                },
            })

            # AI Decision event (if processed)
            if ticket.get("intent"):
                events.append({
                    "id": f"evt-{ticket.get('id')}-ai",
                    "type": "ai_decision",
                    "timestamp": ticket.get("processed_at") or ticket.get("created_at"),
                    "customer": {
                        "email": ticket.get("customer_email", ""),
                        "name": ticket.get("customer_name"),
                    },
                    "metadata": {
                        "intent": ticket.get("intent"),
                        "sentiment": ticket.get("sentiment"),
                        "confidence": ticket.get("ai_confidence"),
                        "decision": "action_proposal" if ticket.get("intent") in ["refund", "cancel", "exchange"] else "auto_reply",
                    },
                    "lifecycle": {
                        "parent_event_id": f"evt-{ticket.get('id')}-email",
                        "child_events": [f"evt-{ticket.get('id')}-action"] if ticket.get("intent") in ["refund", "cancel", "exchange"] else [],
                    },
                })

        # pending_actions has no brand/tenant column of its own, so scope it by
        # cross-referencing against this tenant's own already brand-filtered
        # ticket ids — never return another tenant's pending/executed actions.
        scoped_ticket_ids = {t.get("id") for t in tickets}

        # Get pending actions
        try:
            actions = [
                a for a in (supabase_select("pending_actions", {"status": "eq.Pending"}) or [])
                if a.get("ticket_id") in scoped_ticket_ids
            ]
            for action in actions:
                events.append({
                    "id": f"evt-{action.get('id')}-action",
                    "type": "action_created",
                    "timestamp": action.get("created_at"),
                    "customer": {
                        "email": action.get("customer_email", ""),
                        "name": action.get("customer_name"),
                    },
                    "metadata": {
                        "action_type": action.get("action_type"),
                        "order_id": action.get("order_id"),
                        "risk_level": action.get("risk_score"),
                        "execution_status": "pending",
                    },
                    "lifecycle": {
                        "parent_event_id": f"evt-{action.get('ticket_id')}-ai" if action.get("ticket_id") else None,
                    },
                })
        except Exception as e:
            logger.warning(f"Could not fetch actions: {e}")

        # Get executed actions for completion events
        try:
            executed = [
                a for a in (supabase_select("pending_actions", {"status": "in.(Executed,Approved)"}) or [])
                if a.get("ticket_id") in scoped_ticket_ids
            ]
            for action in executed:
                events.append({
                    "id": f"evt-{action.get('id')}-executed",
                    "type": "execution_completed",
                    "timestamp": action.get("executed_at") or action.get("updated_at"),
                    "customer": {
                        "email": action.get("customer_email", ""),
                        "name": action.get("customer_name"),
                    },
                    "metadata": {
                        "action_type": action.get("action_type"),
                        "order_id": action.get("order_id"),
                        "execution_status": "success",
                    },
                    "lifecycle": {
                        "parent_event_id": f"evt-{action.get('id')}-action",
                    },
                })
        except Exception as e:
            logger.warning(f"Could not fetch executed actions: {e}")

        # Sort by timestamp descending
        events.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        # Apply limit
        events = events[:limit]

        return events

    except Exception as e:
        logger.error(f"Error fetching events: {e}")
        return []


@router.get("/{event_id}")
async def get_event(event_id: str, tenant: TenantContext = Depends(get_current_tenant)):
    """
    Get a specific event by ID.
    """
    try:
        from src.lib.supabase_client import supabase_select
        from src.api.routes.tickets import _get_tenant_brand_ids

        # Extract ID from event_id format
        ticket_id = event_id.replace("evt-", "").split("-")[0]

        tickets = supabase_select("tickets", {"id": f"eq.{ticket_id}"})

        if not tickets:
            raise HTTPException(status_code=404, detail="Event not found")

        ticket = tickets[0]
        ticket_brand_id = ticket.get("brand_id") or ticket.get("store_id")
        owned_brand_ids = await _get_tenant_brand_ids(tenant)
        if owned_brand_ids is not None and ticket_brand_id not in owned_brand_ids:
            raise HTTPException(status_code=404, detail="Event not found")

        return {
            "id": event_id,
            "type": "email_received",
            "timestamp": ticket.get("created_at"),
            "customer": {
                "email": ticket.get("customer_email", ""),
                "name": ticket.get("customer_name"),
            },
            "metadata": {
                "channel": ticket.get("source_channel"),
                "subject": ticket.get("subject"),
                "message_preview": ticket.get("description"),
            },
            "lifecycle": {},
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching event: {e}")
        raise HTTPException(status_code=500, detail=str(e))