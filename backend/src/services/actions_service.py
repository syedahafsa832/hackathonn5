"""
Actions Service for Multi-Tenant SaaS
=====================================
Handles action detection, creation, approval, execution with strict tenant isolation.
"""
import re
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from enum import Enum

from src.lib.supabase_client import supabase_select, supabase_insert, supabase_update
from src.services.shopify_service import shopify_service, ShopifyError, ShopifyErrorCode

logger = logging.getLogger(__name__)


class ActionType(str, Enum):
    REFUND = "refund"
    CANCEL_ORDER = "cancel_order"
    CHANGE_ADDRESS = "change_address"
    RESHIP = "reship"
    RESTORE_ORDER = "restore_order"


class ActionStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    EXECUTED = "executed"
    REJECTED = "rejected"
    FAILED = "failed"


class ActionDetector:
    """Detects and extracts action requests from customer messages using AI."""

    async def detect_async(self, message: str):
        """AI-based action detection — replaces regex patterns."""
        from src.services.intent_detector import intent_detector, IntentResult
        result = await intent_detector.detect(message)
        if not result.has_action:
            return None

        action_map = {
            "refund": "refund",
            "cancel": "cancel_order",
            "address_change": "change_address",
            "reship": "reship",
            "restore_order": "restore_order",
        }
        action_type = action_map.get(result.action_type)
        if not action_type:
            return None

        confidence = min(0.5 + result.confidence * 0.45, 0.95)
        extracted = {"order_id": result.order_id}
        if result.raw_address:
            extracted["new_address"] = {"raw": result.raw_address}
        return {
            "action_type": action_type,
            "confidence": confidence,
            "extracted_data": extracted,
        }

    def detect(self, message: str):
        """Sync shim — kept for compatibility. Use detect_async in async contexts."""
        import re
        message_lower = message.lower()
        # Broad fragment fallback
        order_data = {"order_id": None}
        m = re.search(r'(?:order\s*#?\s*|#)(\d{3,8})', message, re.IGNORECASE) or re.search(r'\b(\d{4,6})\b', message)
        if m:
            order_data["order_id"] = m.group(1)

        if any(f in message_lower for f in ['address', 'delivery address', 'shipping address']):
            return {"action_type": "change_address", "confidence": 0.7, "extracted_data": order_data}
        if any(f in message_lower for f in ['not received', 'never received', 'missing', 'lost', 'stolen', 'not delivered']):
            return {"action_type": "reship", "confidence": 0.7, "extracted_data": order_data}
        if any(f in message_lower for f in ['cancel', 'no longer want', "don't want"]):
            return {"action_type": "cancel_order", "confidence": 0.7, "extracted_data": order_data}
        if any(f in message_lower for f in ['refund', 'money back', 'return', 'exchange']):
            return {"action_type": "refund", "confidence": 0.7, "extracted_data": order_data}
        return None


class ActionsService:
    """
    Service for managing actions with tenant isolation.
    """

    def __init__(self):
        self.detector = ActionDetector()

    async def create_action(
        self,
        tenant_id: str,
        action_type: str,
        customer_email: str,
        customer_name: str = None,
        order_id: str = None,
        message: str = None,
        extracted_data: Dict = None,
        confidence: float = 0.8,
        ai_reasoning: str = None,
        brand_id: str = None,
        ticket_id: str = None,
    ) -> Dict[str, Any]:
        """
        Create a new pending action for tenant.

        Returns:
            Created action or error
        """
        try:
            # Calculate risk level
            risk_level, risk_factors = await self._calculate_risk(
                tenant_id, action_type, order_id, customer_email
            )

            action_data = {
                "tenant_id": tenant_id,
                "action_type": action_type,
                "status": ActionStatus.PENDING.value,
                "customer_email": customer_email,
                "customer_name": customer_name,
                "order_id": order_id,
                "original_message": message[:1000] if message else None,
                "extracted_data": extracted_data or {},
                "confidence": confidence,
                "risk_level": risk_level,
                "risk_factors": risk_factors,
                "ai_reasoning": ai_reasoning or f"Customer requested {action_type}",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            if ticket_id:
                action_data["ticket_id"] = ticket_id
            if brand_id:
                action_data["brand_id"] = brand_id

            result = supabase_insert("actions", action_data)
            action_id = result.get("id")

            # Log creation
            await self._log_event(tenant_id, action_id, "created", "system", {
                "action_type": action_type,
                "order_id": order_id
            })

            logger.info(f"[Actions] Created {action_type} action {action_id} for tenant {tenant_id}")

            return {
                "success": True,
                "action_id": action_id,
                "action_type": action_type,
                "status": "pending",
                "risk_level": risk_level
            }

        except Exception as e:
            logger.error(f"[Actions] Create error: {e}")
            return {"success": False, "error": str(e)}

    async def detect_and_create(
        self,
        tenant_id: str,
        customer_email: str,
        customer_name: str,
        message: str,
        ai_analysis: Dict = None,
        brand_id: str = None,
        ticket_id: str = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Detect action from message and create if found.
        Deduplicates: skips creation when a pending or recently-executed action already
        exists for the same tenant + action_type + order_id.
        """
        detection = await self.detector.detect_async(message)
        if not detection:
            return None

        # restore_order is handled exclusively by return_actions_integration (primary agent path)
        # which checks restocked status first. Never create it here.
        if detection["action_type"] == "restore_order":
            return None

        order_id = detection["extracted_data"].get("order_id")

        # Don't create an action without an order number — the AI already asks for it.
        if not order_id:
            logger.info(f"[Actions] Skipping action creation — no order_id for {detection['action_type']}")
            return None

        if order_id:
            # Dedup: don't create a second pending action for the same order + type
            try:
                existing = supabase_select("actions", {
                    "tenant_id": f"eq.{tenant_id}",
                    "action_type": f"eq.{detection['action_type']}",
                    "order_id": f"eq.{order_id}",
                    "status": f"eq.{ActionStatus.PENDING.value}",
                })
                if existing:
                    logger.info(
                        f"[Actions] Duplicate skipped — pending {detection['action_type']} "
                        f"for order {order_id} already exists ({existing[0]['id']})"
                    )
                    return {
                        "success": True,
                        "action_id": existing[0]["id"],
                        "action_type": detection["action_type"],
                        "status": "duplicate_skipped",
                    }
            except Exception as dedup_err:
                logger.warning(f"[Actions] Dedup check failed (continuing): {dedup_err}")

            # Also skip if the action was already executed (prevents re-creation after AI reply)
            try:
                executed = supabase_select("actions", {
                    "tenant_id": f"eq.{tenant_id}",
                    "action_type": f"eq.{detection['action_type']}",
                    "order_id": f"eq.{order_id}",
                    "status": f"in.(executed,approved)",
                })
                if executed:
                    logger.info(
                        f"[Actions] Duplicate skipped — {detection['action_type']} for order "
                        f"{order_id} already executed ({executed[0]['id']})"
                    )
                    return {
                        "success": True,
                        "action_id": executed[0]["id"],
                        "action_type": detection["action_type"],
                        "status": "duplicate_skipped",
                    }
            except Exception as exec_dedup_err:
                logger.warning(f"[Actions] Executed dedup check failed (continuing): {exec_dedup_err}")

        return await self.create_action(
            tenant_id=tenant_id,
            action_type=detection["action_type"],
            customer_email=customer_email,
            customer_name=customer_name,
            order_id=order_id,
            message=message,
            extracted_data=detection["extracted_data"],
            confidence=detection["confidence"],
            ai_reasoning=ai_analysis.get("reasoning") if ai_analysis else None,
            brand_id=brand_id,
            ticket_id=ticket_id,
        )

    async def get_pending_actions(
        self,
        tenant_id: str,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get pending actions for tenant."""
        try:
            actions = supabase_select("actions", {
                "tenant_id": f"eq.{tenant_id}",
                "status": f"eq.{ActionStatus.PENDING.value}",
                "order": "created_at.desc",
                "limit": str(limit)
            })
            return actions or []

        except Exception as e:
            logger.error(f"[Actions] Get pending error: {e}")
            return []

    async def get_action_history(
        self,
        tenant_id: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get completed/rejected actions for tenant."""
        try:
            actions = supabase_select("actions", {
                "tenant_id": f"eq.{tenant_id}",
                "status": f"in.(executed,rejected,failed)",
                "order": "updated_at.desc",
                "limit": str(limit)
            })
            return actions or []

        except Exception as e:
            logger.error(f"[Actions] Get history error: {e}")
            return []

    async def get_action(self, tenant_id: str, action_id: str) -> Optional[Dict[str, Any]]:
        """Get a single action (tenant-scoped)."""
        try:
            actions = supabase_select("actions", {
                "id": f"eq.{action_id}",
                "tenant_id": f"eq.{tenant_id}"
            })
            return actions[0] if actions else None

        except Exception as e:
            logger.error(f"[Actions] Get action error: {e}")
            return None

    async def approve_action(
        self,
        tenant_id: str,
        action_id: str,
        approved_by: str = "admin"
    ) -> Dict[str, Any]:
        """
        Approve and execute an action.

        This is the core execution flow:
        1. Validate action belongs to tenant
        2. Get Shopify client
        3. Execute the action
        4. Update status
        5. Log result
        """
        try:
            # Get action (tenant-scoped)
            action = await self.get_action(tenant_id, action_id)
            if not action:
                return {"success": False, "error": "Action not found"}

            if action["status"] != ActionStatus.PENDING.value:
                return {"success": False, "error": f"Action already {action['status']}"}

            # Mark as approved
            supabase_update("actions", {"id": f"eq.{action_id}"}, {
                "status": ActionStatus.APPROVED.value,
                "approved_by": approved_by,
                "approved_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            })

            # Get Shopify client
            try:
                shopify_client = await shopify_service.get_client_for_tenant(tenant_id)
            except ShopifyError as e:
                await self._mark_failed(action_id, e.message, e.error_code)
                return {"success": False, "error": e.message, "error_code": e.error_code}

            # Execute based on type
            order_id = action.get("order_id") or action.get("extracted_data", {}).get("order_id")

            if not order_id:
                await self._mark_failed(action_id, "Order ID is required", "missing_order_id")
                return {"success": False, "error": "Order ID is required for this action"}

            execution_result = None
            action_type = action["action_type"]

            try:
                if action_type == ActionType.REFUND.value:
                    extracted = action.get("extracted_data", {})
                    execution_result = await shopify_client.process_refund(
                        order_id=order_id,
                        amount=extracted.get("amount"),
                        reason=f"Customer request - Action {action_id[:8]}"
                    )

                elif action_type == ActionType.CANCEL_ORDER.value:
                    execution_result = await shopify_client.cancel_order(
                        order_id=order_id,
                        reason="customer"
                    )

                elif action_type == ActionType.CHANGE_ADDRESS.value:
                    extracted_data = action.get("extracted_data", {})
                    new_address = extracted_data.get("new_address", {})
                    if not new_address:
                        # No structured address — team needs to manually update in Shopify admin
                        execution_result = {
                            "success": True,
                            "manual_action_required": True,
                            "message": "Please update the shipping address manually in Shopify admin — see customer message for details.",
                            "order_id": order_id,
                            "order_name": f"#{order_id}",
                            "new_address_text": extracted_data.get("new_address_text"),
                        }
                    else:
                        execution_result = await shopify_client.update_shipping_address(
                            order_id=order_id,
                            new_address=new_address,
                            customer_name=action.get("customer_name")
                        )

                elif action_type == ActionType.RESHIP.value:
                    # Reship is handled manually — team creates replacement shipment in Shopify admin
                    execution_result = {
                        "success": True,
                        "manual_action_required": True,
                        "message": "Please create a replacement shipment in Shopify admin for this order.",
                        "order_id": order_id,
                        "order_name": f"#{order_id}",
                    }

                elif action_type == ActionType.RESTORE_ORDER.value:
                    # Check restocked status in real time, then try Shopify reopen.json
                    order_resp = await shopify_client.get_order(order_id)
                    if not order_resp.get("success") or not order_resp.get("order"):
                        raise ShopifyError(f"Order #{order_id} not found in Shopify.", ShopifyErrorCode.ORDER_NOT_FOUND)
                    order_raw = order_resp["order"]
                    fulfillment_status = order_raw.get("fulfillment_status", "")
                    line_items = order_raw.get("line_items", [])
                    is_restocked = (
                        fulfillment_status == "restocked" or
                        any(item.get("fulfillment_status") == "restocked" for item in line_items)
                    )
                    if is_restocked:
                        raise ShopifyError(
                            "Order inventory has been restocked — this order cannot be restored via Shopify. "
                            "The customer will need to place a new order.",
                            "restore_not_possible"
                        )
                    if not order_raw.get("cancelled_at"):
                        raise ShopifyError("Order is not cancelled — nothing to restore.", ShopifyErrorCode.INVALID_REQUEST)
                    execution_result = await shopify_client.reopen_order(order_id)

                else:
                    await self._mark_failed(action_id, f"Unknown action type: {action_type}", "invalid_action_type")
                    return {"success": False, "error": f"Unknown action type: {action_type}"}

            except ShopifyError as e:
                await self._mark_failed(action_id, e.message, e.error_code)
                await self._log_event(tenant_id, action_id, "api_error", approved_by, {
                    "error": e.message,
                    "error_code": e.error_code
                }, e.error_code, e.message)
                return {
                    "success": False,
                    "error": e.message,
                    "error_code": e.error_code
                }

            # Success - update action status
            supabase_update("actions", {"id": f"eq.{action_id}"}, {
                "status": ActionStatus.EXECUTED.value,
                "execution_result": execution_result,
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            })

            # Log success
            await self._log_event(tenant_id, action_id, "executed", approved_by, execution_result)

            logger.info(f"[Actions] Executed {action_type} action {action_id}")

            # Post-execution: send branded confirmation email + resolve ticket
            await self._post_execution_notify(action, action_type, execution_result)

            return {
                "success": True,
                "message": execution_result.get("message", f"{action_type} completed"),
                "execution_result": execution_result
            }

        except Exception as e:
            logger.error(f"[Actions] Approve error: {e}")
            await self._mark_failed(action_id, str(e), "unknown_error")
            return {"success": False, "error": str(e)}

    async def _post_execution_notify(
        self,
        action: dict,
        action_type: str,
        execution_result: dict,
    ) -> None:
        """
        After a successful Shopify execution:
        1. Send a branded confirmation email via brand Gmail.
        2. Resolve the linked ticket and append the sent message.
        Fails silently — never blocks the approval response.
        """
        try:
            customer_email = action.get("customer_email", "")
            ticket_id = action.get("ticket_id")
            brand_id = action.get("brand_id")

            if not customer_email:
                return

            # Fetch ticket for context (subject, messages, customer_name)
            ticket = None
            if ticket_id:
                rows = supabase_select("tickets", {"id": f"eq.{ticket_id}"})
                ticket = rows[0] if rows else None

            # Fetch brand for Gmail credentials and name — fall back to tenant_id lookup
            if brand_id:
                brand_rows = supabase_select("brands", {
                    "id": f"eq.{brand_id}",
                    "gmail_connected": "is.true",
                })
            else:
                tenant_id_lookup = action.get("tenant_id")
                brand_rows = supabase_select("brands", {
                    "tenant_id": f"eq.{tenant_id_lookup}",
                    "gmail_connected": "is.true",
                }) if tenant_id_lookup else []

            if not brand_rows:
                logger.info(f"[Actions] No Gmail-connected brand (brand_id={brand_id}) — skipping confirmation email")
                return
            brand = brand_rows[0]

            customer_name = (
                action.get("customer_name")
                or (ticket.get("customer_name") if ticket else None)
                or customer_email.split("@")[0]
            ).capitalize()
            brand_name = brand.get("name", "our team")
            order_name = execution_result.get("order_name") or f"your order"

            if action_type == ActionType.CANCEL_ORDER.value:
                body = (
                    f"Hey {customer_name},\n\n"
                    f"Your cancellation request for order {order_name} has been processed.\n\n"
                    f"Your order has been successfully cancelled. "
                    f"If you paid by card, your refund will appear within 3–5 business days depending on your bank.\n\n"
                    f"If you have any other questions, just reply to this email.\n\n"
                    f"Luna\n{brand_name}"
                )
            elif action_type == ActionType.REFUND.value:
                amount = execution_result.get("amount", "")
                amount_str = f"PKR {amount:.2f}" if isinstance(amount, (int, float)) else str(amount)
                body = (
                    f"Hey {customer_name},\n\n"
                    f"Your refund for order {order_name} has been processed.\n\n"
                    f"{amount_str} will be returned to your original payment method "
                    f"within 3–5 business days, depending on your bank.\n\n"
                    f"If you have any questions, just reply to this email.\n\n"
                    f"Luna\n{brand_name}"
                )
            elif action_type == ActionType.CHANGE_ADDRESS.value:
                if execution_result.get("manual_action_required"):
                    body = (
                        f"Hey {customer_name},\n\n"
                        f"We've received your address change request for order {order_name} "
                        f"and our team is updating it right now.\n\n"
                        f"You'll receive a shipping confirmation once the address is updated.\n\n"
                        f"If you have any questions, just reply to this email.\n\n"
                        f"Luna\n{brand_name}"
                    )
                else:
                    body = (
                        f"Hey {customer_name},\n\n"
                        f"Your shipping address has been updated for order {order_name}.\n\n"
                        f"If you have any questions, just reply to this email.\n\n"
                        f"Luna\n{brand_name}"
                    )
            elif action_type == ActionType.RESHIP.value:
                body = (
                    f"Hey {customer_name},\n\n"
                    f"We've looked into your delivery issue for order {order_name} and "
                    f"arranged a replacement shipment for you.\n\n"
                    f"You'll receive a tracking update once it ships.\n\n"
                    f"Luna\n{brand_name}"
                )
            elif action_type == ActionType.RESTORE_ORDER.value:
                body = (
                    f"Hey {customer_name},\n\n"
                    f"Great news! Your order {order_name} has been restored and is now active again.\n\n"
                    f"You'll receive a shipping confirmation once your order processes.\n\n"
                    f"If you have any questions, just reply to this email.\n\n"
                    f"Luna\n{brand_name}"
                )
            else:
                return  # no standard confirmation for other types

            # Send via brand Gmail
            from src.services.brand_gmail_service import brand_gmail_service
            subject = (ticket.get("subject") if ticket else None) or f"Your {action_type.replace('_', ' ')}"
            reply_subject = subject if subject.startswith("Re:") else f"Re: {subject}"
            send_result = await brand_gmail_service.send_email(brand, customer_email, reply_subject, body)
            email_sent = send_result.get("success", False)
            logger.info(f"[Actions] Confirmation email sent={email_sent} for action {action.get('id')} → {customer_email}")

            # Update ticket: append message, resolve status
            if ticket_id and ticket:
                existing_msgs = list(ticket.get("messages") or [])
                existing_msgs.append({
                    "from": "AI Agent",
                    "body": body,
                    "sent_at": datetime.now(timezone.utc).isoformat(),
                    "direction": "outbound" if email_sent else "draft",
                })
                supabase_update("tickets", {"id": f"eq.{ticket_id}"}, {
                    "status": "resolved",
                    "messages": existing_msgs,
                    "email_sent": email_sent,
                })
                logger.info(f"[Actions] Ticket {ticket_id} resolved after {action_type} execution")

        except Exception as e:
            logger.warning(f"[Actions] _post_execution_notify failed (non-blocking): {e}")

    async def reject_action(
        self,
        tenant_id: str,
        action_id: str,
        reason: str,
        rejected_by: str = "admin"
    ) -> Dict[str, Any]:
        """Reject an action."""
        try:
            # Verify ownership
            action = await self.get_action(tenant_id, action_id)
            if not action:
                return {"success": False, "error": "Action not found"}

            if action["status"] != ActionStatus.PENDING.value:
                return {"success": False, "error": f"Action already {action['status']}"}

            supabase_update("actions", {"id": f"eq.{action_id}"}, {
                "status": ActionStatus.REJECTED.value,
                "rejection_reason": reason,
                "approved_by": rejected_by,
                "approved_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            })

            await self._log_event(tenant_id, action_id, "rejected", rejected_by, {"reason": reason})

            return {"success": True, "message": "Action rejected"}

        except Exception as e:
            logger.error(f"[Actions] Reject error: {e}")
            return {"success": False, "error": str(e)}

    async def get_stats(self, tenant_id: str) -> Dict[str, Any]:
        """Get action statistics for tenant."""
        try:
            actions = supabase_select("actions", {"tenant_id": f"eq.{tenant_id}"})

            return {
                "total": len(actions),
                "pending": len([a for a in actions if a.get("status") == ActionStatus.PENDING.value]),
                "executed": len([a for a in actions if a.get("status") == ActionStatus.EXECUTED.value]),
                "rejected": len([a for a in actions if a.get("status") == ActionStatus.REJECTED.value]),
                "failed": len([a for a in actions if a.get("status") == ActionStatus.FAILED.value]),
                "by_type": {
                    "refund": len([a for a in actions if a.get("action_type") == ActionType.REFUND.value]),
                    "cancel_order": len([a for a in actions if a.get("action_type") == ActionType.CANCEL_ORDER.value]),
                    "change_address": len([a for a in actions if a.get("action_type") == ActionType.CHANGE_ADDRESS.value])
                }
            }

        except Exception as e:
            logger.error(f"[Actions] Stats error: {e}")
            return {}

    async def _calculate_risk(
        self,
        tenant_id: str,
        action_type: str,
        order_id: str,
        customer_email: str
    ) -> tuple:
        """Calculate risk level for an action."""
        risk_score = 0
        factors = []

        # Action type risk
        if action_type == ActionType.REFUND.value:
            risk_score += 30
            factors.append("Refund request")
        elif action_type == ActionType.CANCEL_ORDER.value:
            risk_score += 20
            factors.append("Cancellation request")
        else:
            risk_score += 10
            factors.append("Address change")

        # Missing order ID
        if not order_id:
            risk_score += 25
            factors.append("Order ID not provided")

        # Check customer history
        try:
            past_refunds = supabase_select("actions", {
                "tenant_id": f"eq.{tenant_id}",
                "customer_email": f"eq.{customer_email}",
                "action_type": f"eq.{ActionType.REFUND.value}",
                "status": f"eq.{ActionStatus.EXECUTED.value}"
            })
            if len(past_refunds) >= 2:
                risk_score += 20
                factors.append(f"Multiple past refunds ({len(past_refunds)})")
        except Exception:
            pass

        # Determine level
        if risk_score >= 50:
            level = "high"
        elif risk_score >= 25:
            level = "medium"
        else:
            level = "low"

        return level, factors

    async def _mark_failed(self, action_id: str, error_message: str, error_code: str = None):
        """Mark an action as failed."""
        try:
            supabase_update("actions", {"id": f"eq.{action_id}"}, {
                "status": ActionStatus.FAILED.value,
                "error_message": error_message,
                "execution_result": {"error": error_message, "error_code": error_code},
                "updated_at": datetime.now(timezone.utc).isoformat()
            })
        except Exception as e:
            logger.error(f"[Actions] Mark failed error: {e}")

    async def _log_event(
        self,
        tenant_id: str,
        action_id: str,
        event: str,
        actor: str,
        details: Dict = None,
        error_code: str = None,
        error_message: str = None
    ):
        """Log an action event."""
        try:
            log_data = {
                "tenant_id": tenant_id,
                "action_id": action_id,
                "event": event,
                "actor": actor,
                "details": details or {},
                "error_code": error_code,
                "error_message": error_message,
            }
            supabase_insert("action_logs", log_data)
        except Exception as e:
            logger.warning(f"[Actions] Log event error: {e}")


# Singleton instance
actions_service = ActionsService()
