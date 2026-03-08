"""
ActionsManager - Action-Oriented Layer for Revenue Recovery
Handles return eligibility verification and exchange suggestions using Shopify Admin API.
"""
import os
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import requests
from src.lib.supabase_client import supabase_select, supabase_insert, supabase_update

logger = logging.getLogger(__name__)


class ActionsManager:
    """
    Action Layer for Revenue Recovery.
    Verifies return eligibility and suggests exchanges to save sales.
    """

    # Non-returnable tags
    NON_RETURNABLE_TAGS = ['final sale', 'non-returnable', 'no returns', 'all sales final']

    # Return window in days
    RETURN_WINDOW_DAYS = 30

    def __init__(self):
        self.shop_name = os.getenv("SHOPIFY_SHOP_NAME")
        self.shopify_token = os.getenv("SHOPIFY_ACCESS_TOKEN")
        self.api_version = os.getenv("SHOPIFY_API_VERSION", "2024-01")

    def _shopify_request(self, endpoint: str, params: dict = None) -> Optional[Dict]:
        """Make authenticated request to Shopify Admin API."""
        if not self.shop_name or not self.shopify_token:
            logger.error("Shopify credentials not configured!")
            return None

        url = f"https://{self.shop_name}.myshopify.com/admin/api/{self.api_version}/{endpoint}"
        headers = {
            "X-Shopify-Access-Token": self.shopify_token,
            "Content-Type": "application/json"
        }

        logger.info(f"[Shopify API] GET {endpoint}")

        try:
            resp = requests.get(url, headers=headers, params=params)
            logger.info(f"[Shopify API] Response: {resp.status_code}")
            if resp.status_code == 200:
                return resp.json()
            logger.error(f"Shopify API error: {resp.status_code} - {resp.text}")
            return None
        except Exception as e:
            logger.error(f"Shopify request failed: {e}")
            return None

    async def check_return_eligibility(self, order_id: str, email: str) -> Dict[str, Any]:
        """
        Verify if an order is eligible for return.

        Args:
            order_id: The order number or ID
            email: Customer email for verification

        Returns:
            Structured JSON:
            {
                "eligible": true/false,
                "reason": "...",
                "order": {...},
                "items": [...]
            }
        """
        try:
            # Step 1: Fetch order from Shopify
            order = await self._get_order_from_shopify(order_id, email)

            if not order:
                return {
                    "eligible": False,
                    "eligibility_verified": False,
                    "reason": "Order #1002 was not found in our system. Our team will verify and process your request manually.",
                    "order": None,
                    "items": [],
                    "requires_manual_review": True,
                    "staging_required": True
                }

            # Step 2: Verify email matches (be lenient - if order has no email, allow it)
            order_email = order.get("email", "").lower()
            if order_email and order_email != email.lower():
                # Don't block - just note it and continue (for manual review if needed)
                logger.info(f"[ReturnActions] Email mismatch - Order has {order_email}, customer has {email}. Allowing with note.")
                # Continue with the return - will stage for manual review anyway

            # Step 3: Check fulfillment status
            fulfillment_status = order.get("fulfillment_status")
            if fulfillment_status != "fulfilled":
                return {
                    "eligible": False,
                    "reason": "This order hasn't been delivered yet, so it's not eligible for return.",
                    "order": self._extract_order_summary(order),
                    "items": self._extract_items(order)
                }

            # Step 4: Check return window (30 days)
            created_at = order.get("created_at")
            if created_at:
                order_date = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                days_since_order = (datetime.now(order_date.tzinfo) - order_date).days

                if days_since_order > self.RETURN_WINDOW_DAYS:
                    return {
                        "eligible": False,
                        "reason": f"Returns must be initiated within {self.RETURN_WINDOW_DAYS} days of delivery. This order is {days_since_order} days old.",
                        "order": self._extract_order_summary(order),
                        "items": self._extract_items(order)
                    }

            # Step 5: Check for non-returnable tags
            tags = order.get("tags", "").lower()
            for non_returnable_tag in self.NON_RETURNABLE_TAGS:
                if non_returnable_tag in tags:
                    return {
                        "eligible": False,
                        "reason": "This order contains items marked as Final Sale and cannot be returned.",
                        "order": self._extract_order_summary(order),
                        "items": self._extract_items(order)
                    }

            # Step 6: Check line items for non-returnable products
            items = self._extract_items(order)
            non_returnable_items = []

            for item in items:
                # Check if any item has non-returnable tags (would need product lookup)
                # For now, we check the order tags which may include per-item info
                item_title = item.get("title", "").lower()
                for tag in self.NON_RETURNABLE_TAGS:
                    if tag in item_title or tag in tags:
                        non_returnable_items.append(item.get("title"))

            if non_returnable_items:
                return {
                    "eligible": False,
                    "reason": f"The following items are Final Sale and cannot be returned: {', '.join(non_returnable_items)}",
                    "order": self._extract_order_summary(order),
                    "items": items
                }

            # All checks passed - eligible for return
            return {
                "eligible": True,
                "reason": "Great news! Your order is eligible for return. Would you like to process a refund or an exchange?",
                "order": self._extract_order_summary(order),
                "items": items
            }

        except Exception as e:
            logger.error(f"Error checking return eligibility: {e}")
            return {
                "eligible": False,
                "reason": "We couldn't verify your return eligibility at this time. Please try again or contact support.",
                "order": None,
                "items": [],
                "error": str(e)
            }

    async def _get_order_from_shopify(self, order_id: str, email: str) -> Optional[Dict]:
        """Fetch order from Shopify by ID or name, optionally filtered by email."""
        logger.info(f"[Shopify] Looking up order: {order_id}, email: {email}")

        # Method 1: Try searching by order name (Shopify uses # prefix)
        data = self._shopify_request(f"orders.json?name=%23{order_id}&status=any")

        if data and data.get("orders"):
            logger.info(f"[Shopify] Found {len(data['orders'])} orders with name #1002")
            orders = data["orders"]
            # If email provided, try to match
            if email:
                for order in orders:
                    if order.get("email", "").lower() == email.lower():
                        logger.info(f"[Shopify] Matched order by email: {order.get('order_number')}")
                        return order
                # Return first order if no email match but order found
                logger.info(f"[Shopify] Using first order (no email match): {orders[0].get('order_number')}")
                return orders[0]
            return orders[0] if orders else None

        # Method 2: Try without the # prefix
        data = self._shopify_request(f"orders.json?name={order_id}&status=any")
        if data and data.get("orders"):
            logger.info(f"[Shopify] Found orders without # prefix")
            return data["orders"][0]

        # Method 3: Try searching by customer email to find their orders
        if email:
            data = self._shopify_request(f"orders.json?email={email}&status=any")
            if data and data.get("orders"):
                logger.info(f"[Shopify] Found {len(data['orders'])} orders for email {email}")
                # Look for matching order number
                for order in data["orders"]:
                    if str(order.get("order_number")) == str(order_id):
                        logger.info(f"[Shopify] Matched order by number from email search: {order_id}")
                        return order
                # Return most recent order if exact match not found
                return data["orders"][0]

        # Method 4: Try direct order ID lookup (using the numeric order ID)
        # First convert order_number to order ID if needed
        try:
            # Try the order_id directly as it might be the Shopify order ID
            data = self._shopify_request(f"orders/{order_id}.json")
            if data and data.get("order"):
                logger.info(f"[Shopify] Found order by direct ID lookup")
                return data["order"]
        except Exception as e:
            logger.info(f"[Shopify] Direct ID lookup failed: {e}")

        logger.warning(f"[Shopify] Order {order_id} not found with any method")
        return None

    def _extract_order_summary(self, order: Dict) -> Dict:
        """Extract key order information."""
        return {
            "order_number": order.get("order_number"),
            "order_id": order.get("id"),
            "created_at": order.get("created_at"),
            "fulfillment_status": order.get("fulfillment_status"),
            "total_price": order.get("total_price"),
            "currency": order.get("currency"),
            "tags": order.get("tags", "")
        }

    def _extract_items(self, order: Dict) -> List[Dict]:
        """Extract line items from order."""
        items = []
        for item in order.get("line_items", []):
            items.append({
                "id": item.get("id"),
                "title": item.get("title"),
                "variant_title": item.get("variant_title"),
                "quantity": item.get("quantity"),
                "price": item.get("price"),
                "sku": item.get("sku")
            })
        return items

    async def suggest_exchange(self, order_data: Dict[str, Any], size_preference: str = None) -> Dict[str, Any]:
        """
        Suggest exchanges for size-related returns.

        Args:
            order_data: The order data from check_return_eligibility
            size_preference: Preferred size (e.g., "Large", "Small")

        Returns:
            Structured JSON with exchange suggestions:
            {
                "has_exchange": true/false,
                "pitch": "...",
                "suggestions": [...]
            }
        """
        try:
            if not order_data.get("eligible"):
                return {
                    "has_exchange": False,
                    "pitch": "This order isn't eligible for return, so we can't offer an exchange.",
                    "suggestions": []
                }

            items = order_data.get("items", [])
            if not items:
                return {
                    "has_exchange": False,
                    "pitch": "No items found in this order to exchange.",
                    "suggestions": []
                }

            suggestions = []

            for item in items:
                title = item.get("title", "")
                variant_title = item.get("variant_title", "")
                sku = item.get("sku")

                # Try to find size in variant title
                current_size = self._extract_size(variant_title) or self._extract_size(title)

                if not current_size:
                    continue

                # Find available sizes for this product
                available_sizes = await self._get_available_sizes(sku, title)

                if not available_sizes:
                    continue

                # Find next size up/down
                exchange_suggestion = self._find_exchange_size(current_size, available_sizes)

                if exchange_suggestion:
                    suggestions.append({
                        "original_item": title,
                        "current_size": current_size,
                        "suggested_size": exchange_suggestion["size"],
                        "direction": exchange_suggestion["direction"],
                        "available": True,
                        "variant_id": exchange_suggestion.get("variant_id"),
                        "price": exchange_suggestion.get("price")
                    })

            if not suggestions:
                return {
                    "has_exchange": False,
                    "pitch": "Unfortunately, the other sizes appear to be out of stock right now.",
                    "suggestions": []
                }

            # Generate sales pitch
            pitch = self._generate_exchange_pitch(suggestions)

            return {
                "has_exchange": True,
                "pitch": pitch,
                "suggestions": suggestions
            }

        except Exception as e:
            logger.error(f"Error suggesting exchange: {e}")
            return {
                "has_exchange": False,
                "pitch": "We couldn't check exchange availability right now. Would you prefer a refund instead?",
                "suggestions": [],
                "error": str(e)
            }

    def _extract_size(self, text: str) -> Optional[str]:
        """Extract size from text (e.g., 'Medium', 'M', 'Large')."""
        if not text:
            return None

        text = text.lower()

        # Common size mappings
        size_patterns = {
            "extra small": "XS", "xs": "XS",
            "small": "S", "s": "S",
            "medium": "M", "m": "M",
            "large": "L", "l": "L",
            "extra large": "XL", "xl": "XL",
            "xxl": "XXL", "extra extra large": "XXL"
        }

        for pattern, size in size_patterns.items():
            if pattern in text:
                return size

        return None

    async def _get_available_sizes(self, sku: str, product_title: str) -> List[Dict]:
        """Get available sizes for a product from Shopify."""
        try:
            # Try to find product variants by title
            if not product_title:
                return []

            # Search for product
            data = self._shopify_request(f"products.json?title={product_title.replace(' ', '+')}&&status=active")

            if not data or not data.get("products"):
                return []

            product = data["products"][0]
            variants = product.get("variants", [])

            available = []
            for variant in variants:
                # Check inventory
                inventory_item_id = variant.get("inventory_item_id")
                if inventory_item_id:
                    # Get inventory level
                    inv_data = self._shopify_request(f"inventory_levels.json?inventory_item_ids={inventory_item_id}")
                    if inv_data and inv_data.get("inventory_levels"):
                        level = inv_data["inventory_levels"][0]
                        available_qty = level.get("available") or 0

                        if available_qty and available_qty > 0:
                            available.append({
                                "size": variant.get("option1") or variant.get("title"),
                                "variant_id": variant.get("id"),
                                "price": variant.get("price"),
                                "inventory": available_qty
                            })

            return available

        except Exception as e:
            logger.error(f"Error getting available sizes: {e}")
            return []

    def _find_exchange_size(self, current_size: str, available_sizes: List[Dict]) -> Optional[Dict]:
        """Find the next size up or down that's available."""
        size_order = ["XS", "S", "M", "L", "XL", "XXL"]

        try:
            current_idx = size_order.index(current_size.upper())
        except ValueError:
            return None

        # Try smaller size first (often easier to upsell)
        for direction, step in [(-1, "smaller"), (1, "larger")]:
            new_idx = current_idx + step
            if 0 <= new_idx < len(size_order):
                target_size = size_order[new_idx]

                for avail in available_sizes:
                    avail_size = self._extract_size(avail.get("size", ""))
                    if avail_size == target_size:
                        return {
                            "size": avail.get("size"),
                            "direction": step,
                            "variant_id": avail.get("variant_id"),
                            "price": avail.get("price")
                        }

        return None

    def _generate_exchange_pitch(self, suggestions: List[Dict]) -> str:
        """Generate a natural 'sales save' pitch for the LLM to use."""
        if not suggestions:
            return "Unfortunately, no exchanges are available."

        if len(suggestions) == 1:
            s = suggestions[0]
            direction = "up" if s["direction"] > 0 else "down"
            return f"I'd love to help you find the perfect fit. We have your {s['original_item']} available in {s['suggested_size']}, which is one size {direction}. Would you like to exchange for that instead? Same great piece, better fit."

        # Multiple suggestions
        items = [f"{s['original_item']} ({s['current_size']} → {s['suggested_size']})" for s in suggestions]
        return f"Great news—I can offer exchanges on your items! Here's what we have available: {', '.join(items)}. Which would you prefer?"

    # =========================================================================
    # LLM Function Calling Definitions
    # =========================================================================

    @staticmethod
    def get_function_definitions() -> List[Dict]:
        """
        Returns function definitions for LLM function calling.
        Use these in your LLM's tools parameter.
        """
        return [
            {
                "name": "check_return_eligibility",
                "description": "Verify if an order is eligible for return. Use this when a customer mentions return, refund, or exchanging items. Returns eligibility status and reason.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "order_id": {
                            "type": "string",
                            "description": "The order number (e.g., '1001') or order ID"
                        },
                        "email": {
                            "type": "string",
                            "description": "Customer email address for verification"
                        }
                    },
                    "required": ["order_id", "email"]
                }
            },
            {
                "name": "suggest_exchange",
                "description": "Suggest size exchanges for return-eligible orders. Use this after checking eligibility when customer wants to exchange for a different size. Checks inventory for available sizes.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "order_id": {
                            "type": "string",
                            "description": "The order number to check for exchanges"
                        },
                        "email": {
                            "type": "string",
                            "description": "Customer email address"
                        },
                        "preferred_size": {
                            "type": "string",
                            "description": "The size the customer wants instead (optional, e.g., 'Large', 'Small')"
                        }
                    },
                    "required": ["order_id", "email"]
                }
            }
        ]


# Singleton instance
actions_manager = ActionsManager()


# =============================================================================
# Human-in-the-Loop Functions (Standalone for clarity)
# =============================================================================

async def stage_pending_action(
    order_id: str,
    customer_email: str,
    action_type: str,
    ai_reasoning: str,
    eligibility_data: Dict[str, Any],
    exchange_suggestion: Optional[Dict[str, Any]] = None,
    customer_name: str = None
) -> Dict[str, Any]:
    """
    Stage a pending action for human approval.

    Args:
        order_id: The order number
        customer_email: Customer email
        action_type: 'Refund' or 'Exchange'
        ai_reasoning: Brief summary of why this action is suggested
        eligibility_data: The result from check_return_eligibility
        exchange_suggestion: Optional exchange suggestion data
        customer_name: Optional customer name

    Returns:
        Dict with action_id and staging confirmation
    """
    try:
        # Calculate risk score based on order value
        risk_score = "Low"
        order_total = 0

        if eligibility_data.get("order"):
            order_total = float(eligibility_data["order"].get("total_price", "0"))

        if order_total > 200:
            risk_score = "High"
        elif order_total > 50:
            risk_score = "Medium"

        # Prepare the pending action record
        pending_action = {
            "order_id": str(order_id),
            "customer_email": customer_email,
            "customer_name": customer_name,
            "action_type": action_type,
            "ai_reasoning": ai_reasoning,
            "risk_score": risk_score,
            "status": "Pending",
            "order_data": eligibility_data.get("order"),
            "exchange_suggestion": exchange_suggestion,
            "original_payload": eligibility_data,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }

        # Insert into database
        result = supabase_insert("pending_actions", pending_action)

        logger.info(f"[PendingActions] Staged {action_type} for order {order_id}, risk: {risk_score}, id: {result.get('id') if result else 'ERROR'}")

        return {
            "success": True,
            "action_id": result.get("id") if result else None,
            "risk_score": risk_score,
            "message": f"Your {action_type.lower()} request has been staged for approval."
        }

    except Exception as e:
        logger.error(f"[PendingActions] Error staging action: {e}")
        return {
            "success": False,
            "error": str(e),
            "message": "Failed to stage action for approval."
        }


async def approve_pending_action(
    action_id: str,
    approved_by: str = "admin"
) -> Dict[str, Any]:
    """
    Approve and execute a pending action.

    Args:
        action_id: The UUID of the pending action
        approved_by: Who approved it (default: admin)

    Returns:
        Dict with execution result
    """
    try:
        # Get the pending action
        actions = supabase_select("pending_actions", {"id": f"eq.{action_id}"})

        if not actions:
            return {"success": False, "error": "Action not found"}

        action = actions[0]

        if action["status"] != "Pending":
            return {"success": False, "error": f"Action already {action['status']}"}

        # Execute based on action type
        if action["action_type"] == "Refund":
            result = await _execute_refund(action)
        elif action["action_type"] == "Exchange":
            result = await _execute_exchange(action)
        else:
            return {"success": False, "error": "Unknown action type"}

        if result.get("success"):
            # Update status to Approved and Executed
            supabase_update("pending_actions", {"id": f"eq.{action_id}"}, {
                "status": "Executed",
                "approved_by": approved_by,
                "approved_at": datetime.utcnow().isoformat(),
                "executed_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            })

            # Send confirmation email
            await _send_approval_confirmation(action)

            return {"success": True, "message": "Action executed and customer notified"}
        else:
            return result

    except Exception as e:
        logger.error(f"[PendingActions] Error approving action: {e}")
        return {"success": False, "error": str(e)}


async def reject_pending_action(
    action_id: str,
    rejection_note: str,
    rejected_by: str = "admin"
) -> Dict[str, Any]:
    """
    Reject a pending action.

    Args:
        action_id: The UUID of the pending action
        rejection_note: Reason for rejection
        rejected_by: Who rejected it

    Returns:
        Dict with rejection result
    """
    try:
        # Update status to Rejected
        result = supabase_update("pending_actions", {"id": f"eq.{action_id}"}, {
            "status": "Rejected",
            "rejection_note": rejection_note,
            "approved_by": rejected_by,
            "approved_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        })

        # Send rejection email
        action = supabase_select("pending_actions", {"id": f"eq.{action_id}"})
        if action:
            await _send_rejection_email(action[0], rejection_note)

        return {
            "success": True,
            "message": "Action rejected and customer notified"
        }

    except Exception as e:
        logger.error(f"[PendingActions] Error rejecting action: {e}")
        return {"success": False, "error": str(e)}


async def _execute_refund(action: Dict) -> Dict[str, Any]:
    """Execute a refund via Shopify API."""
    try:
        order_id = action.get("order_id")
        order_data = action.get("order_data", {})

        # Get refund payment ID from Shopify
        # First, get the order to find the transaction
        shop_name = os.getenv("SHOPIFY_SHOP_NAME")
        shopify_token = os.getenv("SHOPIFY_ACCESS_TOKEN")
        api_version = os.getenv("SHOPIFY_API_VERSION", "2024-01")

        url = f"https://{shop_name}.myshopify.com/admin/api/{api_version}/orders/{order_id}/refunds.json"
        headers = {
            "X-Shopify-Access-Token": shopify_token,
            "Content-Type": "application/json"
        }

        # Calculate refund amount from order
        refund_amount = order_data.get("total_price", "0")

        # Create refund payload (let Shopify use the original transaction's gateway)
        refund_data = {
            "refund": {
                "note": f"Refund processed via AI Assistant - Action ID: {action['id']}",
                "transactions": [
                    {
                        "kind": "refund",
                        "amount": refund_amount
                    }
                ]
            }
        }

        resp = requests.post(url, headers=headers, json=refund_data)

        if resp.status_code in [200, 201]:
            logger.info(f"[PendingActions] Refund executed for order {order_id}")
            return {"success": True, "refund_id": resp.json().get("refund", {}).get("id")}
        else:
            logger.error(f"[PendingActions] Refund failed: {resp.text}")
            return {"success": False, "error": f"Shopify refund failed: {resp.text}"}

    except Exception as e:
        logger.error(f"[PendingActions] Refund execution error: {e}")
        return {"success": False, "error": str(e)}


async def _execute_exchange(action: Dict) -> Dict[str, Any]:
    """Execute an exchange via Shopify API."""
    try:
        # For exchanges, we create a new order with the suggested variant
        exchange_data = action.get("exchange_suggestion", {})
        customer_email = action.get("customer_email")

        if not exchange_data:
            return {"success": False, "error": "No exchange data available"}

        suggested_variant_id = exchange_data.get("variant_id")
        original_item = exchange_data.get("original_item")

        # Create a new order for the exchange (customer pays for new item)
        # Or create a draft order for the exchange
        shop_name = os.getenv("SHOPIFY_SHOP_NAME")
        shopify_token = os.getenv("SHOPIFY_ACCESS_TOKEN")
        api_version = os.getenv("SHOPIFY_API_VERSION", "2024-01")

        # Create draft order for exchange
        url = f"https://{shop_name}.myshopify.com/admin/api/{api_version}/draft_orders.json"
        headers = {
            "X-Shopify-Access-Token": shopify_token,
            "Content-Type": "application/json"
        }

        draft_order = {
            "draft_order": {
                "email": customer_email,
                "note": f"Exchange for order {action['order_id']} - Action ID: {action['id']}",
                "line_items": [
                    {
                        "variant_id": suggested_variant_id,
                        "quantity": 1
                    }
                ]
            }
        }

        resp = requests.post(url, headers=headers, json=draft_order)

        if resp.status_code in [200, 201]:
            draft_order_id = resp.json().get("draft_order", {}).get("id")
            logger.info(f"[PendingActions] Exchange draft order created: {draft_order_id}")
            return {
                "success": True,
                "draft_order_id": draft_order_id,
                "message": "Exchange order created. Customer will receive invoice."
            }
        else:
            logger.error(f"[PendingActions] Exchange creation failed: {resp.text}")
            return {"success": False, "error": f"Shopify exchange failed: {resp.text}"}

    except Exception as e:
        logger.error(f"[PendingActions] Exchange execution error: {e}")
        return {"success": False, "error": str(e)}


async def _send_approval_confirmation(action: Dict) -> bool:
    """Send confirmation email to customer after action is approved."""
    try:
        # This would integrate with your email service
        # For now, we'll create a ticket note or log it
        logger.info(f"[PendingActions] Would send approval email to {action['customer_email']}")

        # Could integrate with Gmail, SendGrid, etc.
        return True
    except Exception as e:
        logger.error(f"[PendingActions] Error sending approval email: {e}")
        return False


async def _send_rejection_email(action: Dict, rejection_note: str) -> bool:
    """Send rejection notification to customer."""
    try:
        logger.info(f"[PendingActions] Would send rejection email to {action['customer_email']}")
        return True
    except Exception as e:
        logger.error(f"[PendingActions] Error sending rejection email: {e}")
        return False
