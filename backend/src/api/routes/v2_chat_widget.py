"""
Chat Widget API
===============
Public endpoints for the embeddable chat widget.
No tenant auth — brand_id (UUID) in request identifies the brand.
Sessions are stored as tickets with channel='chat'.
"""
import re
import os
import hmac
import hashlib
import time
import uuid
import json
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional, List, Callable, Awaitable

from fastapi import APIRouter, HTTPException, Request, Form
from fastapi.responses import PlainTextResponse, StreamingResponse, HTMLResponse
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
    feedback_text: Optional[str] = None


def _map_resolution_step(intent: Optional[str], status: Optional[str]) -> str:
    """Map agent intent/status to widget resolution_step."""
    if status == "auto_resolved":
        if intent in ("refund_request", "return_request", "exchange_request", "cancellation_request", "address_change"):
            return "verifying"
        return "resolved"
    if intent in ("refund_request", "return_request", "exchange_request", "cancellation_request", "address_change"):
        return "acting"
    if intent in ("order_status_inquiry", "shipping_inquiry"):
        return "gathering"
    return "understanding"


def _map_action_result(intent: Optional[str], action_taken: Optional[dict], order_data: Optional[dict]) -> Optional[dict]:
    """Build action_result dict from the REAL staged-action outcome
    (customer_success_agent's "action_taken", sourced from
    return_actions_integration._create_action's actual Supabase insert) —
    never from the LLM's self-reported intent/status. A customer must not be
    told a refund/return/exchange/cancel/address-change request was staged
    unless a pending action row was actually created for it."""
    if not action_taken or not action_taken.get("success"):
        return None
    order_number = (order_data or {}).get("orderNumber", "")
    action_id = action_taken.get("action_id")
    if intent in ("refund_request", "return_request"):
        amount = None
        if order_data:
            items = order_data.get("items", [])
            if items:
                amount = items[0].get("price")
        return {"type": "refund_staged", "amount": amount, "order_number": order_number, "action_id": action_id}
    if intent == "exchange_request":
        return {"type": "exchange_staged", "order_number": order_number, "action_id": action_id}
    if intent == "cancellation_request":
        return {"type": "cancel_staged", "order_number": order_number, "action_id": action_id}
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
            # No placeholder like "there" here - customer_success_agent.py's
            # _known_customer_name() treats a missing/placeholder name as
            # "not known" and uses a neutral greeting instead of guessing.
            "name": body.customer_name or ticket.get("customer_name"),
            "email": body.customer_email or ticket.get("customer_email") or "",
            "channel": "chat",
        }

        # Chat-mode formatting (short replies, no bullets) is driven by
        # customer_info["channel"]="chat" inside the prompt builder now, not
        # a text prefix on the query - that prefix used to leak verbatim
        # into anything that persists `query` (e.g. actions.original_message,
        # shown to merchants on the Escalations page as the customer's own
        # words).
        result = await customer_success_agent.process_customer_query(
            query=full_query,
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

    # ── Human takeover gate — checked fresh on every request (never cached),
    # right before generation, so a merchant who took over mid-conversation
    # can never have this turn overridden by a reply the AI started composing
    # before the takeover. Same authoritative signal (conversation_overrides)
    # the email channel already gates on in message_processor.py; the chat
    # widget previously had no equivalent check at all. ─────────────────────
    from src.services.supabase_service import supabase_service
    try:
        is_overridden = await supabase_service.check_conversation_override(ticket_id)
    except Exception as e:
        # Fail CLOSED, not open: if we can't confirm a human hasn't taken
        # over, don't gamble the customer's conversation on the AI. Costs
        # one turn of "team is looking into this" during a transient outage
        # rather than risking an AI reply landing on a human-owned ticket.
        logger.warning(f"[ChatWidget] Takeover check failed for ticket {ticket_id} ({e}) — failing closed")
        is_overridden = True
    if ticket.get("status") == "human_managing" or is_overridden:
        return ChatResponse(
            reply="A member of our team is already looking into this for you and will respond shortly.",
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
    """Store thumbs-up / thumbs-down rating (with optional written comment)
    for a chat session, enriched with ticket_id/brand_id so a merchant can
    see their own feedback with proper tenant scoping."""
    if body.rating not in ("positive", "negative"):
        raise HTTPException(status_code=400, detail="rating must be 'positive' or 'negative'")

    feedback_text = _sanitize(body.feedback_text, max_len=1000) if body.feedback_text else None

    try:
        tickets = supabase_select("tickets", {"gmail_thread_id": f"eq.{body.session_id}", "channel": "eq.chat"})
        ticket = tickets[0] if tickets else None

        supabase_insert("chat_feedback", {
            "session_id":    body.session_id,
            "rating":        body.rating,
            "feedback_text": feedback_text,
            "ticket_id":     ticket.get("id") if ticket else None,
            "brand_id":      ticket.get("brand_id") if ticket else None,
            "created_at":    datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        logger.warning(f"[ChatWidget] Feedback insert failed (non-blocking): {e}")

    return {"success": True}


# ── Post-ticket CSAT email: tap-a-star rating ────────────────────────────
# Each star in the CSAT email (see email_poller.py's _send_csat_surveys) is
# its own link straight to this GET endpoint - clicking star N means "rate
# N stars", recorded immediately into the same chat_feedback table the chat
# widget's thumbs rating uses. A signed token (HMAC over ticket_id+stars,
# reusing the same SECRET_KEY/JWT_SECRET convention as brand_gmail_service.py
# and shopify_oauth.py) stops a link for one ticket/rating being replayed
# for another - no new secret, no DB-stored token needed.

def _star_signing_key() -> bytes:
    return os.getenv("SECRET_KEY", os.getenv("JWT_SECRET", "change-me-in-production")).encode()


def sign_star_rating(ticket_id: str, stars: int) -> str:
    return hmac.new(_star_signing_key(), f"{ticket_id}:{stars}".encode(), hashlib.sha256).hexdigest()[:20]


def _verify_star_rating_token(ticket_id: str, stars: int, token: str) -> bool:
    return hmac.compare_digest(sign_star_rating(ticket_id, stars), token or "")


def star_rating_email_url(ticket_id: str, stars: int) -> str:
    base_url = os.getenv("API_BASE_URL", "http://localhost:8001")
    token = sign_star_rating(ticket_id, stars)
    return f"{base_url}/api/v2/widget/feedback/rate?ticket_id={ticket_id}&stars={stars}&token={token}"


_FEEDBACK_PAGE_STYLE = """
  body { margin:0; padding:0; background:#FFFBF5; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif; }
  .wrap { min-height:100vh; display:flex; align-items:center; justify-content:center; padding:24px; box-sizing:border-box; }
  .card { max-width:420px; width:100%; background:#ffffff; border-radius:16px; padding:40px 32px; text-align:center; box-shadow:0 1px 2px rgba(15,23,42,0.04), 0 8px 24px rgba(15,23,42,0.06); }
  .emoji { font-size:40px; line-height:1; margin-bottom:14px; }
  h1 { font-size:19px; font-weight:700; color:#1F2937; margin:0 0 8px; }
  p { font-size:14px; color:#6B7280; line-height:1.6; margin:0 0 20px; }
  textarea { width:100%; box-sizing:border-box; border:1px solid #E5E7EB; border-radius:10px; padding:12px; font-size:14px; font-family:inherit; resize:vertical; min-height:80px; margin-bottom:14px; }
  button { background:#F59E0B; color:#ffffff; border:none; border-radius:10px; padding:11px 22px; font-size:14px; font-weight:600; cursor:pointer; }
  .label { font-size:13px; font-weight:600; color:#374151; margin-bottom:8px; text-align:left; }
"""


def _feedback_page(title: str, emoji: str, heading: str, body_html: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title><style>{_FEEDBACK_PAGE_STYLE}</style></head>
<body><div class="wrap"><div class="card">
<div class="emoji">{emoji}</div><h1>{heading}</h1>{body_html}
</div></div></body></html>"""


def _render_star_thanks_page(stars: int, ticket_id: str, token: str) -> str:
    stars_display = "⭐" * stars
    warm = stars >= 4
    body = f"""
<p>You gave us {stars_display} — thank you for taking a moment to let us know!</p>
<form method="POST" action="/api/v2/widget/feedback/comment">
  <input type="hidden" name="ticket_id" value="{ticket_id}">
  <input type="hidden" name="token" value="{token}">
  <div class="label">Want to tell us more? (optional)</div>
  <textarea name="comment" maxlength="1000" placeholder="Tell us more…"></textarea>
  <button type="submit">Leave feedback</button>
</form>"""
    return _feedback_page("Thanks for rating us!", "💛" if warm else "🙏", "How did we do?", body)


def _render_comment_thanks_page() -> str:
    return _feedback_page("Thanks!", "✅", "Feedback received", "<p>Got it — we really appreciate you taking the time.</p>")


def _render_invalid_feedback_link_page() -> str:
    return _feedback_page("Feedback link invalid", "🤔", "Hmm, that link didn't work",
                           "<p>This feedback link is invalid or has expired.</p>")


@router.get("/widget/feedback/rate", response_class=HTMLResponse)
async def rate_via_email(ticket_id: str, stars: int, token: str):
    """Landing page for a tapped star in the CSAT email. Records the rating
    immediately (idempotent — re-clicking the same or a different star for
    the same ticket updates the one row rather than creating duplicates),
    then offers the same optional-comment step described in the product
    brief, never a second rating prompt."""
    if not (1 <= stars <= 5) or not _verify_star_rating_token(ticket_id, stars, token):
        return HTMLResponse(_render_invalid_feedback_link_page(), status_code=400)

    tickets = supabase_select("tickets", {"id": f"eq.{ticket_id}"})
    if not tickets:
        return HTMLResponse(_render_invalid_feedback_link_page(), status_code=404)
    ticket = tickets[0]
    brand_id = ticket.get("brand_id") or ticket.get("store_id")
    rating = "positive" if stars >= 4 else "negative"

    try:
        existing = supabase_select("chat_feedback", {"ticket_id": f"eq.{ticket_id}", "limit": "1"})
        if existing:
            supabase_update("chat_feedback", {"id": f"eq.{existing[0]['id']}"}, {"rating": rating, "rating_stars": stars})
        else:
            supabase_insert("chat_feedback", {
                "session_id":   ticket_id,
                "ticket_id":    ticket_id,
                "brand_id":     brand_id,
                "rating":       rating,
                "rating_stars": stars,
                "created_at":   datetime.now(timezone.utc).isoformat(),
            })
    except Exception as e:
        logger.warning(f"[ChatWidget] Star rating save failed (non-blocking): {e}")

    return HTMLResponse(_render_star_thanks_page(stars, ticket_id, token))


@router.post("/widget/feedback/comment", response_class=HTMLResponse)
async def add_star_rating_comment(ticket_id: str = Form(...), token: str = Form(...), comment: str = Form("")):
    """Optional second step after a star rating — adds a written comment to
    the same chat_feedback row. The token must match the rating actually on
    file (re-derived from the stored rating_stars, not trusted from the
    form) so a comment can't be attached without a real prior rating."""
    existing = supabase_select("chat_feedback", {"ticket_id": f"eq.{ticket_id}", "limit": "1"})
    if not existing or existing[0].get("rating_stars") is None:
        return HTMLResponse(_render_invalid_feedback_link_page(), status_code=404)

    row = existing[0]
    if not _verify_star_rating_token(ticket_id, row["rating_stars"], token):
        return HTMLResponse(_render_invalid_feedback_link_page(), status_code=400)

    text = _sanitize(comment, max_len=1000)
    if text:
        try:
            supabase_update("chat_feedback", {"id": f"eq.{row['id']}"}, {"feedback_text": text})
        except Exception as e:
            logger.warning(f"[ChatWidget] Star rating comment save failed (non-blocking): {e}")

    return HTMLResponse(_render_comment_thanks_page())


# Fields safe to expose to an unauthenticated customer polling their own
# action's outcome - never customer_email/reason/risk_* /ai_reasoning, which
# live on the same `actions` row.
_ACTION_STATUS_FIELDS = {"status", "action_type", "order_number", "error_message"}


@router.get("/widget/actions/{action_id}/status")
async def get_widget_action_status(action_id: str, request: Request):
    """Poll target for the chat widget: once a merchant approves/rejects a
    staged action, this reports the real outcome (executed/failed) recorded
    by actions_service.approve_action() so the widget can show a confirmed
    'Order cancelled' state instead of leaving 'Awaiting merchant approval'
    up forever. Read-only, rate-limited like the rest of this public router."""
    client_ip = request.client.host if request.client else "unknown"
    if not _allow(client_ip):
        raise HTTPException(status_code=429, detail="Too many requests")

    rows = supabase_select("actions", {"id": f"eq.{action_id}"})
    if not rows:
        raise HTTPException(status_code=404, detail="Action not found")

    action = rows[0]
    return {k: action.get(k) for k in _ACTION_STATUS_FIELDS}


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
