"""
Events API Routes
Unified event stream for frontend dashboard
"""
import logging
from typing import Optional
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

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
):
    """
    Get unified event stream for dashboard.
    Returns all events in chronological order.
    """
    try:
        from src.lib.supabase_client import supabase_select

        # Build filters
        filters = {}
        if brand_id:
            filters["brand_id"] = f"eq.{brand_id}"

        # Get tickets as base events
        tickets = supabase_select("tickets", filters)

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

        # Get pending actions
        try:
            actions = supabase_select("pending_actions", {"status": "eq.Pending"})
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
            executed = supabase_select("pending_actions", {"status": "in.(Executed,Approved)"})
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
async def get_event(event_id: str):
    """
    Get a specific event by ID.
    """
    try:
        from src.lib.supabase_client import supabase_select

        # Extract ID from event_id format
        ticket_id = event_id.replace("evt-", "").split("-")[0]

        tickets = supabase_select("tickets", {"id": f"eq.{ticket_id}"})

        if not tickets:
            raise HTTPException(status_code=404, detail="Event not found")

        ticket = tickets[0]

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