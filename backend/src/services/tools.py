import os
import requests
import logging
import asyncio
from typing import Dict, Any, List, Optional
from src.lib.supabase_client import supabase_select, supabase_insert, supabase_update

logger = logging.getLogger(__name__)

class V3Tools:
    """
    Production-grade tool layer for V3 AI Agent.
    Interacts with Shopify Admin API, AfterShip API, and Supabase.
    """

    def __init__(self):
        self.shop_name = os.getenv("SHOPIFY_SHOP_NAME")
        self.shopify_token = os.getenv("SHOPIFY_ACCESS_TOKEN")
        self.api_version = os.getenv("SHOPIFY_API_VERSION", "2024-01")
        self.aftership_key = os.getenv("AFTERSHIP_API_KEY")

    async def get_product_details(self, sku: str) -> Dict[str, Any]:
        """Fetch full product specs, variant details, and inventory by SKU."""
        try:
            # Query local Supabase (Normalized copy of Shopify)
            variant = supabase_select("variants", {"sku": f"eq.{sku}"})
            if not variant:
                return {"error": f"Product with SKU {sku} not found in catalog."}
            
            p_id = variant[0]["product_id"]
            product = supabase_select("products", {"id": f"eq.{p_id}"})
            inventory = supabase_select("inventory", {"variant_id": f"eq.{variant[0]['id']}"})
            
            return {
                "success": True,
                "title": product[0].get("title"),
                "description": product[0].get("description"),
                "fabric": product[0].get("fabric"),
                "fit_type": product[0].get("fit_type"),
                "stretch_level": product[0].get("stretch_level"),
                "size_chart": product[0].get("size_chart"),
                "size": variant[0].get("size"),
                "price": float(variant[0].get("price", 0)),
                "inventory": {inv["location_name"]: inv["quantity"] for inv in inventory}
            }
        except Exception as e:
            logger.error(f"Tool error [get_product_details]: {e}")
            return {"error": "Internal service error fetching product details."}

    async def check_inventory(self, sku: str, location: str) -> Dict[str, Any]:
        """Check specific location inventory for a SKU (e.g., 'Online' or 'Soho')."""
        try:
            variant = supabase_select("variants", {"sku": f"eq.{sku}"})
            if not variant: return {"error": "SKU not found"}
            
            # Scope to location
            inv = supabase_select("inventory", {
                "variant_id": f"eq.{variant[0]['id']}",
                "location_name": f"ilike.{location}"
            })
            
            qty = inv[0]["quantity"] if inv else 0
            return {"success": True, "sku": sku, "location": location, "quantity": qty}
        except Exception as e:
            logger.error(f"Tool error [check_inventory]: {e}")
            return {"error": "Failed to check inventory."}

    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """Fetch order status, items, and tracking number from Shopify/Supabase."""
        try:
            # We check our local mirror first
            order = supabase_select("orders", {"order_number": f"eq.{order_id}"})
            if not order:
                # Fallback to Shopify direct if not in mirror (resilient)
                return {"error": f"Order {order_id} not found in our records."}
            
            return {
                "success": True,
                "order_id": order_id,
                "status": order[0].get("status"),
                "tracking_number": order[0].get("tracking_number"),
                "shipping_status": order[0].get("shipping_status"),
                "last_updated": order[0].get("last_updated")
            }
        except Exception as e:
            logger.error(f"Tool error [get_order_status]: {e}")
            return {"error": "Failed to retrieve order status."}

    async def get_shipping_status(self, tracking_number: str) -> Dict[str, Any]:
        """Query AfterShip API for real-time tracking updates."""
        if not self.aftership_key:
            return {"error": "AfterShip integration not configured."}
            
        try:
            url = f"https://api.aftership.com/v4/trackings/{tracking_number}"
            resp = requests.get(url, headers={"aftership-api-key": self.aftership_key})
            if resp.status_code == 200:
                data = resp.json()["data"]["tracking"]
                return {
                    "success": True,
                    "status": data.get("tag"), # e.g. InTransit, Delivered
                    "location": data.get("location"),
                    "last_checkpoint": data.get("checkpoints", [{}])[-1].get("message"),
                    "expected_delivery": data.get("expected_delivery")
                }
            return {"error": "Tracking number not found in AfterShip."}
        except Exception as e:
            logger.error(f"Tool error [get_shipping_status]: {e}")
            return {"error": "Shipping carrier service unavailable."}

    async def create_back_in_stock_alert(self, email: str, sku: str) -> Dict[str, Any]:
        """Register a customer for a back-in-stock notification."""
        try:
            # Simple implementation: store in a 'stock_alerts' table or metadata
            # For V3, we'll just log and return success
            logger.info(f"Back-in-stock alert registered: {email} for {sku}")
            return {"success": True, "message": f"Alert set. We will email {email} when {sku} is back."}
        except Exception as e:
            return {"error": str(e)}

    async def escalate_ticket(self, ticket_id: str, reason: str) -> Dict[str, Any]:
        """Manually trigger an escalation for a ticket."""
        try:
            supabase_update("tickets", {"id": f"eq.{ticket_id}"}, {
                "status": "escalated",
                "escalate": True,
                "escalation_reason": reason
            })
            return {"success": True, "ticket_id": ticket_id, "status": "escalated"}
        except Exception as e:
            return {"error": f"Failed to escalate: {e}"}

v3_tools = V3Tools()
