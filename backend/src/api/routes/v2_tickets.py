"""
Tickets API Routes (v2)
=======================
Multi-tenant ticket management with brand scoping.
"""

import logging
from typing import Optional, List
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field

from src.api.middleware.auth_middleware import (
    AuthenticatedContext,
    get_current_user,
    require_agent_or_admin,
    require_brand_access
)
from src.services.supabase_auth_service import UserRole
from src.lib.supabase_client import supabase_select, supabase_insert, supabase_update

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tickets", tags=["Tickets"])


# ==================== Request/Response Models ====================

class CreateTicketRequest(BaseModel):
    brand_id: str
    customer_email: str = Field(..., min_length=1)
    customer_name: Optional[str] = None
    subject: Optional[str] = None
    message: str = Field(..., min_length=1)
    channel: str = "web_form"
    priority: str = "normal"
    order_id: Optional[str] = None
    order_number: Optional[str] = None


class UpdateTicketRequest(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    assigned_to: Optional[str] = None
    human_response: Optional[str] = None
    tags: Optional[List[str]] = None


class BulkEscalationCloseRequest(BaseModel):
    ticket_ids: Optional[List[str]] = None
    close_all: bool = False


class RespondToTicketRequest(BaseModel):
    response: str = Field(..., min_length=1)
    send_to_customer: bool = True
    response_method: str = "email"


class TicketResponse(BaseModel):
    id: str
    brand_id: str
    customer_email: str
    customer_name: Optional[str] = None
    subject: Optional[str] = None
    message: str
    channel: str
    status: str
    priority: str
    ai_response: Optional[str] = None
    ai_confidence: Optional[float] = None
    ai_sentiment: Optional[str] = None
    human_response: Optional[str] = None
    human_approved: Optional[bool] = None
    response_sent: bool = False
    created_at: str


# ==================== Routes ====================

@router.get("")
async def list_tickets(
    context: AuthenticatedContext = Depends(get_current_user),
    brand_id: Optional[str] = Query(None, description="Filter by brand"),
    status: Optional[str] = Query(None, description="Filter by status"),
    channel: Optional[str] = Query(None, description="Filter by channel"),
    priority: Optional[str] = Query(None, description="Filter by priority"),
    assigned_to: Optional[str] = Query(None, description="Filter by assignee"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
):
    """
    List tickets accessible to the user.

    Filters:
    - brand_id: Filter by specific brand
    - status: open, pending, ai_responded, human_responded, resolved, escalated, closed
    - channel: email, web_form, whatsapp, chat
    - priority: low, normal, high, urgent
    - assigned_to: User ID
    """
    try:
        # Build base filter for user's brands
        if UserRole.is_admin(context.user.role):
            org_brands = supabase_select("brands", {
                "organization_id": f"eq.{context.user.organization_id}",
                "is_active": "eq.true"
            }) or []
            if not org_brands:
                org_brands = supabase_select("brands", {
                    "tenant_id": f"eq.{context.user.organization_id}",
                    "is_active": "is.true"
                }) or []
            brand_ids = [b["id"] for b in org_brands]
        else:
            brand_ids = list(context.brand_ids or [])

        if not brand_ids:
            return {"tickets": [], "count": 0, "total": 0}

        # Apply brand filter
        if brand_id:
            if brand_id not in brand_ids:
                raise HTTPException(status_code=403, detail="Access denied to this brand")
            brand_filter = f"eq.{brand_id}"
        else:
            brand_filter = f"in.({','.join(brand_ids)})"

        filters = {
            "brand_id": brand_filter,
            "order": "created_at.desc",
            "limit": str(limit),
            "offset": str(offset)
        }

        # Apply additional filters
        if status:
            filters["status"] = f"eq.{status}"
        if channel:
            filters["channel"] = f"eq.{channel}"
        if priority:
            filters["priority"] = f"eq.{priority}"
        if assigned_to:
            filters["assigned_to"] = f"eq.{assigned_to}"

        tickets = supabase_select("tickets", filters)

        # Get total count (without pagination)
        count_filters = {k: v for k, v in filters.items() if k not in ["limit", "offset", "order"]}
        all_tickets = supabase_select("tickets", count_filters)
        total = len(all_tickets) if all_tickets else 0

        return {
            "tickets": tickets or [],
            "count": len(tickets) if tickets else 0,
            "total": total,
            "limit": limit,
            "offset": offset
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing tickets: {e}")
        raise HTTPException(status_code=500, detail="Failed to list tickets")


@router.get("/stats")
async def get_ticket_stats(
    context: AuthenticatedContext = Depends(get_current_user),
    brand_id: Optional[str] = Query(None, description="Filter by brand")
):
    """Get ticket statistics"""
    try:
        # Get user's brands
        if UserRole.is_admin(context.user.role):
            org_brands = supabase_select("brands", {
                "organization_id": f"eq.{context.user.organization_id}",
                "is_active": "eq.true"
            })
            brand_ids = [b["id"] for b in org_brands] if org_brands else []
        else:
            brand_ids = context.brand_ids

        if brand_id:
            if brand_id not in brand_ids:
                raise HTTPException(status_code=403, detail="Access denied")
            brand_ids = [brand_id]

        if not brand_ids:
            return {"stats": {}}

        brand_filter = f"in.({','.join(brand_ids)})"

        # Get counts by status
        all_tickets = supabase_select("tickets", {"brand_id": brand_filter})
        tickets = all_tickets or []

        stats = {
            "total": len(tickets),
            "by_status": {},
            "by_channel": {},
            "by_priority": {},
            "ai_metrics": {
                "ai_responded": 0,
                "avg_confidence": None,
                "sentiment": {"positive": 0, "neutral": 0, "negative": 0}
            }
        }

        confidences = []
        for t in tickets:
            # By status
            status = t.get("status", "unknown")
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1

            # By channel
            channel = t.get("channel", "unknown")
            stats["by_channel"][channel] = stats["by_channel"].get(channel, 0) + 1

            # By priority
            priority = t.get("priority", "normal")
            stats["by_priority"][priority] = stats["by_priority"].get(priority, 0) + 1

            # AI metrics
            if t.get("ai_response"):
                stats["ai_metrics"]["ai_responded"] += 1
                if t.get("ai_confidence"):
                    confidences.append(t["ai_confidence"])
                sentiment = t.get("ai_sentiment", "neutral")
                if sentiment in stats["ai_metrics"]["sentiment"]:
                    stats["ai_metrics"]["sentiment"][sentiment] += 1

        if confidences:
            stats["ai_metrics"]["avg_confidence"] = sum(confidences) / len(confidences)

        return {"stats": stats}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting ticket stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to get stats")


@router.post("/bulk-escalation-close")
async def bulk_close_escalations_route(
    request: BulkEscalationCloseRequest,
    context: AuthenticatedContext = Depends(require_agent_or_admin)
):
    """Mark multiple escalated tickets as resolved, or clear all escalations."""
    try:
        if UserRole.is_admin(context.user.role):
            org_brands = supabase_select("brands", {
                "organization_id": f"eq.{context.user.organization_id}",
                "is_active": "eq.true"
            }) or []
            if not org_brands:
                # v1 accounts: brands use tenant_id; skip is_active filter to handle NULL values
                org_brands = supabase_select("brands", {
                    "tenant_id": f"eq.{context.user.organization_id}",
                }) or []
            brand_ids = [b["id"] for b in org_brands]
        else:
            brand_ids = list(context.brand_ids or [])
            if not brand_ids:
                fallback = supabase_select("brands", {
                    "tenant_id": f"eq.{context.user.organization_id}",
                }) or []
                brand_ids = [b["id"] for b in fallback]

        if not brand_ids:
            logger.warning(f"[bulk-escalation-close] No brands found for user {context.user.email}")
            return {"success": True, "closed": 0}

        brand_filter = f"in.({','.join(brand_ids)})"
        now = datetime.now(timezone.utc).isoformat()

        if request.close_all:
            escalated = supabase_select("tickets", {
                "brand_id": brand_filter,
                "status": "eq.escalated",
            }) or []
            ids_to_close = [t["id"] for t in escalated]
        elif request.ticket_ids:
            ids_to_close = request.ticket_ids
        else:
            return {"success": False, "error": "Provide ticket_ids or close_all=true"}

        closed = 0
        for ticket_id in ids_to_close:
            try:
                rows = supabase_select("tickets", {"id": f"eq.{ticket_id}"})
                if not rows:
                    continue
                ticket = rows[0]
                if ticket.get("brand_id") not in brand_ids:
                    continue
                supabase_update("tickets", {"id": f"eq.{ticket_id}"}, {
                    "status": "resolved",
                    "resolved_at": now,
                })
                closed += 1
            except Exception as item_err:
                logger.warning(f"[bulk-escalation-close] Failed to close {ticket_id}: {item_err}")

        logger.info(f"[bulk-escalation-close] {closed} escalations resolved by {context.user.email}")
        return {"success": True, "closed": closed}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in bulk escalation close: {e}")
        raise HTTPException(status_code=500, detail="Failed to close escalations")


@router.get("/{ticket_id}")
async def get_ticket(
    ticket_id: str,
    context: AuthenticatedContext = Depends(get_current_user)
):
    """Get a specific ticket"""
    try:
        tickets = supabase_select("tickets", {"id": f"eq.{ticket_id}"})

        if not tickets:
            raise HTTPException(status_code=404, detail="Ticket not found")

        ticket = tickets[0]

        # Verify access
        if ticket["brand_id"] not in context.brand_ids:
            if not UserRole.is_admin(context.user.role):
                raise HTTPException(status_code=403, detail="Access denied")

            # Admin check - verify brand is in their org
            brands = supabase_select("brands", {
                "id": f"eq.{ticket['brand_id']}",
                "organization_id": f"eq.{context.user.organization_id}"
            })
            if not brands:
                raise HTTPException(status_code=403, detail="Access denied")

        # Get related actions
        actions = supabase_select("actions", {
            "ticket_id": f"eq.{ticket_id}",
            "order": "created_at.desc"
        })
        ticket["actions"] = actions or []

        # Get AI conversation history
        conversations = supabase_select("ai_conversations", {
            "ticket_id": f"eq.{ticket_id}",
            "order": "created_at.asc"
        })
        ticket["ai_history"] = conversations or []

        return {"ticket": ticket}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting ticket: {e}")
        raise HTTPException(status_code=500, detail="Failed to get ticket")


@router.post("")
async def create_ticket(
    request: CreateTicketRequest,
    context: AuthenticatedContext = Depends(require_agent_or_admin)
):
    """Create a new ticket manually"""
    try:
        # Verify brand access
        if request.brand_id not in context.brand_ids:
            if not UserRole.is_admin(context.user.role):
                raise HTTPException(status_code=403, detail="Access denied to this brand")

        ticket_data = {
            "brand_id": request.brand_id,
            "customer_email": request.customer_email,
            "customer_name": request.customer_name,
            "subject": request.subject,
            "message": request.message,
            "channel": request.channel,
            "status": "open",
            "priority": request.priority,
            "order_id": request.order_id,
            "order_number": request.order_number
        }

        result = supabase_insert("tickets", ticket_data)

        logger.info(f"Ticket created manually: {result['id']} by {context.user.email}")

        return {
            "success": True,
            "ticket": result,
            "message": "Ticket created successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating ticket: {e}")
        raise HTTPException(status_code=500, detail="Failed to create ticket")


@router.patch("/{ticket_id}")
async def update_ticket(
    ticket_id: str,
    request: UpdateTicketRequest,
    context: AuthenticatedContext = Depends(require_agent_or_admin)
):
    """Update ticket fields"""
    try:
        # Get ticket and verify access
        tickets = supabase_select("tickets", {"id": f"eq.{ticket_id}"})
        if not tickets:
            raise HTTPException(status_code=404, detail="Ticket not found")

        ticket = tickets[0]

        # Verify brand access
        if ticket["brand_id"] not in context.brand_ids:
            if not UserRole.is_admin(context.user.role):
                raise HTTPException(status_code=403, detail="Access denied")

        updates = request.model_dump(exclude_none=True)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        result = supabase_update("tickets", {"id": f"eq.{ticket_id}"}, updates)

        logger.info(f"Ticket updated: {ticket_id} by {context.user.email}")

        return {
            "success": True,
            "ticket": result[0] if result else None,
            "message": "Ticket updated successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating ticket: {e}")
        raise HTTPException(status_code=500, detail="Failed to update ticket")


@router.post("/{ticket_id}/respond")
async def respond_to_ticket(
    ticket_id: str,
    request: RespondToTicketRequest,
    context: AuthenticatedContext = Depends(require_agent_or_admin)
):
    """Send a human response to a ticket"""
    try:
        # Get ticket and verify access
        tickets = supabase_select("tickets", {"id": f"eq.{ticket_id}"})
        if not tickets:
            raise HTTPException(status_code=404, detail="Ticket not found")

        ticket = tickets[0]

        # Verify brand access
        if ticket["brand_id"] not in context.brand_ids:
            if not UserRole.is_admin(context.user.role):
                raise HTTPException(status_code=403, detail="Access denied")

        # Update ticket with response
        updates = {
            "human_response": request.response,
            "human_approved": True,
            "human_approved_by": context.user.user_id,
            "human_approved_at": datetime.now(timezone.utc).isoformat(),
            "status": "human_responded"
        }

        # If sending to customer
        if request.send_to_customer:
            # Get brand for email sending
            brands = supabase_select("brands", {"id": f"eq.{ticket['brand_id']}"})
            if brands:
                brand = brands[0]

                # Send email response
                try:
                    from production.channels.gmail_handler import gmail_handler
                    await gmail_handler.send_reply(
                        to_email=ticket["customer_email"],
                        subject=f"Re: {ticket.get('subject', 'Your Support Request')}",
                        body=request.response
                    )
                    updates["response_sent"] = True
                    updates["response_sent_at"] = datetime.now(timezone.utc).isoformat()
                    updates["response_method"] = request.response_method

                except Exception as email_error:
                    logger.error(f"Failed to send email: {email_error}")
                    # Don't fail the whole request

        result = supabase_update("tickets", {"id": f"eq.{ticket_id}"}, updates)

        # Log AI conversation
        supabase_insert("ai_conversations", {
            "brand_id": ticket["brand_id"],
            "ticket_id": ticket_id,
            "role": "assistant",
            "content": request.response,
            "metadata": {"source": "human", "user_id": context.user.user_id}
        })

        logger.info(f"Human response sent: {ticket_id} by {context.user.email}")

        return {
            "success": True,
            "ticket": result[0] if result else None,
            "email_sent": updates.get("response_sent", False),
            "message": "Response sent successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error responding to ticket: {e}")
        raise HTTPException(status_code=500, detail="Failed to send response")


@router.post("/{ticket_id}/approve-ai")
async def approve_ai_response(
    ticket_id: str,
    context: AuthenticatedContext = Depends(require_agent_or_admin)
):
    """Approve and send the AI-generated response"""
    try:
        # Get ticket and verify access
        tickets = supabase_select("tickets", {"id": f"eq.{ticket_id}"})
        if not tickets:
            raise HTTPException(status_code=404, detail="Ticket not found")

        ticket = tickets[0]

        # Verify brand access
        if ticket["brand_id"] not in context.brand_ids:
            if not UserRole.is_admin(context.user.role):
                raise HTTPException(status_code=403, detail="Access denied")

        # Check if AI response exists (fall back to ai_draft)
        reply_body = ticket.get("ai_response") or ticket.get("ai_draft")
        if not reply_body:
            raise HTTPException(status_code=400, detail="No AI response to approve")

        # Update ticket as approved
        updates = {
            "human_approved": True,
            "human_approved_by": context.user.user_id,
            "human_approved_at": datetime.now(timezone.utc).isoformat(),
            "status": "human_responded"
        }

        # Send via per-brand Gmail
        brand_id = ticket.get("brand_id") or ticket.get("store_id")
        email_sent = False
        if brand_id:
            try:
                from src.services.brand_gmail_service import brand_gmail_service
                brands = supabase_select("brands", {
                    "id": f"eq.{brand_id}",
                    "gmail_connected": "is.true"
                })
                if brands:
                    subject = ticket.get("subject", "Your Support Request")
                    reply_subject = f"Re: {subject}" if not subject.startswith("Re:") else subject
                    send_result = await brand_gmail_service.send_email(
                        brands[0],
                        ticket["customer_email"],
                        reply_subject,
                        reply_body
                    )
                    if send_result.get("success"):
                        email_sent = True
                        updates["response_sent"] = True
                        updates["response_sent_at"] = datetime.now(timezone.utc).isoformat()
                        updates["response_method"] = "email"
                        updates["status"] = "resolved"
                        updates["email_sent"] = True
                        # Append sent message to thread so conversation replay shows it
                        existing_msgs = list(ticket.get("messages") or [])
                        existing_msgs.append({
                            "from": "AI Agent",
                            "body": reply_body,
                            "sent_at": datetime.now(timezone.utc).isoformat(),
                            "direction": "outbound",
                        })
                        updates["messages"] = existing_msgs
                    else:
                        logger.warning(f"[v2_tickets] Gmail send failed: {send_result.get('error')}")
                else:
                    logger.warning(f"[v2_tickets] No Gmail-connected brand found for brand_id={brand_id}")
            except Exception as email_error:
                logger.error(f"[v2_tickets] Email send error: {email_error}")

        result = supabase_update("tickets", {"id": f"eq.{ticket_id}"}, updates)

        logger.info(f"AI response approved: {ticket_id} by {context.user.email}, email_sent={email_sent}")

        if not email_sent:
            return {
                "success": False,
                "error": "AI response approved but email could not be sent. Check Gmail connection in Brands settings.",
                "email_sent": False
            }

        return {
            "success": True,
            "ticket": result[0] if result else None,
            "email_sent": True,
            "message": "AI response approved and sent"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error approving AI response: {e}")
        raise HTTPException(status_code=500, detail="Failed to approve AI response")


@router.get("/{ticket_id}/order")
async def get_ticket_order(
    ticket_id: str,
    context: AuthenticatedContext = Depends(get_current_user)
):
    """Get Shopify order data for a ticket (if an order number was detected)."""
    try:
        tickets = supabase_select("tickets", {"id": f"eq.{ticket_id}"})
        if not tickets:
            raise HTTPException(status_code=404, detail="Ticket not found")

        ticket = tickets[0]

        order_number = ticket.get("detected_order_number") or ticket.get("detected_order_id")
        if not order_number:
            return {"order": None, "message": "No order number detected for this ticket"}

        brand_id = ticket.get("brand_id") or ticket.get("store_id")
        if not brand_id:
            return {"order": None, "message": "No brand associated with this ticket"}

        brands = supabase_select("brands", {
            "id": f"eq.{brand_id}",
            "shopify_connected": "is.true",
        })
        if not brands:
            return {"order": None, "message": "Shopify not connected for this brand"}

        from src.services.shopify_service import fetch_shopify_order
        order_data = await fetch_shopify_order(brands[0], order_number)

        if not order_data:
            return {"order": None, "message": f"Order #{order_number} not found in Shopify"}

        return {"order": order_data}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching order for ticket {ticket_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch order data")


@router.post("/{ticket_id}/actions/cancel")
async def execute_cancel_order(
    ticket_id: str,
    context: AuthenticatedContext = Depends(require_agent_or_admin)
):
    """Cancel the Shopify order attached to this ticket and send confirmation email."""
    try:
        tickets = supabase_select("tickets", {"id": f"eq.{ticket_id}"})
        if not tickets:
            raise HTTPException(status_code=404, detail="Ticket not found")
        ticket = tickets[0]

        brand_id = ticket.get("brand_id") or ticket.get("store_id")
        if brand_id not in context.brand_ids:
            if not UserRole.is_admin(context.user.role):
                raise HTTPException(status_code=403, detail="Access denied")

        brands = supabase_select("brands", {"id": f"eq.{brand_id}"})
        if not brands:
            raise HTTPException(status_code=404, detail="Brand not found")
        brand = brands[0]

        if not brand.get("shopify_connected") or not brand.get("shopify_access_token"):
            raise HTTPException(status_code=400, detail="Shopify not connected for this brand")

        order_identifier = ticket.get("detected_order_id") or ticket.get("detected_order_number")
        if not order_identifier:
            raise HTTPException(status_code=400, detail="No order number detected on this ticket")

        # Execute cancel in Shopify
        try:
            from src.services.shopify_service import ShopifyClient, decrypt_token
            client = ShopifyClient(
                brand["shopify_domain"],
                decrypt_token(brand["shopify_access_token"])
            )
            result = await client.cancel_order(
                order_id=str(order_identifier),
                reason="customer",
                email_customer=False,
                restock=True
            )
        except Exception as shopify_err:
            raise HTTPException(status_code=422, detail=str(shopify_err))

        order_name = result.get("order_name") or f"#{order_identifier}"

        # Build and send confirmation email
        customer_name = (ticket.get("customer_name") or ticket.get("customer_email", "").split("@")[0]).capitalize()
        brand_name = brand.get("name", "our team")
        confirmation = (
            f"Hey {customer_name},\n\n"
            f"Your cancellation request for order {order_name} has been processed.\n\n"
            f"Your order has been successfully cancelled. "
            f"If you paid by card, your refund will appear within 3–5 business days depending on your bank.\n\n"
            f"If you have any other questions, just reply to this email.\n\n"
            f"Thank you for your patience.\n\n"
            f"Luna\n{brand_name}"
        )

        email_sent = False
        try:
            from src.services.brand_gmail_service import brand_gmail_service
            gmail_brands = supabase_select("brands", {
                "id": f"eq.{brand_id}",
                "gmail_connected": "is.true"
            })
            if gmail_brands:
                subject = ticket.get("subject") or "Your order cancellation"
                reply_subject = subject if subject.startswith("Re:") else f"Re: {subject}"
                send_result = await brand_gmail_service.send_email(
                    gmail_brands[0], ticket["customer_email"], reply_subject, confirmation
                )
                email_sent = send_result.get("success", False)
        except Exception as mail_err:
            logger.warning(f"[cancel] Confirmation email failed: {mail_err}")

        # Append confirmation to messages array
        existing = list(ticket.get("messages") or [])
        existing.append({
            "from": "AI Agent",
            "body": confirmation,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "direction": "outbound" if email_sent else "draft",
        })

        supabase_update("tickets", {"id": f"eq.{ticket_id}"}, {
            "status": "resolved",
            "messages": existing,
            "email_sent": email_sent,
        })

        # Mark the pending cancel action as executed
        pending = supabase_select("actions", {
            "ticket_id": f"eq.{ticket_id}",
            "action_type": "eq.cancel_order",
            "status": "eq.pending",
        })
        if pending:
            supabase_update("actions", {"id": f"eq.{pending[0]['id']}"}, {
                "status": "executed",
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "approved_by": context.user.user_id,
            })

        logger.info(f"[cancel] Order {order_name} cancelled, ticket {ticket_id} resolved by {context.user.email}")

        return {
            "success": True,
            "message": f"Order {order_name} cancelled.",
            "email_sent": email_sent,
            "confirmation": confirmation,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[cancel] Error for ticket {ticket_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to cancel order")


@router.post("/{ticket_id}/actions/refund")
async def execute_refund(
    ticket_id: str,
    context: AuthenticatedContext = Depends(require_agent_or_admin)
):
    """Issue a full Shopify refund for the order attached to this ticket and send confirmation."""
    try:
        tickets = supabase_select("tickets", {"id": f"eq.{ticket_id}"})
        if not tickets:
            raise HTTPException(status_code=404, detail="Ticket not found")
        ticket = tickets[0]

        brand_id = ticket.get("brand_id") or ticket.get("store_id")
        if brand_id not in context.brand_ids:
            if not UserRole.is_admin(context.user.role):
                raise HTTPException(status_code=403, detail="Access denied")

        brands = supabase_select("brands", {"id": f"eq.{brand_id}"})
        if not brands:
            raise HTTPException(status_code=404, detail="Brand not found")
        brand = brands[0]

        if not brand.get("shopify_connected") or not brand.get("shopify_access_token"):
            raise HTTPException(status_code=400, detail="Shopify not connected for this brand")

        order_identifier = ticket.get("detected_order_id") or ticket.get("detected_order_number")
        if not order_identifier:
            raise HTTPException(status_code=400, detail="No order number detected on this ticket")

        # Execute refund in Shopify
        try:
            from src.services.shopify_service import ShopifyClient, decrypt_token
            client = ShopifyClient(
                brand["shopify_domain"],
                decrypt_token(brand["shopify_access_token"])
            )
            result = await client.process_refund(
                order_id=str(order_identifier),
                amount=None,  # full refund
                reason="Customer refund request",
                restock=True,
                notify_customer=False,
            )
        except Exception as shopify_err:
            raise HTTPException(status_code=422, detail=str(shopify_err))

        order_name = result.get("order_name") or f"#{order_identifier}"
        refund_amount = result.get("amount", "")
        currency = "PKR"
        try:
            order_rows = supabase_select("tickets", {"id": f"eq.{ticket_id}"})
        except Exception:
            pass
        # Try to get currency from order context already fetched; fall back to brand default
        try:
            from src.services.shopify_service import fetch_shopify_order
            ord_data = await fetch_shopify_order(brand, str(order_identifier))
            if ord_data:
                currency = ord_data.get("currency", "PKR")
        except Exception:
            pass

        # Build confirmation email
        customer_name = (ticket.get("customer_name") or ticket.get("customer_email", "").split("@")[0]).capitalize()
        brand_name = brand.get("name", "our team")
        refund_display = f"{currency} {refund_amount:.2f}" if isinstance(refund_amount, float) else str(refund_amount)
        confirmation = (
            f"Hey {customer_name},\n\n"
            f"Your refund for order {order_name} has been processed.\n\n"
            f"{refund_display} will be returned to your original payment method "
            f"within 3–5 business days, depending on your bank.\n\n"
            f"If you have any questions, just reply to this email.\n\n"
            f"Luna\n{brand_name}"
        )

        email_sent = False
        try:
            from src.services.brand_gmail_service import brand_gmail_service
            gmail_brands = supabase_select("brands", {
                "id": f"eq.{brand_id}",
                "gmail_connected": "is.true"
            })
            if gmail_brands:
                subject = ticket.get("subject") or "Your refund"
                reply_subject = subject if subject.startswith("Re:") else f"Re: {subject}"
                send_result = await brand_gmail_service.send_email(
                    gmail_brands[0], ticket["customer_email"], reply_subject, confirmation
                )
                email_sent = send_result.get("success", False)
        except Exception as mail_err:
            logger.warning(f"[refund] Confirmation email failed: {mail_err}")

        # Append confirmation to messages array
        existing = list(ticket.get("messages") or [])
        existing.append({
            "from": "AI Agent",
            "body": confirmation,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "direction": "outbound" if email_sent else "draft",
        })

        supabase_update("tickets", {"id": f"eq.{ticket_id}"}, {
            "status": "resolved",
            "messages": existing,
            "email_sent": email_sent,
        })

        # Mark the pending refund action as executed
        pending = supabase_select("actions", {
            "ticket_id": f"eq.{ticket_id}",
            "action_type": "eq.refund",
            "status": "eq.pending",
        })
        if pending:
            supabase_update("actions", {"id": f"eq.{pending[0]['id']}"}, {
                "status": "executed",
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "approved_by": context.user.user_id,
            })

        logger.info(f"[refund] {refund_display} refunded, ticket {ticket_id} resolved by {context.user.email}")

        return {
            "success": True,
            "message": f"Refund of {refund_display} issued for order {order_name}.",
            "email_sent": email_sent,
            "confirmation": confirmation,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[refund] Error for ticket {ticket_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to process refund")


@router.post("/{ticket_id}/escalate")
async def escalate_ticket(
    ticket_id: str,
    reason: str = Query(..., min_length=1),
    context: AuthenticatedContext = Depends(require_agent_or_admin)
):
    """Escalate a ticket for further review"""
    try:
        # Get ticket and verify access
        tickets = supabase_select("tickets", {"id": f"eq.{ticket_id}"})
        if not tickets:
            raise HTTPException(status_code=404, detail="Ticket not found")

        ticket = tickets[0]

        # Verify brand access
        if ticket["brand_id"] not in context.brand_ids:
            if not UserRole.is_admin(context.user.role):
                raise HTTPException(status_code=403, detail="Access denied")

        # Update ticket
        current_metadata = ticket.get("metadata", {}) or {}
        current_metadata["escalation_reason"] = reason
        current_metadata["escalated_by"] = context.user.user_id
        current_metadata["escalated_at"] = datetime.now(timezone.utc).isoformat()

        result = supabase_update("tickets", {"id": f"eq.{ticket_id}"}, {
            "status": "escalated",
            "priority": "high",
            "metadata": current_metadata
        })

        logger.info(f"Ticket escalated: {ticket_id} by {context.user.email}")

        return {
            "success": True,
            "ticket": result[0] if result else None,
            "message": "Ticket escalated"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error escalating ticket: {e}")
        raise HTTPException(status_code=500, detail="Failed to escalate ticket")


@router.post("/{ticket_id}/close")
async def close_ticket(
    ticket_id: str,
    resolution: Optional[str] = Query(None),
    context: AuthenticatedContext = Depends(require_agent_or_admin)
):
    """Close a resolved ticket"""
    try:
        # Get ticket and verify access
        tickets = supabase_select("tickets", {"id": f"eq.{ticket_id}"})
        if not tickets:
            raise HTTPException(status_code=404, detail="Ticket not found")

        ticket = tickets[0]

        # Verify brand access
        if ticket["brand_id"] not in context.brand_ids:
            if not UserRole.is_admin(context.user.role):
                raise HTTPException(status_code=403, detail="Access denied")

        updates = {
            "status": "closed",
            "resolved_at": datetime.now(timezone.utc).isoformat()
        }

        if resolution:
            current_metadata = ticket.get("metadata", {}) or {}
            current_metadata["resolution"] = resolution
            updates["metadata"] = current_metadata

        result = supabase_update("tickets", {"id": f"eq.{ticket_id}"}, updates)

        logger.info(f"Ticket closed: {ticket_id} by {context.user.email}")

        return {
            "success": True,
            "ticket": result[0] if result else None,
            "message": "Ticket closed"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error closing ticket: {e}")
        raise HTTPException(status_code=500, detail="Failed to close ticket")


