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
from src.services.financial_audit import get_cached_result, record_financial_action
from src.services import email_automation_service

logger = logging.getLogger(__name__)

# financial_action_audit_log's action_type CHECK constraint allows these
# three — change_address/reship/restore_order don't move money, so they're
# outside the "financial action" audit/idempotency system by design.
# exchange is included: create_exchange_draft_order() creates a real
# Shopify order (free or invoiced) and must not fire twice on a retried
# approval any more than a refund or cancel may.
_AUDITED_ACTION_TYPES = {"refund", "cancel_order", "exchange"}


class ActionType(str, Enum):
    REFUND = "refund"
    CANCEL_ORDER = "cancel_order"
    CHANGE_ADDRESS = "change_address"
    RESHIP = "reship"
    RESTORE_ORDER = "restore_order"
    EXCHANGE = "exchange"


class ActionStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    EXECUTED = "executed"
    REJECTED = "rejected"
    FAILED = "failed"
    # Approved, but the underlying step is manual (reship only, today) and
    # nothing was actually done in Shopify — waits here until the merchant
    # explicitly confirms via complete_manual_action(). Never set by
    # anything except approve_action()'s RESHIP branch.
    AWAITING_MANUAL_STEP = "awaiting_manual_step"


class ActionDetector:
    """Detects and extracts action requests from customer messages using AI."""

    async def detect_async(self, message: str):
        """AI-based action detection — replaces regex patterns."""
        from src.services.intent_detector import intent_detector, IntentResult
        result = await intent_detector.detect(message)
        if not result.has_action:
            return None

        # "exchange" is deliberately absent — like restore_order, it needs
        # the rich, live-Shopify-grounded target-variant resolution that
        # only return_actions_integration.py's primary agent path does
        # (this fallback's own eligibility pre-filter below has no way to
        # know what replacement the customer wants). Creating a bare
        # exchange action here with no target data would just be a dead
        # action a human approver can't act on. "return" maps to the same
        # "refund" action_type as "refund" itself — a return and a refund
        # resolve to the exact same Shopify mutation (there is no separate
        # Returns-API call in this integration), so they share one path.
        action_map = {
            "refund": "refund",
            "return": "refund",
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
            # 409 = idx_actions_dedup_active (migration 053) rejected a
            # concurrent duplicate insert — two requests for the same
            # tenant+order+action_type both passed the app-level
            # check-then-insert dedup before either had committed. This is
            # the exact race that check is meant to catch; look up and
            # return the row that actually won instead of surfacing a raw
            # failure, matching detect_and_create()'s own duplicate_skipped
            # shape for the same situation.
            if "409" in str(e):
                logger.warning(f"[Actions] Duplicate insert caught by DB constraint for tenant {tenant_id}, order {order_id}, type {action_type} — returning existing action")
                try:
                    existing = supabase_select("actions", {
                        "tenant_id": f"eq.{tenant_id}",
                        "order_id": f"eq.{order_id}",
                        "action_type": f"eq.{action_type}",
                        "status": "in.(pending,approved,executed,awaiting_manual_step)",
                        "order": "created_at.desc",
                        "limit": "1",
                    })
                    if existing:
                        return {
                            "success": True,
                            "action_id": existing[0]["id"],
                            "action_type": action_type,
                            "status": "duplicate_skipped",
                        }
                except Exception as lookup_err:
                    logger.warning(f"[Actions] Could not look up the winning duplicate action: {lookup_err}")
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
                    "status": f"in.(executed,approved,{ActionStatus.AWAITING_MANUAL_STEP.value})",
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

        extracted_data = detection["extracted_data"]

        # Refund/cancel eligibility gate — uses the exact same shared
        # function return_actions_integration.py uses (actions_manager.
        # check_return_eligibility), so there is one source of truth for
        # policy no matter which channel (chat/email vs WhatsApp/web-form)
        # a request came in through. This only affects whether an action is
        # staged for human review; it never bypasses approval — every path
        # that stages an action still requires an explicit approve call.
        if detection["action_type"] in ("refund", "cancel_order"):
            try:
                from src.services.actions_manager import actions_manager
                eligibility = await actions_manager.check_return_eligibility(
                    order_id, customer_email, tenant_id=tenant_id, brand_id=brand_id
                )
                if not eligibility.get("eligible") and not (
                    eligibility.get("staging_required") or eligibility.get("requires_manual_review")
                ):
                    logger.info(
                        f"[Actions] Not staging {detection['action_type']} for order {order_id}: "
                        f"{eligibility.get('reason')}"
                    )
                    return None
                extracted_data = {**extracted_data, "eligibility": eligibility}
            except Exception as elig_err:
                # Fail open to manual review rather than silently skipping —
                # an eligibility-check failure shouldn't hide a real refund
                # request from staff, it should just skip the pre-filter.
                logger.warning(f"[Actions] Eligibility check failed (continuing without it): {elig_err}")

        return await self.create_action(
            tenant_id=tenant_id,
            action_type=detection["action_type"],
            customer_email=customer_email,
            customer_name=customer_name,
            order_id=order_id,
            message=message,
            extracted_data=extracted_data,
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
                "status": f"in.(executed,rejected,failed,{ActionStatus.AWAITING_MANUAL_STEP.value})",
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
        approved_by: str = "admin",
        idempotency_key: Optional[str] = None,
        ip_address: Optional[str] = None,
        override_amount: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Approve and execute an action.

        This is the core execution flow:
        1. Validate action belongs to tenant
        2. Get Shopify client
        3. Execute the action
        4. Update status
        5. Log result

        For refund/cancel_order (the two real "financial actions"): a
        retried request with the same Idempotency-Key header replays the
        original result instead of re-executing against Shopify, and every
        execution attempt is recorded in financial_action_audit_log — same
        protections v2_tickets.py's execute_refund/execute_cancel_order
        already have, applied here since this is a second, equally-live
        path to the same Shopify calls.

        override_amount: a HUMAN-entered partial refund amount, typed by the
        approver at approval time in the dashboard — never AI-extracted, and
        never inferred from the customer's message. Only meaningful for
        action_type=refund. Positive-value validation happens here (defense
        in depth — the API layer already rejects <=0 via Pydantic); the
        upper bound (must not exceed the order's actual refundable amount)
        is enforced deterministically inside ShopifyClient.process_refund()
        against live Shopify state, never guessed here.
        """
        if override_amount is not None and override_amount <= 0:
            return {"success": False, "error": "Refund amount must be greater than zero", "error_code": "invalid_amount"}
        try:
            # Get action (tenant-scoped)
            action = await self.get_action(tenant_id, action_id)
            if not action:
                return {"success": False, "error": "Action not found"}

            action_type = action["action_type"]
            is_audited = action_type in _AUDITED_ACTION_TYPES
            ticket_id = action.get("ticket_id")

            if is_audited and idempotency_key and ticket_id:
                cached = get_cached_result(ticket_id, action_type, idempotency_key)
                if cached:
                    if cached["status"] == "success":
                        return cached["result"]
                    return {"success": False, "error": cached.get("error_detail") or "Action failed on the original attempt."}

            # A previously-failed action (e.g. Shopify was briefly down, or
            # the store's token had expired and has since been reconnected)
            # may be retried from here — same call, same code path, no
            # separate retry endpoint. Anything else already actioned
            # (approved/executed/rejected) is not.
            if action["status"] not in (ActionStatus.PENDING.value, ActionStatus.FAILED.value):
                return {"success": False, "error": f"Action already {action['status']}"}

            # Atomically claim the action (conditioned on it still being
            # "pending" or "failed") before touching Shopify. Closes the race
            # where two concurrent approve calls (double-click, retry) could
            # both pass the check above and each execute a real refund/cancel
            # against the same order.
            claimed = supabase_update(
                "actions",
                {"id": f"eq.{action_id}", "status": f"in.({ActionStatus.PENDING.value},{ActionStatus.FAILED.value})"},
                {
                    "status": ActionStatus.APPROVED.value,
                    "approved_by": approved_by,
                    "approved_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }
            )
            if not claimed:
                return {"success": False, "error": "Action already actioned"}

            # Get Shopify client
            try:
                shopify_client = await shopify_service.get_client_for_tenant(tenant_id)
            except ShopifyError as e:
                await self._mark_failed(action_id, e.message, e.error_code)
                self._record_financial_audit(action, idempotency_key, ip_address, approved_by,
                                              status="failed", error_detail=e.message)
                return {"success": False, "error": e.message, "error_code": e.error_code}

            # Execute based on type
            order_id = action.get("order_id") or action.get("extracted_data", {}).get("order_id")

            if not order_id:
                await self._mark_failed(action_id, "Order ID is required", "missing_order_id")
                self._record_financial_audit(action, idempotency_key, ip_address, approved_by,
                                              status="failed", error_detail="Order ID is required")
                return {"success": False, "error": "Order ID is required for this action"}

            execution_result = None
            action_type = action["action_type"]

            try:
                if action_type == ActionType.REFUND.value:
                    extracted = action.get("extracted_data", {})
                    # override_amount (human-entered at approval time) wins
                    # over extracted_data.amount (which today is never
                    # AI-set — staging never extracts a dollar figure from
                    # the customer's message). Neither set means the
                    # existing full-refund default inside process_refund().
                    refund_amount = override_amount if override_amount is not None else extracted.get("amount")
                    await self._record_edit_tracking(action_id, extracted, override_amount, extracted.get("amount"))
                    execution_result = await shopify_client.process_refund(
                        order_id=order_id,
                        amount=refund_amount,
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

                elif action_type == ActionType.EXCHANGE.value:
                    extracted = action.get("extracted_data", {})
                    target = extracted.get("target") or {}
                    original_item = extracted.get("original_item") or {}
                    price_difference = extracted.get("price_difference")

                    if not target.get("variant_id") or price_difference is None:
                        # Staged without a resolved live target — return_actions_integration.py
                        # never stages an exchange without one, but this is the same
                        # defense-in-depth every other branch here has for malformed data.
                        raise ShopifyError(
                            "This exchange has no resolved replacement item on file. "
                            "Please verify the requested size/color/product manually in Shopify admin.",
                            ShopifyErrorCode.INVALID_REQUEST,
                        )

                    if price_difference < 0:
                        # A cheaper replacement means money is potentially owed back to
                        # the customer — no store policy exists anywhere in this system
                        # for whether that's refunded, credited, or absorbed. This must
                        # never be auto-decided; return_actions_integration.py already
                        # stages this case as manual-review-required, but this is the
                        # same safety net every other execution branch has.
                        execution_result = {
                            "success": True,
                            "manual_action_required": True,
                            "message": (
                                f"Replacement item is ${abs(price_difference):.2f} cheaper than the original. "
                                "Decide how to handle the difference (refund, store credit, or none) and "
                                "create the replacement order manually in Shopify admin."
                            ),
                            "order_id": order_id,
                            "order_name": f"#{order_id}",
                            "price_difference": price_difference,
                        }
                    else:
                        execution_result = await shopify_client.create_exchange_draft_order(
                            customer_email=action.get("customer_email"),
                            variant_id=target["variant_id"],
                            quantity=original_item.get("quantity") or 1,
                            price_difference=float(price_difference),
                            order_name=f"#{order_id}",
                            note=f"Exchange for order #{order_id} - Action {action_id[:8]}",
                        )

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
                self._record_financial_audit(action, idempotency_key, ip_address, approved_by,
                                              status="failed", error_detail=e.message)
                return {
                    "success": False,
                    "error": e.message,
                    "error_code": e.error_code
                }

            # Success - update action status. Reship's manual_action_required
            # is a real Shopify no-op (the RESHIP branch above never calls
            # Shopify) - marking it EXECUTED here would claim work that
            # hasn't happened. It waits in AWAITING_MANUAL_STEP until the
            # merchant explicitly confirms via complete_manual_action().
            # Scoped to RESHIP only - Change Address's own manual_action_
            # required case is unaffected, matching its existing behavior.
            if action_type == ActionType.RESHIP.value:
                supabase_update("actions", {"id": f"eq.{action_id}"}, {
                    "status": ActionStatus.AWAITING_MANUAL_STEP.value,
                    "execution_result": execution_result,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                })
            else:
                supabase_update("actions", {"id": f"eq.{action_id}"}, {
                    "status": ActionStatus.EXECUTED.value,
                    "execution_result": execution_result,
                    "executed_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                })

            # Log success
            await self._log_event(tenant_id, action_id, "executed", approved_by, execution_result)

            # Invalidate the Order Context panel's cache (see fetch_shopify_order)
            # so a ticket reopened right after this action shows the new order
            # state instead of the pre-execution cached snapshot.
            if action_type in (ActionType.REFUND.value, ActionType.CANCEL_ORDER.value, ActionType.RESTORE_ORDER.value):
                from src.services.shopify_service import invalidate_order_cache
                invalidate_order_cache(action.get("brand_id"), order_id)

            logger.info(f"[Actions] Executed {action_type} action {action_id}")

            # Post-execution: send branded confirmation email + resolve ticket
            await self._post_execution_notify(action, action_type, execution_result)

            final_result = {
                "success": True,
                "message": execution_result.get("message", f"{action_type} completed"),
                "execution_result": execution_result
            }
            self._record_financial_audit(action, idempotency_key, ip_address, approved_by,
                                          status="success", result=final_result)
            return final_result

        except Exception as e:
            # Unlike a ShopifyError (whose .message is already a curated,
            # merchant-safe string set at each raise site), this is a
            # genuinely unexpected exception — str(e) can be an arbitrary
            # library/network error and must never be shown to the merchant
            # verbatim (it's logged and kept in the audit trail for staff,
            # never in the row error_message/response the dashboard renders).
            logger.error(f"[Actions] Approve error: {e}")
            safe_message = "Something went wrong completing this action. Please try again or check Shopify directly."
            await self._mark_failed(action_id, safe_message, "unknown_error")
            if 'action' in locals():
                self._record_financial_audit(action, idempotency_key, ip_address, approved_by,
                                              status="failed", error_detail=str(e))
            return {"success": False, "error": safe_message}

    async def complete_manual_action(
        self,
        tenant_id: str,
        action_id: str,
        completed_by: str,
    ) -> Dict[str, Any]:
        """Merchant-confirmed completion for an action sitting in
        AWAITING_MANUAL_STEP (reship, today) after they've done the manual
        Shopify work by hand. Never calls Shopify and never creates
        anything - purely a status transition. Tenant-scoped via
        get_action() (same isolation every other action lookup uses), so
        another tenant's action_id simply isn't found. Idempotent: calling
        this again on an already-completed action returns success unchanged
        rather than erroring, and a genuine concurrent double-click is
        closed the same way approve_action() closes its own race - an
        atomic conditional update, only one of which can win."""
        action = await self.get_action(tenant_id, action_id)
        if not action:
            return {"success": False, "error": "Action not found"}

        if action["status"] == ActionStatus.EXECUTED.value:
            return {"success": True, "status": ActionStatus.EXECUTED.value, "already_completed": True}

        if action["status"] != ActionStatus.AWAITING_MANUAL_STEP.value:
            return {"success": False, "error": f"Action is {action['status']}, not awaiting a manual step"}

        # Preserve the original execution_result (the "please create a
        # replacement shipment" instruction + order info) instead of
        # overwriting it - just layer the completion facts on top.
        # manual_action_required is cleared here (not before): it's what the
        # dashboard's Completed section already reads to decide between its
        # "Completed" and "Approved — manual step remaining" badges, so this
        # is what makes a freshly-completed reship render as truly Completed
        # instead of still looking like it needs manual work.
        merged_result = {
            **(action.get("execution_result") or {}),
            "manual_action_required": False,
            "manually_completed_by": completed_by,
            "manually_completed_at": datetime.now(timezone.utc).isoformat(),
        }

        claimed = supabase_update(
            "actions",
            {"id": f"eq.{action_id}", "status": f"eq.{ActionStatus.AWAITING_MANUAL_STEP.value}"},
            {
                "status": ActionStatus.EXECUTED.value,
                "execution_result": merged_result,
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        if not claimed:
            # Lost a race to a concurrent completion request - already done.
            return {"success": True, "status": ActionStatus.EXECUTED.value, "already_completed": True}

        await self._log_event(tenant_id, action_id, "manually_completed", completed_by, {
            "action_type": action.get("action_type"),
            "order_id": action.get("order_id"),
        })

        return {"success": True, "status": ActionStatus.EXECUTED.value}

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

            # Never derive a name from the email local-part (e.g.
            # "customer10@example.com" -> "Customer10"), and never greet by
            # a placeholder ("Website Visitor", "Customer", ...) as if it
            # were real - same placeholder set customer_success_agent.py's
            # _known_customer_name() treats as "no name known". Falls back
            # to the neutral idiom "there" ("Hey there,") in that case.
            _unknown_name_placeholders = {"there", "customer", "website visitor", "unknown", "guest", "friend"}
            _raw_customer_name = action.get("customer_name") or (ticket.get("customer_name") if ticket else None)
            customer_name = (
                _raw_customer_name.capitalize()
                if _raw_customer_name and _raw_customer_name.strip().lower() not in _unknown_name_placeholders
                else "there"
            )
            brand_name = brand.get("name", "our team")
            order_name = execution_result.get("order_name") or f"your order"

            # Custom Email Automation: a merchant-configured template for
            # this exact brand+trigger takes over the confirmation email
            # entirely, still gated behind the same already-successful
            # execution this whole function only ever runs after. Falls
            # through to the existing hardcoded copy below when none is
            # configured/enabled — unchanged default behavior.
            if brand_id and action_type in email_automation_service.SUPPORTED_TRIGGERS:
                custom_automation = email_automation_service.get_enabled_automation(brand_id, action_type)
                if custom_automation:
                    variables = {
                        "customer_name": customer_name,
                        "order_number": order_name,
                        "brand_name": brand_name,
                        "order_status": email_automation_service.order_status_label(action_type, execution_result),
                    }
                    if action_type == ActionType.REFUND.value:
                        amount = execution_result.get("amount", "")
                        variables["refund_amount"] = f"PKR {amount:.2f}" if isinstance(amount, (int, float)) else str(amount)
                    subject = email_automation_service.render_template(custom_automation["subject"], variables)
                    body = email_automation_service.render_template(custom_automation["body"], variables)

                    if custom_automation.get("requires_approval", True):
                        email_automation_service.queue_pending_send(custom_automation, action, customer_email, subject, body)
                        email_sent = False
                        logger.info(f"[Actions] Custom automation '{custom_automation.get('name')}' queued for merchant approval — action {action.get('id')}")
                    else:
                        from src.services.brand_gmail_service import brand_gmail_service
                        reply_subject = subject if subject.startswith("Re:") else f"Re: {subject}"
                        send_result = await brand_gmail_service.send_email(brand, customer_email, reply_subject, body)
                        email_sent = send_result.get("success", False)
                        logger.info(f"[Actions] Custom automation '{custom_automation.get('name')}' sent={email_sent} for action {action.get('id')} → {customer_email}")

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
                        logger.info(f"[Actions] Ticket {ticket_id} resolved after {action_type} execution (custom automation)")
                    return

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
                # Reship has no automated Shopify operation (see approve_action's
                # RESHIP branch - always manual_action_required=True): a team
                # member still has to create the replacement shipment by hand.
                # Never tell the customer it's "arranged" or that it "will
                # ship" - that claims a step that hasn't actually happened yet.
                if execution_result.get("manual_action_required"):
                    body = (
                        f"Hey {customer_name},\n\n"
                        f"We've looked into your delivery issue for order {order_name} and our team "
                        f"is arranging a replacement shipment now.\n\n"
                        f"You'll get a tracking update as soon as it's created.\n\n"
                        f"Luna\n{brand_name}"
                    )
                else:
                    body = (
                        f"Hey {customer_name},\n\n"
                        f"Your replacement shipment for order {order_name} has been arranged.\n\n"
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
            elif action_type == ActionType.EXCHANGE.value:
                if execution_result.get("manual_action_required"):
                    body = (
                        f"Hey {customer_name},\n\n"
                        f"We've received your exchange request for order {order_name} and our team "
                        f"is finishing it up now.\n\n"
                        f"You'll get a confirmation once your replacement order is ready.\n\n"
                        f"If you have any questions, just reply to this email.\n\n"
                        f"Luna\n{brand_name}"
                    )
                elif execution_result.get("completed"):
                    body = (
                        f"Hey {customer_name},\n\n"
                        f"Your exchange for order {order_name} is confirmed, no additional payment needed. "
                        f"We've created your replacement order and it'll ship the same way your original order did.\n\n"
                        f"Once we receive your original item back, you're all set.\n\n"
                        f"If you have any questions, just reply to this email.\n\n"
                        f"Luna\n{brand_name}"
                    )
                else:
                    invoice_url = execution_result.get("invoice_url")
                    balance = execution_result.get("balance_due")
                    balance_str = f"${balance:.2f}" if isinstance(balance, (int, float)) else str(balance)
                    link_line = f"Complete it here: {invoice_url}\n\n" if invoice_url else ""
                    body = (
                        f"Hey {customer_name},\n\n"
                        f"Your exchange for order {order_name} is ready, there's a {balance_str} difference "
                        f"for the new item.\n\n"
                        f"{link_line}"
                        f"Once we receive your original item back, you're all set.\n\n"
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

            # Atomically claim the action (conditioned on it still being
            # "pending") before writing - closes the same race approve_action
            # already guards against: without this, a reject request racing
            # a concurrent approve/execute could pass the check above and
            # then unconditionally overwrite an action that was just
            # approved or executed back to "rejected", corrupting the audit
            # trail (the action itself was never re-executed by this path,
            # but its recorded status would silently lie about what happened).
            claimed = supabase_update(
                "actions",
                {"id": f"eq.{action_id}", "status": f"eq.{ActionStatus.PENDING.value}"},
                {
                    "status": ActionStatus.REJECTED.value,
                    "rejection_reason": reason,
                    "approved_by": rejected_by,
                    "approved_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }
            )
            if not claimed:
                return {"success": False, "error": "Action already actioned"}

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
                    "change_address": len([a for a in actions if a.get("action_type") == ActionType.CHANGE_ADDRESS.value]),
                    "exchange": len([a for a in actions if a.get("action_type") == ActionType.EXCHANGE.value])
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
        elif action_type == ActionType.EXCHANGE.value:
            # Creates a real (possibly free) Shopify order — comparable
            # financial/inventory weight to a refund.
            risk_score += 30
            factors.append("Exchange request")
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

    def _record_financial_audit(
        self,
        action: Dict[str, Any],
        idempotency_key: Optional[str],
        ip_address: Optional[str],
        approved_by: str,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        error_detail: Optional[str] = None,
    ) -> None:
        """No-op for action types outside _AUDITED_ACTION_TYPES, or when there's
        no ticket_id to key the record on. Never raises — audit recording must
        not be the reason a real approve/reject response fails to return."""
        action_type = action.get("action_type")
        ticket_id = action.get("ticket_id")
        if action_type not in _AUDITED_ACTION_TYPES or not ticket_id:
            return
        try:
            record_financial_action(
                ticket_id=ticket_id,
                tenant_id=action.get("tenant_id"),
                brand_id=action.get("brand_id"),
                action_type=action_type,
                idempotency_key=idempotency_key,
                user_id=None,
                user_email=approved_by,
                ip_address=ip_address,
                status=status,
                result=result,
                error_detail=error_detail,
            )
        except Exception as e:
            logger.warning(f"[Actions] Failed to record financial audit for action {action.get('id')}: {e}")

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

    async def _record_edit_tracking(
        self,
        action_id: str,
        extracted_data: Dict[str, Any],
        override_amount: Optional[float],
        proposed_amount: Optional[float],
    ):
        """Best-effort: record whether the human approver changed the
        refund amount before approving (the only edit surface any action
        type has today - see actions/046_action_edit_tracking.sql).

        Deliberately a separate, non-blocking update - never folded into
        the atomic pending->approved claim - for two reasons: (1) this is
        enrichment, not part of the approval state machine, so it must
        never be able to fail an approval; (2) the migration adding these
        columns is not applied to production yet, so writing them as part
        of a required update would break every refund approval the moment
        this code ships, not just silently skip the tracking. Once the
        migration lands this starts writing real data with no further code
        change needed; until then it logs and moves on.
        """
        was_edited = override_amount is not None and override_amount != proposed_amount
        if not was_edited:
            return
        try:
            supabase_update("actions", {"id": f"eq.{action_id}"}, {
                "was_edited": True,
                "approved_extracted_data": {**extracted_data, "amount": override_amount},
            })
        except Exception as e:
            logger.info(f"[Actions] Edit tracking not recorded for {action_id} (migration 046 likely not applied yet): {e}")

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
