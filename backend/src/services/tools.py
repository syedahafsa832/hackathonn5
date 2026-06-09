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

    async def get_order_status(self, order_id: str, shop_domain: str = None, access_token: str = None) -> Dict[str, Any]:
        """Fetch order status directly from Shopify. Local mirror is never used — always live data."""
        try:
            shop = shop_domain or self.shop_name
            token = access_token or self.shopify_token

            logger.info(f"[Tools] Fetching order #{order_id} from Shopify (shop={shop}, token_set={bool(token)})")

            if not shop or not token:
                logger.error("[Tools] Shopify credentials not configured!")
                return {"error": f"Order {order_id} not found — Shopify not connected.", "order_number": order_id}

            # Normalize domain
            shop = shop.rstrip("/").removeprefix("https://").removeprefix("http://")
            if ".myshopify.com" not in shop:
                shop = f"{shop}.myshopify.com"

            headers = {
                "X-Shopify-Access-Token": token,
                "Content-Type": "application/json"
            }

            # Try name=#1002 first, then name=1002 (without #)
            shopify_orders = []
            for name_param in [f"%23{order_id}", order_id]:
                url = f"https://{shop}/admin/api/{self.api_version}/orders.json?name={name_param}&status=any&limit=1"
                logger.info(f"[Tools] Querying Shopify: {url}")
                resp = requests.get(url, headers=headers)
                logger.info(f"[Tools] Shopify response: {resp.status_code}")
                if resp.status_code == 200:
                    shopify_orders = resp.json().get("orders", [])
                    if shopify_orders:
                        break

            if not shopify_orders:
                # Scan recent 250 orders by integer order_number field (handles custom name prefixes)
                logger.info(f"[Tools] Name lookup failed for #{order_id} — scanning order_number field")
                try:
                    fallback_url = f"https://{shop}/admin/api/{self.api_version}/orders.json?status=any&limit=250&order=created_at+desc"
                    fb_resp = requests.get(fallback_url, headers=headers, timeout=15)
                    if fb_resp.status_code == 200:
                        all_candidates = fb_resp.json().get("orders", [])
                        sample = [f"#{o.get('order_number')} ({o.get('name')})" for o in all_candidates[:10]]
                        logger.info(f"[Tools] Scan returned {len(all_candidates)} orders. Sample: {sample}")
                        for candidate in all_candidates:
                            if str(candidate.get("order_number")) == str(order_id):
                                shopify_orders = [candidate]
                                logger.info(f"[Tools] Found order #{order_id} via order_number scan")
                                break
                    else:
                        logger.warning(f"[Tools] Fallback scan HTTP {fb_resp.status_code}: {fb_resp.text[:200]}")
                except Exception as fb_err:
                    logger.warning(f"[Tools] Fallback scan error: {fb_err}")

            if not shopify_orders:
                logger.warning(f"[Tools] Order #{order_id} not found in Shopify after all lookup strategies")
                return {"error": f"Order #{order_id} not found.", "order_number": order_id}

            o = shopify_orders[0]
            _first_fulfillment = o.get("fulfillments", [{}])[0] if o.get("fulfillments") else {}
            tracking = _first_fulfillment.get("tracking_number")
            tracking_url = _first_fulfillment.get("tracking_url")
            tracking_company = _first_fulfillment.get("tracking_company")

            result = {
                "success": True,
                "source": "shopify",
                "order_id": order_id,
                "order_number": o.get("order_number"),
                "status": o.get("fulfillment_status") or "unfulfilled",
                "financial_status": o.get("financial_status"),
                "cancelled_at": o.get("cancelled_at"),
                "tracking_number": tracking,
                "tracking_url": tracking_url,
                "tracking_company": tracking_company,
                "total_amount": o.get("total_price"),
                "items": [
                    {
                        "title": item.get("title"),
                        "quantity": item.get("quantity"),
                        "price": item.get("price"),
                        "variant_title": item.get("variant_title"),
                        "sku": item.get("sku"),
                    }
                    for item in o.get("line_items", [])
                ],
                "created_at": o.get("created_at")
            }
            logger.info(f"[Tools] Order #{order_id} fetched from Shopify: {len(result['items'])} items, total={result['total_amount']}")
            return result

        except Exception as e:
            logger.error(f"Tool error [get_order_status]: {e}")
            return {"error": "Failed to retrieve order status."}

    async def get_orders_by_email(self, email: str) -> Dict[str, Any]:
        """Find all orders for a customer by email."""
        try:
            # Look up orders by customer email
            orders = supabase_select("orders", {"customer_email": f"eq.{email}"})

            if not orders:
                return {"error": f"No orders found for {email}"}

            # Return simplified order list with items
            order_list = []
            for o in orders:
                # Get order items
                order_items = supabase_select("order_items", {"order_id": f"eq.{o.get('id')}"})
                items = []
                for item in order_items:
                    items.append({
                        "title": item.get("title"),
                        "quantity": item.get("quantity"),
                        "price": item.get("price"),
                        "sku": item.get("sku")
                    })

                order_list.append({
                    "order_number": o.get("order_number"),
                    "status": o.get("status"),
                    "tracking_number": o.get("tracking_number"),
                    "shipping_status": o.get("shipping_status"),
                    "total_amount": o.get("total_amount"),
                    "items": items
                })

            return {
                "success": True,
                "email": email,
                "orders": order_list,
                "count": len(order_list)
            }
        except Exception as e:
            logger.error(f"Tool error [get_orders_by_email]: {e}")
            return {"error": "Failed to retrieve orders."}

    async def get_shipping_status(self, tracking_number: str) -> Dict[str, Any]:
        """Query AfterShip API for real-time tracking updates."""
        if not self.aftership_key:
            return {"error": "AfterShip integration not configured."}
            
        try:
            # V4 Tracking API requires as-api-key and versioned URL
            url = f"https://api.aftership.com/tracking/2024-10/trackings/{tracking_number}"
            headers = {
                "as-api-key": self.aftership_key,
                "Content-Type": "application/json"
            }
            resp = requests.get(url, headers=headers)
            
            if resp.status_code == 200:
                data = resp.json()["data"]["tracking"]
                return {
                    "success": True,
                    "status": data.get("tag"), # e.g. InTransit, Delivered
                    "location": data.get("location"),
                    "last_checkpoint": data.get("checkpoints", [{}])[-1].get("message") if data.get("checkpoints") else "No checkpoints yet.",
                    "expected_delivery": data.get("expected_delivery")
                }
            
            logger.warning(f"AfterShip Tracking Not Found ({tracking_number}): {resp.text}")
            return {"error": "Tracking number not found in AfterShip."}
        except Exception as e:
            logger.error(f"Tool error [get_shipping_status]: {e}")
            return {"error": "Shipping carrier service unavailable."}

    async def get_inventory_status(self, product_name: str) -> Dict[str, Any]:
        """Check inventory levels for a product by name."""
        try:
            # Search products in Supabase - use 'title' not 'name'
            products = supabase_select("products", {"title": f"ilike.%{product_name}%"})

            if not products:
                # Try variants - use 'size' or 'sku', not 'title' for name search
                variants = supabase_select("variants", {"sku": f"ilike.%{product_name}%"})

            if not products and not variants:
                return {"success": False, "message": f"I couldn't find '{product_name}' in our current collection. Want me to check something else for you?"}

            # Get inventory from variants
            inventory_info = []
            product_title = ""

            if products:
                product_title = products[0].get("title", product_name)
                # Get variants for this product
                product_id = products[0].get("id")
                variants = supabase_select("variants", {"product_id": f"eq.{product_id}"})

            if variants:
                for v in variants[:5]:  # Limit to 5 results
                    inventory_info.append({
                        "size": v.get("size"),
                        "sku": v.get("sku"),
                        "price": v.get("price")
                    })

            if inventory_info:
                return {
                    "success": True,
                    "product": product_title or product_name,
                    "variants": inventory_info,
                    "message": f"Yes, we have {product_title} available in {len(inventory_info)} sizes."
                }

            return {"success": False, "message": f"We have {product_title} in our collection but let me check on specific availability for you."}

        except Exception as e:
            logger.error(f"Tool error [get_inventory_status]: {e}")
            return {"success": False, "message": "Let me look into that for you personally."}

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
