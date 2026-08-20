"""
Chat Widget API
===============
Public endpoints for the embeddable chat widget.
No tenant auth — brand_id (UUID) in request identifies the brand.
Sessions are stored as tickets with channel='chat'.
"""
import re
import time
import uuid
import json
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional, List, Callable, Awaitable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from src.lib.supabase_client import supabase_select, supabase_insert, supabase_update

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Chat Widget"])

# ── In-memory rate limiter ─────────────────────────────────────────────────
_rate_buckets: dict = defaultdict(list)
_RATE_WINDOW = 60   # seconds
_RATE_MAX = 10      # requests per window


def _allow(ip: str) -> bool:
    now = time.time()
    cutoff = now - _RATE_WINDOW
    _rate_buckets[ip] = [t for t in _rate_buckets[ip] if t > cutoff]
    if len(_rate_buckets[ip]) >= _RATE_MAX:
        return False
    _rate_buckets[ip].append(now)
    return True


def _sanitize(text: str, max_len: int = 1000) -> str:
    """Strip HTML tags and limit length."""
    clean = re.sub(r"<[^>]+>", "", text or "")
    return clean.strip()[:max_len]


# ── Request / Response models ──────────────────────────────────────────────

class ChatRequest(BaseModel):
    brand_id: str
    session_id: str
    message: str
    customer_email: Optional[str] = None
    customer_name: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    suggested_actions: List[str] = []
    confidence: Optional[int] = None
    resolution_step: Optional[str] = None
    order_data: Optional[dict] = None
    customer_name: Optional[str] = None
    action_result: Optional[dict] = None
    resolution_complete: bool = False


class FeedbackRequest(BaseModel):
    session_id: str
    rating: str  # 'positive' | 'negative'


def _map_resolution_step(intent: Optional[str], status: Optional[str]) -> str:
    """Map agent intent/status to widget resolution_step."""
    if status == "auto_resolved":
        if intent in ("refund_request", "cancellation_request", "address_change"):
            return "verifying"
        return "resolved"
    if intent in ("refund_request", "cancellation_request", "address_change"):
        return "acting"
    if intent in ("order_status_inquiry", "shipping_inquiry"):
        return "gathering"
    return "understanding"


def _map_action_result(intent: Optional[str], action_taken: Optional[dict], order_data: Optional[dict]) -> Optional[dict]:
    """Build action_result dict from the REAL staged-action outcome
    (customer_success_agent's "action_taken", sourced from
    return_actions_integration._create_action's actual Supabase insert) —
    never from the LLM's self-reported intent/status. A customer must not be
    told a refund/cancel/address-change request was staged unless a pending
    action row was actually created for it."""
    if not action_taken or not action_taken.get("success"):
        return None
    order_number = (order_data or {}).get("orderNumber", "")
    if intent == "refund_request":
        amount = None
        if order_data:
            items = order_data.get("items", [])
            if items:
                amount = items[0].get("price")
        return {"type": "refund_staged", "amount": amount, "order_number": order_number}
    if intent == "cancellation_request":
        return {"type": "cancel_staged", "order_number": order_number}
    if intent == "address_change":
        return {"type": "address_updated", "new_address": None}
    return None


class EmailCaptureRequest(BaseModel):
    email: str


# ── Helpers ────────────────────────────────────────────────────────────────

def _get_brand(brand_id: str) -> dict:
    """Validate brand exists. Raises 404 on failure."""
    brands = supabase_select("brands", {"id": f"eq.{brand_id}"})
    if not brands:
        raise HTTPException(status_code=404, detail="Brand not found")
    return brands[0]


def _get_or_create_session(session_id: str, brand_id: str, customer_email: Optional[str] = None, customer_name: Optional[str] = None) -> dict:
    """Look up existing chat ticket by session_id or create one."""
    existing = supabase_select("tickets", {"gmail_thread_id": f"eq.{session_id}", "channel": "eq.chat"})
    if existing:
        return existing[0]

    ticket = supabase_insert("tickets", {
        "channel":         "chat",
        "status":          "open",
        "gmail_thread_id": session_id,
        "brand_id":        brand_id,
        "store_id":        brand_id,  # v1 list_tickets queries store_id; keep in sync
        "customer_email":  customer_email,
        "customer_name":   customer_name or "Website Visitor",
        "messages":        [],
        "created_at":      datetime.now(timezone.utc).isoformat(),
        "updated_at":      datetime.now(timezone.utc).isoformat(),
    })
    return ticket


def _build_history_context(messages: list, agent_name: str = "Luna") -> str:
    """Turn recent messages into a context string for the agent."""
    if not messages:
        return ""
    recent = messages[-6:]  # last 3 exchanges
    lines = ["[CHAT HISTORY — earlier in this conversation:]"]
    for m in recent:
        role = "Customer" if m.get("direction") == "inbound" else agent_name
        lines.append(f"{role}: {m.get('body', '')}")
    lines.append("[END CHAT HISTORY]")
    return "\n".join(lines)


# ── Endpoints ──────────────────────────────────────────────────────────────

async def _generate_reply(
    body: ChatRequest,
    brand: dict,
    ticket: dict,
    ticket_id: str,
    tenant_id: Optional[str],
    on_progress: Optional[Callable[[str, str], Awaitable[None]]] = None,
) -> ChatResponse:
    """Run the agent for one chat turn and persist the outcome.

    Shared by the plain-JSON and streaming (`?stream=1`) paths in `chat()`
    below so both stay identical in behavior — only how the caller receives
    the result differs. `on_progress`, when given, is forwarded to the agent
    so real dispatch stages (order lookup, product lookup, ...) can be
    surfaced to the customer as they actually happen; it is None on the
    plain-JSON path, which does not stream and needs no progress events.
    """
    from src.services import plan_service

    brand_name = brand.get("name", "our store")
    agent_name = brand.get("agent_name") or "Luna"
    message = _sanitize(body.message)

    # Parse stored messages
    stored_msgs = ticket.get("messages") or []
    if isinstance(stored_msgs, str):
        try:
            stored_msgs = json.loads(stored_msgs)
        except Exception:
            stored_msgs = []

    # Append customer message
    now_iso = datetime.now(timezone.utc).isoformat()
    stored_msgs.append({
        "direction": "inbound",
        "body": message,
        "created_at": now_iso,
    })

    # Build query for the agent: history + current message
    history_ctx = _build_history_context(stored_msgs[:-1], agent_name=agent_name)
    full_query = f"{history_ctx}\n\nCustomer: {message}" if history_ctx else message

    result_confidence: Optional[int] = None
    result_resolution_step: Optional[str] = None
    result_order_data: Optional[dict] = None
    result_action_result: Optional[dict] = None
    result_resolution_complete: bool = False
    ticket_status_update: Optional[str] = None
    ticket_escalate: bool = False
    ticket_escalation_reason: Optional[str] = None

    # Call the agent
    try:
        from src.agent.customer_success_agent import customer_success_agent

        customer_info = {
            "name": body.customer_name or "there",
            "email": body.customer_email or ticket.get("customer_email") or "",
            "channel": "chat",
        }

        # Prepend chat-mode instruction to query so the agent knows context
        chat_query = (
            "[CHAT MODE — reply in 1-3 short sentences, conversational tone, no bullet points]\n"
            f"{full_query}"
        )

        result = await customer_success_agent.process_customer_query(
            query=chat_query,
            customer_info=customer_info,
            tenant_id=tenant_id,
            store_id=body.brand_id,
            ticket_id=ticket_id,
            on_progress=on_progress,
        )

        reply_body = result.get("reply_body", "Hey! Let me look into that for you.")
        # Keep sign-off line if present
        sign_off_marker = f"— {agent_name}"
        if sign_off_marker in reply_body:
            sign_idx = reply_body.find(sign_off_marker)
            reply_clean = reply_body[:sign_idx].strip()
        else:
            reply_clean = reply_body

        result_confidence = result.get("confidence_score")
        agent_intent = result.get("intent")
        agent_status = result.get("status")
        result_resolution_step = _map_resolution_step(agent_intent, agent_status)
        result_order_data = result.get("order_data")
        result_action_result = _map_action_result(agent_intent, result.get("action_taken"), result_order_data)
        result_resolution_complete = agent_status in ("auto_resolved", "escalated")

        ticket_escalate = result.get("escalate", False)
        ticket_escalation_reason = result.get("escalation_reason")
        if ticket_escalate or agent_status == "escalated":
            ticket_status_update = "escalated"
        elif agent_status in ("auto_resolved", "auto_resolved_review"):
            ticket_status_update = agent_status

        # ai_reply_generated is only set on the real model-generated path —
        # every fallback path (provider outage, empty response, JSON parse
        # error) returns the same canned "having trouble" reply_body without
        # it, so reply_body alone can't detect a real generation. A failed
        # AI call must never consume trial/plan quota.
        if result.get("reply_body") and result.get("ai_reply_generated"):
            plan_service.record_ai_reply_event(
                tenant_id,
                brand_id=body.brand_id,
                channel="chat_widget",
                ticket_id=ticket_id,
                customer_identifier=body.customer_email or ticket.get("customer_email") or body.session_id,
                model_used=result.get("model_used"),
            )

    except Exception as e:
        logger.error(f"[ChatWidget] Agent error: {e}")
        reply_clean = f"Hey! Thanks for reaching out. I'll get someone from the {brand_name} team to follow up with you shortly."
        result_resolution_step = "understanding"
        ticket_status_update = "escalated"
        ticket_escalate = True
        ticket_escalation_reason = f"Agent error: {e}"

    # The reply text is ready — what's left (persisting the ticket, building
    # the response) is real, if brief, work of its own.
    if on_progress:
        try:
            await on_progress("sending", "Sending your reply…")
        except Exception:
            pass

    # Append the agent's reply
    stored_msgs.append({
        "direction": "outbound",
        "body": reply_clean,
        "role": "ai",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    # Update ticket
    ticket_update: dict = {
        "messages":       stored_msgs,
        "customer_email": body.customer_email or ticket.get("customer_email"),
        "email_sent":     True,
        "updated_at":     datetime.now(timezone.utc).isoformat(),
    }
    if ticket_status_update:
        ticket_update["status"] = ticket_status_update
    if ticket_escalate:
        ticket_update["escalate"] = True
    if ticket_escalation_reason:
        ticket_update["escalation_reason"] = ticket_escalation_reason
    supabase_update("tickets", {"id": f"eq.{ticket_id}"}, ticket_update)
    if ticket_escalate:
        logger.info(f"[ChatWidget] Ticket {ticket_id} escalated — reason: {ticket_escalation_reason or 'agent flagged'}")

    # Suggested quick-reply actions (only on first exchange)
    suggested = []
    if len(stored_msgs) <= 2:
        suggested = ["Where is my order?", "I want a refund", "Change my address"]

    return ChatResponse(
        reply=reply_clean,
        session_id=body.session_id,
        suggested_actions=suggested,
        confidence=result_confidence,
        resolution_step=result_resolution_step,
        order_data=result_order_data,
        customer_name=body.customer_name,
        action_result=result_action_result,
        resolution_complete=result_resolution_complete,
    )


@router.post("/widget/chat")
async def chat(request: Request, body: ChatRequest, stream: Optional[int] = None):
    """Receive a chat message and return the AI agent's reply.

    Pass `?stream=1` to receive newline-delimited JSON activity events
    (`{"type": "status", "stage": ..., "label": ...}`) followed by a final
    `{"type": "result", ...}` frame carrying the same fields as the plain
    ChatResponse below. Used by the widget to show real backend-driven
    activity states (which tool is running) instead of a blank or invented
    loading indicator. The early-exit responses right below (rate limit,
    plan limits) are always plain JSON regardless of `stream` — only the
    slow agent-processing path actually streams, since those short-circuit
    before any tool would run.
    """
    ip = request.client.host if request.client else "unknown"
    if not _allow(ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Please wait a moment.")

    message = _sanitize(body.message)
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    brand = _get_brand(body.brand_id)

    ticket = _get_or_create_session(
        body.session_id, body.brand_id,
        customer_email=body.customer_email,
        customer_name=body.customer_name,
    )
    ticket_id = ticket.get("id")

    from src.services.auth_service import auth_service
    from src.services import plan_service
    tenant_id = brand.get("tenant_id")

    # ── AI entitlement (trial/subscription gate) — checked as early as
    # possible, right after the tenant is known and before any other
    # Supabase/AI work. An expired trial with no active paid plan (or a
    # lapsed paid plan) must never reach the model, mirroring the same gate
    # in message_processor.py's Gmail/webform/WhatsApp path — see
    # plan_service.check_ai_entitlement's docstring for why this is a
    # different, stricter question than the AI-reply *quota* check below. ──
    entitlement = plan_service.check_ai_entitlement(tenant_id)
    if not entitlement["allowed"]:
        supabase_update("tickets", {"id": f"eq.{ticket_id}"}, {
            "status": "requires_human",
            "escalation_reason": (
                "Your free trial has ended. Upgrade to a paid plan to resume AI-powered replies."
                if entitlement["reason"] == "trial_expired"
                else "No active AI plan on this account. Upgrade to enable AI-powered replies."
            ),
        })
        return ChatResponse(
            reply="✨ Luna's free trial has ended.\n\nThe store owner can upgrade to continue AI-powered support.",
            session_id=body.session_id,
            suggested_actions=[],
        )

    # ── Founding cohort daily limit ─────────────────────────────────────────
    if not await auth_service.check_daily_ticket_limit(tenant_id):
        limit_reply = (
            "We've hit today's free ticket limit on this plan — your message has been "
            "saved and our team will follow up. Upgrade anytime to remove this limit."
        )
        supabase_update("tickets", {"id": f"eq.{ticket_id}"}, {"status": "requires_human"})
        return ChatResponse(reply=limit_reply, session_id=body.session_id, suggested_actions=[])

    # ── AI-reply quota (shared with the Gmail channel — same tenant-scoped
    # counter, checked before calling the model so an exhausted quota never
    # spends an API call) ───────────────────────────────────────────────────
    ai_limit_check = plan_service.check_limit(tenant_id, "ai_replies")
    if not ai_limit_check["allowed"]:
        supabase_update("tickets", {"id": f"eq.{ticket_id}"}, {"status": "requires_human"})
        return ChatResponse(
            reply="✨ Luna's free trial replies have been used.\n\nThe store owner can upgrade to continue AI-powered support.",
            session_id=body.session_id,
            suggested_actions=[],
        )

    if stream != 1:
        return await _generate_reply(body, brand, ticket, ticket_id, tenant_id)

    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()

        async def on_progress(stage: str, label: str) -> None:
            await queue.put({"type": "status", "stage": stage, "label": label})

        async def run() -> None:
            try:
                resp = await _generate_reply(body, brand, ticket, ticket_id, tenant_id, on_progress=on_progress)
                await queue.put({"type": "result", **resp.model_dump()})
            except Exception as e:
                # Generic message only — never leak stack traces, prompts,
                # or tool arguments to the customer. Real detail goes to
                # the log line, not the wire.
                logger.error(f"[ChatWidget] Streaming error: {e}")
                await queue.put({
                    "type": "error",
                    "message": "Sorry, something went wrong on our end. Please try again.",
                })
            finally:
                await queue.put(None)

        task = asyncio.create_task(run())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield (json.dumps(item) + "\n").encode("utf-8")
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@router.post("/widget/feedback")
async def submit_feedback(body: FeedbackRequest):
    """Store thumbs-up / thumbs-down rating for a chat session."""
    if body.rating not in ("positive", "negative"):
        raise HTTPException(status_code=400, detail="rating must be 'positive' or 'negative'")

    try:
        supabase_insert("chat_feedback", {
            "session_id": body.session_id,
            "rating":     body.rating,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        logger.warning(f"[ChatWidget] Feedback insert failed (non-blocking): {e}")

    return {"success": True}


@router.get("/widget/chat/{session_id}")
async def get_chat_history(session_id: str):
    """Return the full conversation history for a session (for restore on reload)."""
    tickets = supabase_select("tickets", {"gmail_thread_id": f"eq.{session_id}", "channel": "eq.chat"})
    if not tickets:
        return {"session_id": session_id, "messages": [], "customer_email": None}

    ticket = tickets[0]
    msgs = ticket.get("messages") or []
    if isinstance(msgs, str):
        import json
        try:
            msgs = json.loads(msgs)
        except Exception:
            msgs = []

    return {
        "session_id": session_id,
        "messages": [
            {
                "role":       "user" if m.get("direction") == "inbound" else "assistant",
                "content":    m.get("body", ""),
                "created_at": m.get("created_at", ""),
            }
            for m in msgs
        ],
        "customer_email": ticket.get("customer_email"),
    }


@router.post("/widget/chat/{session_id}/email")
async def update_session_email(session_id: str, body: EmailCaptureRequest):
    """Attach customer email to a chat session."""
    email = _sanitize(body.email, 254)
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email")

    tickets = supabase_select("tickets", {"gmail_thread_id": f"eq.{session_id}", "channel": "eq.chat"})
    if not tickets:
        raise HTTPException(status_code=404, detail="Session not found")

    supabase_update("tickets", {"id": f"eq.{tickets[0]['id']}"}, {
        "customer_email": email,
        "updated_at":     datetime.now(timezone.utc).isoformat(),
    })
    return {"success": True}
