"""
Multi-Brand Actions Manager
============================
Handles action detection, staging, approval, and execution for multiple brands.
Supports: Refund, Cancel Order, Address Change
"""
import os
import re
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from enum import Enum

from src.lib.supabase_client import supabase_select, supabase_insert, supabase_update
from src.services.brand_manager import brand_manager, BrandShopifyClient

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

    REFUND_KEYWORDS = [
        'refund', 'money back', 'get my money', 'want a refund',
        'full refund', 'partial refund', 'reimbursement', 'reimburse'
    ]

    CANCEL_KEYWORDS = [
        'cancel order', 'cancel my order', 'cancellation', 'cancel the order',
        'dont want', "don't want", 'stop the order', 'cancel it'
    ]

    ADDRESS_KEYWORDS = [
        'change address', 'update address', 'wrong address', 'new address',
        'change shipping', 'update shipping', 'ship to different',
        'moved', 'change delivery'
    ]

    def detect_action(self, message: str) -> Optional[Dict[str, Any]]:
        """
        Detect action type from message.

        Returns:
            Dict with action_type, confidence, extracted_data
        """
        message_lower = message.lower()

        # Check for refund intent
        if any(kw in message_lower for kw in self.REFUND_KEYWORDS):
            return {
                "action_type": ActionType.REFUND,
                "confidence": self._calculate_confidence(message_lower, self.REFUND_KEYWORDS),
                "extracted_data": self._extract_refund_data(message)
            }

        # Check for cancellation intent
        if any(kw in message_lower for kw in self.CANCEL_KEYWORDS):
            return {
                "action_type": ActionType.CANCEL_ORDER,
                "confidence": self._calculate_confidence(message_lower, self.CANCEL_KEYWORDS),
                "extracted_data": self._extract_order_data(message)
            }

        # Check for address change intent
        if any(kw in message_lower for kw in self.ADDRESS_KEYWORDS):
            return {
                "action_type": ActionType.CHANGE_ADDRESS,
                "confidence": self._calculate_confidence(message_lower, self.ADDRESS_KEYWORDS),
                "extracted_data": self._extract_address_data(message)
            }

        return None

    def _calculate_confidence(self, message: str, keywords: List[str]) -> float:
        """Calculate confidence based on keyword matches."""
        matches = sum(1 for kw in keywords if kw in message)
        return min(0.5 + (matches * 0.15), 0.95)

    def _extract_order_data(self, message: str) -> Dict[str, Any]:
        """Extract order ID from message."""
        data = {"order_id": None}

        # Pattern: order #1234, order 1234, #1234
        patterns = [
            r'order\s*#?(\d{4,})',
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

        # Try to extract amount
        amount_match = re.search(r'\$(\d+(?:\.\d{2})?)', message)
        if amount_match:
            data["amount"] = float(amount_match.group(1))

        # Check for full vs partial refund
        if 'full' in message.lower():
            data["refund_type"] = "full"
        elif 'partial' in message.lower():
            data["refund_type"] = "partial"
        else:
            data["refund_type"] = "full"  # Default to full

        return data

    def _extract_address_data(self, message: str) -> Dict[str, Any]:
        """Extract address change data."""
        data = self._extract_order_data(message)

        # Try to extract new address components
        # This is simplified - in production, use an address parsing service
        address_parts = {}

        # Look for common address patterns
        zip_match = re.search(r'\b(\d{5}(?:-\d{4})?)\b', message)
        if zip_match:
            address_parts["zip"] = zip_match.group(1)

        # Look for state abbreviations
        state_match = re.search(r'\b([A-Z]{2})\b', message)
        if state_match:
            address_parts["state"] = state_match.group(1)

        data["new_address"] = address_parts
        return data


class MultiBrandActionsManager:
    """
    Central manager for multi-brand actions.
    Handles the full lifecycle: detect -> stage -> approve/reject -> execute
    """

    def __init__(self):
        self.detector = ActionDetector()

    async def detect_and_stage_action(
        self,
        brand_id: str,
        ticket_id: str,
        customer_email: str,
        customer_name: str,
        message: str,
        ai_analysis: Dict[str, Any] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Detect action from message and stage it for approval.

        Returns:
            Staged action or None if no action detected
        """
        # Detect action type
        detection = self.detector.detect_action(message)
        if not detection:
            return None

        action_type = detection["action_type"]
        confidence = detection["confidence"]
        extracted_data = detection["extracted_data"]

        # Get brand for risk assessment
        brand = await brand_manager.get_brand(brand_id)
        if not brand:
            logger.error(f"[Actions] Brand not found: {brand_id}")
            return None

        # Determine risk level
        risk_level = await self._calculate_risk(
            brand=brand,
            action_type=action_type,
            extracted_data=extracted_data,
            customer_email=customer_email
        )

        # Build action payload
        action_payload = {
            "brand_id": brand_id,
            "ticket_id": ticket_id,
            "action_type": action_type.value,
            "status": ActionStatus.PENDING.value,
            "customer_email": customer_email,
            "customer_name": customer_name,
            "order_id": extracted_data.get("order_id"),
            "confidence_score": confidence,
            "risk_level": risk_level["level"],
            "risk_factors": risk_level["factors"],
            "extracted_data": extracted_data,
            "ai_reasoning": ai_analysis.get("reasoning") if ai_analysis else f"Customer requested {action_type.value}",
            "original_message": message[:500],  # Truncate for storage
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }

        # Insert into database
        try:
            result = supabase_insert("brand_actions", action_payload)
            action_id = result.get("id")

            logger.info(f"[Actions] Staged {action_type.value} for brand {brand_id}, action ID: {action_id}")

            return {
                "success": True,
                "action_id": action_id,
                "action_type": action_type.value,
                "risk_level": risk_level["level"],
                "confidence": confidence,
                "requires_approval": risk_level["level"] in ["medium", "high"]
            }

        except Exception as e:
            logger.error(f"[Actions] Failed to stage action: {e}")
            return {"success": False, "error": str(e)}

    async def _calculate_risk(
        self,
        brand: Dict[str, Any],
        action_type: ActionType,
        extracted_data: Dict[str, Any],
        customer_email: str
    ) -> Dict[str, Any]:
        """Calculate risk level for an action."""
        factors = []
        risk_score = 0

        # Factor 1: Action type risk
        if action_type == ActionType.REFUND:
            risk_score += 30
            factors.append("Refund request")
        elif action_type == ActionType.CANCEL_ORDER:
            risk_score += 20
            factors.append("Cancellation request")
        elif action_type == ActionType.CHANGE_ADDRESS:
            risk_score += 10
            factors.append("Address change")

        # Factor 2: Check order value (if we have order data)
        order_id = extracted_data.get("order_id")
        if order_id:
            try:
                # Try to get order value from database
                orders = supabase_select("orders", {"order_number": f"eq.{order_id}", "store_id": f"eq.{brand.get('id')}"})
                if orders:
                    order_value = float(orders[0].get("total_amount", 0))
                    if order_value > brand.get("auto_approve_threshold", 50):
                        risk_score += 30
                        factors.append(f"High value order (${order_value})")
                    elif order_value > 100:
                        risk_score += 15
                        factors.append(f"Medium value order (${order_value})")
            except Exception:
                pass

        # Factor 3: Customer history (check for repeat refund requests)
        try:
            recent_actions = supabase_select("brand_actions", {
                "customer_email": f"eq.{customer_email}",
                "action_type": f"eq.{ActionType.REFUND.value}",
                "status": "eq.executed"
            })
            if len(recent_actions) >= 3:
                risk_score += 25
                factors.append(f"Multiple past refunds ({len(recent_actions)})")
        except Exception:
            pass

        # Factor 4: Missing order ID
        if not order_id:
            risk_score += 20
            factors.append("Order ID not provided")

        # Determine level
        if risk_score >= 60:
            level = "high"
        elif risk_score >= 30:
            level = "medium"
        else:
            level = "low"

        return {
            "level": level,
            "score": risk_score,
            "factors": factors
        }

    async def get_pending_actions(
        self,
        brand_id: str = None,
        status: str = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get pending actions, optionally filtered by brand."""
        try:
            filters = {}
            if brand_id:
                filters["brand_id"] = f"eq.{brand_id}"
            if status:
                filters["status"] = f"eq.{status}"
            else:
                filters["status"] = f"eq.{ActionStatus.PENDING.value}"

            actions = supabase_select("brand_actions", filters)

            # Sort by created_at descending
            actions = sorted(actions, key=lambda x: x.get("created_at", ""), reverse=True)[:limit]

            # Enrich with brand name
            enriched = []
            brand_cache = {}
            for action in actions:
                b_id = action.get("brand_id")
                if b_id not in brand_cache:
                    b = await brand_manager.get_brand(b_id)
                    brand_cache[b_id] = b.get("name") if b else "Unknown"

                enriched.append({
                    **action,
                    "brand_name": brand_cache[b_id]
                })

            return enriched

        except Exception as e:
            logger.error(f"[Actions] Error getting pending actions: {e}")
            return []

    async def approve_action(
        self,
        action_id: str,
        approved_by: str = "admin"
    ) -> Dict[str, Any]:
        """
        Approve and execute an action.
        """
        try:
            # Get the action
            actions = supabase_select("brand_actions", {"id": f"eq.{action_id}"})
            if not actions:
                return {"success": False, "error": "Action not found"}

            action = actions[0]

            if action["status"] != ActionStatus.PENDING.value:
                return {"success": False, "error": f"Action already {action['status']}"}

            # Get brand and create Shopify client
            brand = await brand_manager.get_brand(action["brand_id"])
            if not brand:
                return {"success": False, "error": "Brand not found"}

            shopify_client = brand_manager.get_shopify_client(brand)

            # Execute based on action type
            action_type = action["action_type"]
            order_id = action.get("order_id") or action.get("extracted_data", {}).get("order_id")

            if not order_id:
                return {"success": False, "error": "Order ID required for execution"}

            execution_result = None

            if action_type == ActionType.REFUND.value:
                extracted = action.get("extracted_data", {})
                execution_result = await shopify_client.process_refund(
                    order_id=order_id,
                    amount=extracted.get("amount"),
                    note=f"Action ID: {action_id} - Approved by {approved_by}"
                )

            elif action_type == ActionType.CANCEL_ORDER.value:
                execution_result = await shopify_client.cancel_order(
                    order_id=order_id,
                    reason="customer",
                    email=True
                )

            elif action_type == ActionType.CHANGE_ADDRESS.value:
                new_address = action.get("extracted_data", {}).get("new_address", {})
                if not new_address:
                    return {"success": False, "error": "New address not provided"}

                execution_result = await shopify_client.update_shipping_address(
                    order_id=order_id,
                    new_address=new_address
                )

            else:
                return {"success": False, "error": f"Unknown action type: {action_type}"}

            # Handle execution result
            if execution_result and execution_result.get("success"):
                # Update action status
                supabase_update("brand_actions", {"id": f"eq.{action_id}"}, {
                    "status": ActionStatus.EXECUTED.value,
                    "approved_by": approved_by,
                    "approved_at": datetime.now(timezone.utc).isoformat(),
                    "executed_at": datetime.now(timezone.utc).isoformat(),
                    "execution_result": execution_result,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                })

                # Send confirmation email
                await self._send_confirmation_email(action, brand, execution_result)

                # Log to action_logs
                await self._log_action(action_id, "executed", approved_by, execution_result)

                return {
                    "success": True,
                    "message": f"{action_type} executed successfully",
                    "execution_result": execution_result
                }
            else:
                # Mark as failed
                error_msg = execution_result.get("error") if execution_result else "Unknown error"
                supabase_update("brand_actions", {"id": f"eq.{action_id}"}, {
                    "status": ActionStatus.FAILED.value,
                    "execution_result": {"error": error_msg},
                    "updated_at": datetime.now(timezone.utc).isoformat()
                })

                return {"success": False, "error": error_msg}

        except Exception as e:
            logger.error(f"[Actions] Error approving action: {e}")
            return {"success": False, "error": str(e)}

    async def reject_action(
        self,
        action_id: str,
        rejection_reason: str,
        rejected_by: str = "admin"
    ) -> Dict[str, Any]:
        """Reject an action."""
        try:
            supabase_update("brand_actions", {"id": f"eq.{action_id}"}, {
                "status": ActionStatus.REJECTED.value,
                "rejection_reason": rejection_reason,
                "approved_by": rejected_by,
                "approved_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            })

            # Get action for email
            actions = supabase_select("brand_actions", {"id": f"eq.{action_id}"})
            if actions:
                action = actions[0]
                brand = await brand_manager.get_brand(action["brand_id"])
                if brand:
                    await self._send_rejection_email(action, brand, rejection_reason)

            await self._log_action(action_id, "rejected", rejected_by, {"reason": rejection_reason})

            return {"success": True, "message": "Action rejected"}

        except Exception as e:
            logger.error(f"[Actions] Error rejecting action: {e}")
            return {"success": False, "error": str(e)}

    async def _send_confirmation_email(
        self,
        action: Dict[str, Any],
        brand: Dict[str, Any],
        execution_result: Dict[str, Any]
    ):
        """Send branded confirmation email to customer."""
        try:
            from production.channels.gmail_handler import gmail_handler

            action_type = action["action_type"]
            customer_email = action["customer_email"]
            customer_name = action.get("customer_name", "Valued Customer")
            order_id = action.get("order_id", "N/A")

            # Build branded email
            brand_name = brand.get("name", "Support")
            sender_name = brand.get("sender_name", brand_name)
            signature = brand.get("email_signature", f"— The {brand_name} Team")

            if action_type == ActionType.REFUND.value:
                subject = f"Your Refund Has Been Processed - Order #{order_id}"
                body = f"""Hi {customer_name},

Great news! Your refund request for order #{order_id} has been approved and processed.

Refund Details:
- Order: #{order_id}
- Status: Processed

The refund will appear in your original payment method within 5-10 business days, depending on your bank.

If you have any questions, just reply to this email.

{signature}
"""
            elif action_type == ActionType.CANCEL_ORDER.value:
                subject = f"Your Order Has Been Cancelled - Order #{order_id}"
                body = f"""Hi {customer_name},

Your cancellation request for order #{order_id} has been processed.

If you were charged, a full refund will be issued to your original payment method within 5-10 business days.

Need anything else? Just reply to this email.

{signature}
"""
            elif action_type == ActionType.CHANGE_ADDRESS.value:
                subject = f"Shipping Address Updated - Order #{order_id}"
                body = f"""Hi {customer_name},

Your shipping address for order #{order_id} has been updated successfully.

Your order will now be shipped to the new address you provided.

If you have any questions, just reply to this email.

{signature}
"""
            else:
                return

            await gmail_handler.send_reply(
                to_email=customer_email,
                subject=subject,
                body=body
            )

            logger.info(f"[Actions] Confirmation email sent to {customer_email}")

        except Exception as e:
            logger.error(f"[Actions] Error sending confirmation email: {e}")

    async def _send_rejection_email(
        self,
        action: Dict[str, Any],
        brand: Dict[str, Any],
        rejection_reason: str
    ):
        """Send branded rejection email to customer."""
        try:
            from production.channels.gmail_handler import gmail_handler

            customer_email = action["customer_email"]
            customer_name = action.get("customer_name", "Valued Customer")
            order_id = action.get("order_id", "N/A")
            brand_name = brand.get("name", "Support")
            signature = brand.get("email_signature", f"— The {brand_name} Team")

            subject = f"Update on Your Request - Order #{order_id}"
            body = f"""Hi {customer_name},

Thank you for reaching out about order #{order_id}.

After reviewing your request, we were unable to process it at this time.

Reason: {rejection_reason}

If you have questions or believe this was a mistake, please reply to this email and a team member will assist you.

{signature}
"""

            await gmail_handler.send_reply(
                to_email=customer_email,
                subject=subject,
                body=body
            )

            logger.info(f"[Actions] Rejection email sent to {customer_email}")

        except Exception as e:
            logger.error(f"[Actions] Error sending rejection email: {e}")

    async def _log_action(
        self,
        action_id: str,
        event: str,
        actor: str,
        details: Dict[str, Any]
    ):
        """Log action event for audit trail."""
        try:
            log_entry = {
                "action_id": action_id,
                "event": event,
                "actor": actor,
                "details": details,
            }
            supabase_insert("action_logs", log_entry)
        except Exception as e:
            logger.warning(f"[Actions] Failed to log action: {e}")

    async def get_action_stats(self, brand_id: str = None) -> Dict[str, Any]:
        """Get action statistics, optionally filtered by brand."""
        try:
            filters = {}
            if brand_id:
                filters["brand_id"] = f"eq.{brand_id}"

            actions = supabase_select("brand_actions", filters)

            stats = {
                "total": len(actions),
                "pending": len([a for a in actions if a.get("status") == ActionStatus.PENDING.value]),
                "executed": len([a for a in actions if a.get("status") == ActionStatus.EXECUTED.value]),
                "rejected": len([a for a in actions if a.get("status") == ActionStatus.REJECTED.value]),
                "failed": len([a for a in actions if a.get("status") == ActionStatus.FAILED.value]),
                "by_type": {
                    "refund": len([a for a in actions if a.get("action_type") == ActionType.REFUND.value]),
                    "cancel_order": len([a for a in actions if a.get("action_type") == ActionType.CANCEL_ORDER.value]),
                    "change_address": len([a for a in actions if a.get("action_type") == ActionType.CHANGE_ADDRESS.value])
                },
                "by_risk": {
                    "low": len([a for a in actions if a.get("risk_level") == "low"]),
                    "medium": len([a for a in actions if a.get("risk_level") == "medium"]),
                    "high": len([a for a in actions if a.get("risk_level") == "high"])
                }
            }

            return stats

        except Exception as e:
            logger.error(f"[Actions] Error getting stats: {e}")
            return {}


# Singleton instance
multi_brand_actions = MultiBrandActionsManager()
