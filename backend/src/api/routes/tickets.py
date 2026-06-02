from fastapi import APIRouter, HTTPException, Query, Depends
from typing import Optional, List
from src.services.supabase_service import supabase_service
from src.lib.supabase_client import supabase_select, supabase_update
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext
from pydantic import BaseModel
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tickets", tags=["tickets"])

class TicketUpdate(BaseModel):
    status: Optional[str] = None
    escalate: Optional[bool] = None
    escalation_reason: Optional[str] = None
    ai_reply: Optional[str] = None

class SendReplyRequest(BaseModel):
    body: Optional[str] = None  # manual text; if omitted, uses ai_draft from ticket


async def _get_tenant_brand_ids(tenant: TenantContext) -> Optional[List[str]]:
    """Return brand IDs owned by this tenant, or None if we can't determine ownership.
    Includes inactive brands to catch the onboarding 409 edge case where a brand is
    deactivated but still has Gmail connected and active tickets."""
    from src.services.auth_service import auth_service
    # Return ALL brands for this tenant (active or not) — tickets may belong to inactive brands
    owned = supabase_select("brands", {"tenant_id": f"eq.{tenant.tenant_id}"})
    if owned:
        return [b["id"] for b in owned]
    # Fallback: match via shopify_domain for rows before migration 010
    tenant_data = await auth_service.get_tenant(tenant.tenant_id)
    shopify_domain = (tenant_data or {}).get("shopify_domain")
    if shopify_domain:
        brands = supabase_select("brands", {"shopify_domain": f"eq.{shopify_domain}"})
        if brands:
            return [b["id"] for b in brands]
    return None


@router.get("")
async def list_tickets(
    status: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    tenant: TenantContext = Depends(get_current_tenant),
):
    """List tickets scoped to the current tenant's brands."""
    try:
        # If caller specifies a store_id, verify it belongs to this tenant
        if store_id:
            brand_ids = await _get_tenant_brand_ids(tenant)
            if brand_ids and store_id not in brand_ids:
                return []  # return empty rather than 403 (don't confirm existence)
            tickets = await supabase_service.get_tickets(store_id=store_id, status=status)
        else:
            brand_ids = await _get_tenant_brand_ids(tenant)
            if brand_ids:
                # Fetch tickets for each owned brand and merge
                all_tickets: list = []
                for bid in brand_ids:
                    t = await supabase_service.get_tickets(store_id=bid, status=status)
                    all_tickets.extend(t)
                # Sort by created_at descending
                all_tickets.sort(key=lambda x: x.get("created_at", ""), reverse=True)
                tickets = all_tickets
            else:
                # Brand not linked to tenant yet — return empty rather than leaking all tickets
                tickets = []

        for t in tickets:
            if not t.get("channel"):
                t["channel"] = "email"
        return tickets
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{ticket_id}")
async def get_ticket(
    ticket_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Fetch a single ticket by UUID, scoped to the current tenant's brands."""
    try:
        ticket = await supabase_service.get_ticket_by_id(ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")

        # Tickets use store_id as the brand FK (brand_id is a secondary alias on some rows)
        ticket_brand_id = ticket.get("brand_id") or ticket.get("store_id")

        # Verify the ticket belongs to one of this tenant's brands
        brand_ids = await _get_tenant_brand_ids(tenant)
        if brand_ids is not None and ticket_brand_id not in brand_ids:
            # Auto-heal: if this brand has no tenant_id yet, link it to the current tenant
            if ticket_brand_id:
                brand_row = supabase_select("brands", {"id": f"eq.{ticket_brand_id}"})
                if brand_row and brand_row[0].get("tenant_id") is None:
                    supabase_update("brands", {"id": f"eq.{ticket_brand_id}"}, {"tenant_id": tenant.tenant_id})
                    logger.info(f"[Tickets] Auto-linked brand {ticket_brand_id} to tenant {tenant.tenant_id}")
                    return ticket
            raise HTTPException(status_code=404, detail="Ticket not found")

        return ticket
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/{ticket_id}")
async def update_ticket(ticket_id: str, updates: TicketUpdate):
    """Update a ticket status or metadata."""
    try:
        update_data = updates.dict(exclude_unset=True)
        if not update_data:
            raise HTTPException(status_code=400, detail="No updates provided")
        result = await supabase_service.update_ticket(ticket_id, update_data)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{ticket_id}/send-reply")
async def send_reply(ticket_id: str, req: SendReplyRequest):
    """Send a reply email for a ticket — manual text or approve pending AI draft."""
    try:
        ticket = await supabase_service.get_ticket_by_id(ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")

        reply_body = req.body or ticket.get("ai_draft") or ticket.get("ai_reply")
        if not reply_body:
            raise HTTPException(status_code=400, detail="No reply body available")

        store_id = ticket.get("store_id")
        customer_email = ticket.get("customer_email")
        subject = ticket.get("subject", "Support")
        reply_subject = f"Re: {subject}" if not subject.startswith("Re:") else subject

        default_store = "00000000-0000-0000-0000-000000000000"
        sent = False

        if store_id and store_id != default_store:
            try:
                from src.services.brand_gmail_service import brand_gmail_service
                brands = supabase_select("brands", {"id": f"eq.{store_id}", "gmail_connected": "is.true"})
                if brands:
                    result = await brand_gmail_service.send_email(brands[0], customer_email, reply_subject, reply_body)
                    if result.get("success"):
                        sent = True
                        logger.info(f"[Tickets] Reply sent via brand Gmail for ticket {ticket_id}")
                    else:
                        logger.warning(f"[Tickets] Brand Gmail send failed: {result.get('error')}")
            except Exception as e:
                logger.error(f"[Tickets] Brand Gmail error: {e}")

        if not sent:
            raise HTTPException(
                status_code=400,
                detail="No Gmail connected for this brand. Go to Brands → Connect Gmail first."
            )

        # Mark sent and update status
        is_manual = bool(req.body)
        now_iso = datetime.now(timezone.utc).isoformat()
        supabase_update("tickets", {"id": f"eq.{ticket_id}"}, {
            "status": "resolved",
            "email_sent": True,
            "email_sent_at": now_iso,
            **({"human_response": reply_body} if is_manual else {"ai_reply": reply_body}),
        })

        # Append outbound message so conversation replay shows it
        existing_messages = list(ticket.get("messages") or [])
        existing_messages.append({
            "from": "Support",
            "body": reply_body,
            "sent_at": now_iso,
            "direction": "outbound",
        })
        supabase_update("tickets", {"id": f"eq.{ticket_id}"}, {"messages": existing_messages})

        return {"success": True, "message": "Reply sent successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Tickets] send-reply error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{ticket_id}/approve-ai")
async def approve_ai(ticket_id: str):
    """Approve and send the AI-generated draft — alias for send-reply with no body."""
    return await send_reply(ticket_id, SendReplyRequest())


@router.get("/{ticket_id}/reply-suggestions")
async def get_reply_suggestions(ticket_id: str):
    """Generate 3 reply variations from the AI draft for quick human response."""
    import os, json
    from openai import OpenAI

    try:
        ticket = await supabase_service.get_ticket_by_id(ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")

        body = ticket.get("message", "")
        draft = ticket.get("ai_draft") or ticket.get("ai_reply", "")

        if not body and not draft:
            return {"success": True, "suggestions": {"short": "", "detailed": "", "empathetic": ""}}

        api_key = os.getenv("MISTRAL_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            return {"success": True, "suggestions": {"short": draft, "detailed": draft, "empathetic": draft}}

        client_obj = OpenAI(
            api_key=api_key,
            base_url=os.getenv("MISTRAL_API_BASE_URL", "https://api.mistral.ai/v1")
        )

        prompt = f"""Given this customer email:
{body[:500]}

And this draft reply:
{draft[:500]}

Write exactly 3 reply variations as JSON:
{{"short": "2-3 sentences max", "detailed": "full explanation with steps", "empathetic": "starts by acknowledging how the customer feels"}}

JSON only, no markdown."""

        resp = client_obj.chat.completions.create(
            model=os.getenv("MISTRAL_MODEL", "mistral-large-latest"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        raw = resp.choices[0].message.content.strip()
        # Clean markdown if present
        if raw.startswith("```"): raw = raw.split("```")[1].lstrip("json").strip()
        suggestions = json.loads(raw)
        return {"success": True, "suggestions": suggestions}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Tickets] reply-suggestions error: {e}")
        draft_text = ""
        try:
            ticket = await supabase_service.get_ticket_by_id(ticket_id)
            draft_text = (ticket or {}).get("ai_draft") or (ticket or {}).get("ai_reply", "")
        except Exception:
            pass
        return {"success": True, "suggestions": {"short": draft_text, "detailed": draft_text, "empathetic": draft_text}}
