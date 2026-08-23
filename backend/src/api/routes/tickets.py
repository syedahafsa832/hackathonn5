from fastapi import APIRouter, HTTPException, Query, Depends
from typing import Optional, List
from src.services.supabase_service import supabase_service
from src.lib.supabase_client import supabase_select, supabase_update
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext
from pydantic import BaseModel
from datetime import datetime, timezone
import logging
import time

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tickets", tags=["tickets"])

# In-memory TTL cache for the list endpoint — this loops over every owned brand on
# each call, and the dashboard hits it twice per load (stats + active list). A real
# Redis cache isn't available on the free Render tier (single worker anyway, so an
# in-process cache behaves the same), so this fills the same role.
_TICKETS_CACHE_TTL = 15  # seconds — matches the dashboard's own refetch interval
_tickets_cache: dict = {}


def _invalidate_tickets_cache():
    _tickets_cache.clear()

class TicketUpdate(BaseModel):
    status: Optional[str] = None
    escalate: Optional[bool] = None
    escalation_reason: Optional[str] = None
    ai_reply: Optional[str] = None

class SendReplyRequest(BaseModel):
    body: Optional[str] = None  # manual text; if omitted, uses ai_draft from ticket


class ReviewDecisionRequest(BaseModel):
    decision: str  # "approve" | "edit_approve" | "reject"
    edited_response: Optional[str] = None
    rejection_reason: Optional[str] = None


# Deterministic, fixed vocabulary — never classified by an LLM. Any other
# string is still stored as-is (a merchant typing something outside this
# list isn't blocked), this is only what the frontend's picker offers.
REJECTION_REASONS = {
    "Wrong tone", "Wrong information", "Missing information",
    "Policy issue", "Too verbose", "Other",
}


def _compute_review_status(ticket: dict) -> Optional[str]:
    """Needs Review / Approved / Edited / Rejected for 'Review Luna's Work' —
    derived purely from the existing human_approved/human_response fields
    (already written by send-reply/approve-ai above and by v2_tickets.py's
    equivalents) plus human_rejected (migration 049). Never a second status
    column to keep in sync. None means this ticket has no Luna-authored
    reply at all, so it's not applicable to review."""
    if not (ticket.get("ai_reply") or ticket.get("ai_draft") or ticket.get("ai_response")):
        return None
    if ticket.get("human_rejected"):
        return "rejected"
    if ticket.get("human_approved") and ticket.get("human_response"):
        return "edited"
    if ticket.get("human_approved"):
        return "approved"
    return "needs_review"


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
    cache_key = f"{tenant.tenant_id}:{status}:{store_id}"
    cached = _tickets_cache.get(cache_key)
    if cached and (time.time() - cached[1]) < _TICKETS_CACHE_TTL:
        return cached[0]

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
                # Sort by activity so active threads float to the top
                all_tickets.sort(key=lambda x: x.get("updated_at") or x.get("last_message_at") or x.get("created_at") or "", reverse=True)
                tickets = all_tickets
            else:
                # Brand not linked to tenant yet — return empty rather than leaking all tickets
                tickets = []

        from src.api.routes.v2_tickets import _normalize_ticket_messages
        for t in tickets:
            if not t.get("channel"):
                t["channel"] = "email"
            # last_message has no backing DB column (confirmed via schema check) —
            # nothing ever populated it, so the dashboard's Recent Conversations
            # widget (which already reads c.last_message) silently showed "—" for
            # every ticket. Derive it from the same normalized-message list the
            # ticket detail view already trusts, so it's never a second, divergent
            # parse of the thread.
            normalized = _normalize_ticket_messages(t)
            t["last_message"] = normalized[-1]["content"] if normalized else ""
        _tickets_cache[cache_key] = (tickets, time.time())
        return tickets
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/review/queue")
async def list_review_queue(
    review_status: Optional[str] = Query(None, description="needs_review, approved, edited, or rejected"),
    store_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Real Luna-authored conversations for human review ("Review Luna's
    Work") — reuses the exact same tenant-scoped ticket rows Conversation
    Replay and the Conversations list already read, never a second
    conversation store. review_status is computed from the existing
    human_approved/human_response/human_rejected fields, the same ones
    Reply Style Learning's organic counter already reads (see
    reply_style_service._approved_reply_texts)."""
    try:
        brand_ids = await _get_tenant_brand_ids(tenant)
        if not brand_ids:
            return {"items": [], "count": 0}
        if store_id:
            if store_id not in brand_ids:
                return {"items": [], "count": 0}
            brand_ids = [store_id]

        all_tickets: list = []
        for bid in brand_ids:
            all_tickets.extend(await supabase_service.get_tickets(store_id=bid, status=None))
        all_tickets.sort(key=lambda t: t.get("updated_at") or t.get("created_at") or "", reverse=True)

        # Batch lookup of any linked Shopify action (cancel/refund/exchange/
        # address change) for "action usage" context — reuses the existing
        # actions table (actions.ticket_id), never a new audit trail.
        ticket_ids = [t["id"] for t in all_tickets if t.get("id")]
        actions_by_ticket: dict = {}
        if ticket_ids:
            id_list = ",".join(ticket_ids)
            linked_actions = supabase_select("actions", {"ticket_id": f"in.({id_list})"}) or []
            for a in linked_actions:
                actions_by_ticket.setdefault(a["ticket_id"], []).append({
                    "action_type": a.get("action_type"), "status": a.get("status"),
                })

        items = []
        for t in all_tickets:
            status = _compute_review_status(t)
            if status is None:
                continue
            if review_status and status != review_status:
                continue
            items.append({
                "ticket_id": t.get("id"),
                "customer_message": t.get("message") or t.get("body") or "",
                "luna_reply": t.get("human_response") or t.get("ai_reply") or t.get("ai_draft") or t.get("ai_response") or "",
                "channel": t.get("channel") or "email",
                "created_at": t.get("created_at"),
                "updated_at": t.get("updated_at"),
                "order_id": t.get("order_id"),
                "actions": actions_by_ticket.get(t.get("id"), []),
                "human_outcome": {
                    "approved": bool(t.get("human_approved")),
                    "edited": bool(t.get("human_response")),
                    "rejected": bool(t.get("human_rejected")),
                    "rejection_reason": t.get("human_rejected_reason"),
                },
                "review_status": status,
            })
            if len(items) >= limit:
                break

        return {"items": items, "count": len(items)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Tickets] review-queue error: {e}")
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

        # conversation_overrides (not tickets.status) is the authoritative source
        # for "is a human actively managing this" - send-reply overwrites
        # tickets.status to "resolved" on every manual send (legitimate ticket-
        # lifecycle behavior, not a takeover release), so status alone can't be
        # trusted for this. The dashboard reads this field instead of deriving
        # it from status.
        ticket["human_override_active"] = await supabase_service.check_conversation_override(ticket_id)

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

        # Post-conversation customer feedback (rating + optional written
        # comment), if the customer left any via SatisfactionRating.
        feedback_rows = supabase_select("chat_feedback", {
            "ticket_id": f"eq.{ticket_id}",
            "order": "created_at.desc",
            "limit": "1",
        })
        ticket["feedback"] = feedback_rows[0] if feedback_rows else None

        return ticket
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def _assert_ticket_access(ticket_id: str, tenant: TenantContext) -> dict:
    """Fetch a ticket and raise 404 if it doesn't belong to the authenticated tenant.
    Mirrors the brand-ownership check already used by GET /{ticket_id}."""
    ticket = await supabase_service.get_ticket_by_id(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket_brand_id = ticket.get("brand_id") or ticket.get("store_id")
    brand_ids = await _get_tenant_brand_ids(tenant)
    if brand_ids is not None and ticket_brand_id not in brand_ids:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


@router.patch("/{ticket_id}")
async def update_ticket(
    ticket_id: str,
    updates: TicketUpdate,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Update a ticket status or metadata."""
    try:
        await _assert_ticket_access(ticket_id, tenant)
        update_data = updates.dict(exclude_unset=True)
        if not update_data:
            raise HTTPException(status_code=400, detail="No updates provided")
        result = await supabase_service.update_ticket(ticket_id, update_data)
        _invalidate_tickets_cache()
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{ticket_id}/send-reply")
async def send_reply(
    ticket_id: str,
    req: SendReplyRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Send a reply email for a ticket — manual text or approve pending AI draft."""
    try:
        ticket = await _assert_ticket_access(ticket_id, tenant)

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

        # Mark sent and update status. human_approved is set either way - a
        # human sent this reply whether they edited it first or approved
        # Luna's draft as-is; reply_style_service's eligible-approved-reply
        # count relies on this flag (previously only set for a manual edit,
        # so a plain "Approve" click never counted toward Reply Style
        # learning even though a human demonstrably stood behind it).
        is_manual = bool(req.body)
        now_iso = datetime.now(timezone.utc).isoformat()
        supabase_update("tickets", {"id": f"eq.{ticket_id}"}, {
            "status": "resolved",
            "email_sent": True,
            "email_sent_at": now_iso,
            "human_approved": True,
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
        _invalidate_tickets_cache()

        return {"success": True, "message": "Reply sent successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Tickets] send-reply error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{ticket_id}/approve-ai")
async def approve_ai(
    ticket_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Approve and send the AI-generated draft — alias for send-reply with no body."""
    return await send_reply(ticket_id, SendReplyRequest(), tenant)


@router.post("/{ticket_id}/review")
async def review_ai_reply(
    ticket_id: str,
    request: ReviewDecisionRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Record a human's review decision on a real Luna reply — Approve /
    Edit & Approve / Reject ("Review Luna's Work"). This is retrospective
    quality review of a reply that already exists (drafted or already
    sent), separate from the pre-send send-reply/approve-ai flow above:
    it never sends or re-sends anything. Approve and Edit & Approve write
    the exact same human_approved/human_response fields those endpoints
    already write, so Reply Style Learning's organic counter
    (reply_style_service.count_eligible_approved_replies) counts it
    identically — no second learning system. Reject writes human_rejected
    (migration 049), the one outcome with no prior representation, and
    never touches human_approved/human_response, so a rejected reply can
    never count toward the 20-approved-replies threshold."""
    try:
        ticket = await _assert_ticket_access(ticket_id, tenant)
        ai_text = ticket.get("ai_reply") or ticket.get("ai_draft") or ticket.get("ai_response")
        if not ai_text:
            raise HTTPException(status_code=400, detail="This conversation has no Luna reply to review")

        now_iso = datetime.now(timezone.utc).isoformat()
        if request.decision == "approve":
            updates = {
                "human_approved": True,
                "human_approved_at": now_iso,
                "human_rejected": False,
                "updated_at": now_iso,
            }
        elif request.decision == "edit_approve":
            if not request.edited_response or not request.edited_response.strip():
                raise HTTPException(status_code=400, detail="edited_response is required for edit_approve")
            updates = {
                "human_response": request.edited_response.strip(),
                "human_approved": True,
                "human_approved_at": now_iso,
                "human_rejected": False,
                "updated_at": now_iso,
            }
        elif request.decision == "reject":
            updates = {
                "human_rejected": True,
                "human_rejected_at": now_iso,
                "human_rejected_reason": request.rejection_reason,
                "updated_at": now_iso,
            }
        else:
            raise HTTPException(status_code=400, detail="decision must be approve, edit_approve, or reject")

        supabase_update("tickets", {"id": f"eq.{ticket_id}"}, updates)
        _invalidate_tickets_cache()
        merged = {**ticket, **updates}
        return {"success": True, "review_status": _compute_review_status(merged)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Tickets] review error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{ticket_id}/reply-suggestions")
async def get_reply_suggestions(
    ticket_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Generate 3 reply variations from the AI draft for quick human response."""
    import json
    from src.services.ai_provider_manager import ai_provider_manager

    try:
        ticket = await _assert_ticket_access(ticket_id, tenant)

        body = ticket.get("message", "")
        draft = ticket.get("ai_draft") or ticket.get("ai_reply", "")

        if not body and not draft:
            return {"success": True, "suggestions": {"short": "", "detailed": "", "empathetic": ""}}

        if not ai_provider_manager.has_providers:
            return {"success": True, "suggestions": {"short": draft, "detailed": draft, "empathetic": draft}}

        prompt = f"""Given this customer email:
{body[:500]}

And this draft reply:
{draft[:500]}

Write exactly 3 reply variations as JSON:
{{"short": "2-3 sentences max", "detailed": "full explanation with steps", "empathetic": "starts by acknowledging how the customer feels"}}

JSON only, no markdown."""

        resp, _label, _model, _usage = await ai_provider_manager.create_chat_completion(
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

