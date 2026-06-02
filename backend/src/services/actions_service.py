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


class ActionStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    EXECUTED = "executed"
    REJECTED = "rejected"
    FAILED = "failed"


class ActionDetector:
    """Detects and extracts action requests from customer messages."""

    REFUND_PATTERNS = [
        r'refund', r'money\s*back', r'get\s*my\s*money', r'want\s*a?\s*refund',
        r'full\s*refund', r'partial\s*refund', r'reimburse', r'reimbursement'
    ]

    CANCEL_PATTERNS = [
        r'cancel\s*(my\s*)?order', r'cancellation', r"don'?t\s*want",
        r'stop\s*(the\s*)?order', r'cancel\s*it'
    ]

    ADDRESS_PATTERNS = [
        r'change\s*(my\s*)?(shipping\s*)?address', r'update\s*(my\s*)?(shipping\s*)?address',
        r'wrong\s*address', r'new\s*address', r'ship\s*to\s*different',
        r'moved', r'change\s*delivery'
    ]

    def detect(self, message: str) -> Optional[Dict[str, Any]]:
        """
        Detect action type from message.

        Returns:
            Dict with action_type, confidence, extracted_data, or None
        """
        message_lower = message.lower()

        # Check refund
        refund_matches = sum(1 for p in self.REFUND_PATTERNS if re.search(p, message_lower))
        if refund_matches > 0:
            return {
                "action_type": ActionType.REFUND.value,
                "confidence": min(0.5 + (refund_matches * 0.15), 0.95),
                "extracted_data": self._extract_refund_data(message)
            }

        # Check cancellation
        cancel_matches = sum(1 for p in self.CANCEL_PATTERNS if re.search(p, message_lower))
        if cancel_matches > 0:
            return {
                "action_type": ActionType.CANCEL_ORDER.value,
                "confidence": min(0.5 + (cancel_matches * 0.15), 0.95),
                "extracted_data": self._extract_order_data(message)
            }

        # Check address change
        address_matches = sum(1 for p in self.ADDRESS_PATTERNS if re.search(p, message_lower))
        if address_matches > 0:
            return {
                "action_type": ActionType.CHANGE_ADDRESS.value,
                "confidence": min(0.5 + (address_matches * 0.15), 0.95),
                "extracted_data": self._extract_address_data(message)
            }

        return None

    def _extract_order_data(self, message: str) -> Dict[str, Any]:
        """Extract order ID from message."""
        data = {"order_id": None}

        patterns = [
            r'order\s*#?\s*(\d{4,})',
            r'#(\d{4,})',
            r'\b(\d{4,6})\b'
        ]

        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                data["order_id"] = match.group(1)
                break

        return data

    def _extract_refund_data(self, message: str) -> Dict[str, Any]:
        """Extract refund-specific data."""
        data = self._extract_order_data(message)

        # Extract amount
        amount_match = re.search(r'\$(\d+(?:\.\d{2})?)', message)
        if amount_match:
            data["amount"] = float(amount_match.group(1))

        # Refund type
        if 'full' in message.lower():
            data["refund_type"] = "full"
        elif 'partial' in message.lower():
            data["refund_type"] = "partial"
        else:
            data["refund_type"] = "full"

        return data

    def _extract_address_data(self, message: str) -> Dict[str, Any]:
        """Extract address change data."""
        data = self._extract_order_data(message)

        address_parts = {}

        # ZIP code
        zip_match = re.search(r'\b(\d{5}(?:-\d{4})?)\b', message)
        if zip_match:
            address_parts["zip"] = zip_match.group(1)

        # State abbreviation
        state_match = re.search(r'\b([A-Z]{2})\b', message)
        if state_match:
            address_parts["state"] = state_match.group(1)

        data["new_address"] = address_parts
        return data


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
        ai_reasoning: str = None
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
        ai_analysis: Dict = None
    ) -> Optional[Dict[str, Any]]:
        """
        Detect action from message and create if found.
        Deduplicates: skips creation when a pending action already exists for the
        same tenant + action_type + order_id to prevent duplicate escalations.
        """
        detection = self.detector.detect(message)
        if not detection:
            return None

        order_id = detection["extracted_data"].get("order_id")

        # Dedup: don't create a second pending action for the same order + type
        if order_id:
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

        return await self.create_action(
            tenant_id=tenant_id,
            action_type=detection["action_type"],
            customer_email=customer_email,
            customer_name=customer_name,
            order_id=order_id,
            message=message,
            extracted_data=detection["extracted_data"],
            confidence=detection["confidence"],
            ai_reasoning=ai_analysis.get("reasoning") if ai_analysis else None
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
                    new_address = action.get("extracted_data", {}).get("new_address", {})
                    if not new_address:
                        await self._mark_failed(action_id, "New address not provided", "missing_address")
                        return {"success": False, "error": "New address is required"}

                    execution_result = await shopify_client.update_shipping_address(
                        order_id=order_id,
                        new_address=new_address
                    )

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

            # Success - update status
            supabase_update("actions", {"id": f"eq.{action_id}"}, {
                "status": ActionStatus.EXECUTED.value,
                "execution_result": execution_result,
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            })

            # Log success
            await self._log_event(tenant_id, action_id, "executed", approved_by, execution_result)

            logger.info(f"[Actions] Executed {action_type} action {action_id}")

            return {
                "success": True,
                "message": execution_result.get("message", f"{action_type} completed"),
                "execution_result": execution_result
            }

        except Exception as e:
            logger.error(f"[Actions] Approve error: {e}")
            await self._mark_failed(action_id, str(e), "unknown_error")
            return {"success": False, "error": str(e)}

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
