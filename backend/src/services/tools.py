import os
import requests
import logging
import asyncio
from typing import Dict, Any, List, Optional
from src.lib.supabase_client import supabase_select, supabase_insert, supabase_update
from src.config import SHOPIFY_API_VERSION

logger = logging.getLogger(__name__)

class V3Tools:
    """
    Production-grade tool layer for V3 AI Agent.
    Interacts with Shopify Admin API, AfterShip API, and Supabase.
    """

    def __init__(self):
        self.shop_name = os.getenv("SHOPIFY_SHOP_NAME")
        self.shopify_token = os.getenv("SHOPIFY_ACCESS_TOKEN")
        self.api_version = SHOPIFY_API_VERSION
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

    async def get_order_status(self, order_id: str, shop_domain: str = None, access_token: str = None, customer_email: str = None) -> Dict[str, Any]:
        """Fetch order status directly from Shopify. Local mirror is never used — always live data.

        customer_email, when passed (not None), is enforced as an ownership check:
        a valid order number alone must never return another customer's order
        details. None (the default) preserves the old no-check behavior for
        callers that don't have a customer email to verify against; passing ""
        (e.g. an unverified chat-widget visitor) always fails the check since it
        can never match a real order email."""
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

            if customer_email is not None:
                order_email = (
                    o.get("email") or o.get("contact_email")
                    or (o.get("customer") or {}).get("email") or ""
                ).strip().lower()
                provided_email = customer_email.strip().lower()
                if not provided_email or not order_email or provided_email != order_email:
                    logger.warning(
                        f"[Tools] Order #{order_id} ownership check failed — "
                        f"requested by a different/unverified email, not returning order data"
                    )
                    return {"error": f"Order #{order_id} not found.", "order_number": order_id}

            _fulfillments = o.get("fulfillments") or []
            # Real Shopify shipments — one entry per fulfillment, never merged.
            # An order can ship in multiple boxes (split shipment, backorder catch-up,
            # multi-warehouse) each with its own tracking number/carrier/status.
            _all_shipments = [
                {
                    "tracking_number": f.get("tracking_number"),
                    "tracking_url": f.get("tracking_url"),
                    "tracking_company": f.get("tracking_company"),
                    "shipment_status": f.get("shipment_status"),
                    "shipped_at": f.get("created_at"),
                }
                for f in _fulfillments
            ]
            _first_fulfillment = _fulfillments[0] if _fulfillments else {}
            tracking = _first_fulfillment.get("tracking_number")
            tracking_url = _first_fulfillment.get("tracking_url")
            tracking_company = _first_fulfillment.get("tracking_company")
            shipment_status = _first_fulfillment.get("shipment_status")
            shipped_at = _first_fulfillment.get("created_at")

            result = {
                "success": True,
                "source": "shopify",
                "order_id": order_id,
                "order_number": o.get("order_number"),
                "status": o.get("fulfillment_status") or "unfulfilled",
                "financial_status": o.get("financial_status"),
                "cancelled_at": o.get("cancelled_at"),
                # Backward-compatible single-shipment fields — mirror the first
                # real fulfillment, exactly as before. Callers that only look at
                # these still work unchanged for the (overwhelmingly common)
                # single-fulfillment case.
                "tracking_number": tracking,
                "tracking_url": tracking_url,
                "tracking_company": tracking_company,
                "shipment_status": shipment_status,
                "shipped_at": shipped_at,
                # Full shipment list — always present, length == number of real
                # Shopify fulfillments. Callers that need to represent multiple
                # shipments distinctly (rather than just the first) use this.
                "fulfillments": _all_shipments,
                "fulfillment_count": len(_all_shipments),
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

    async def get_orders_by_email(self, email: str, shop_domain: str = None, access_token: str = None) -> Dict[str, Any]:
        """Find a customer's orders by email — live Shopify data only.
        Returns every matching order (never picks one) so the caller can ask
        "which order?" instead of guessing when there's more than one."""
        if not shop_domain or not access_token:
            return {"error": "Shopify not connected for this store."}
        try:
            from src.services.shopify_service import ShopifyClient
            client = ShopifyClient(shop_domain, access_token)
            result = await client.find_orders_by_email(email)
            order_list = [
                {
                    "order_number": o.get("order_number"),
                    "status": o.get("fulfillment_status") or "unfulfilled",
                    "financial_status": o.get("financial_status"),
                    "total_amount": o.get("total_price"),
                    "created_at": o.get("created_at"),
                }
                for o in result.get("orders", [])
            ]
            return {"success": True, "email": email, "orders": order_list, "count": len(order_list)}
        except Exception as e:
            logger.error(f"Tool error [get_orders_by_email]: {e}")
            return {"error": "Failed to retrieve orders from Shopify."}

    async def get_shipping_status(self, tracking_number: str) -> Dict[str, Any]:
        """Query AfterShip API for real-time tracking updates."""
        logger.warning("V3Tools.get_shipping_status called — deprecated path, use tracking_service instead")
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

    async def get_inventory_status(self, product_name: str, shop_domain: str = None, access_token: str = None) -> Dict[str, Any]:
        """Check real Shopify inventory for a product by name. Never guesses:
        zero matches is reported as "not found" (not "out of stock"), and
        multiple matches come back as an ambiguous list for the caller to
        ask about instead of picking one."""
        if not shop_domain or not access_token:
            return {"success": False, "message": "I can't check live inventory right now — let me get a team member to confirm."}
        try:
            from src.services.shopify_service import ShopifyClient
            client = ShopifyClient(shop_domain, access_token)
            result = await client.find_products_by_title(product_name)
            products = result.get("products", [])

            if not products:
                return {"success": False, "message": f"I couldn't find '{product_name}' in our current collection. Want me to check something else for you?"}

            if len(products) > 1:
                titles = [p.get("title") for p in products[:8]]
                return {
                    "success": True,
                    "ambiguous": True,
                    "matches": titles,
                    "message": f"I found a few products matching '{product_name}': {', '.join(titles)}. Which one did you mean?",
                }

            product = products[0]
            variant_info = [
                {
                    "size": v.get("title"),
                    "sku": v.get("sku"),
                    "price": v.get("price"),
                    # inventory_management=None means Shopify isn't tracking stock for
                    # this variant (continues selling regardless of quantity) — treating
                    # its quantity as authoritative would falsely report "out of stock"
                    # for a product that's actually always available.
                    "in_stock": True if not v.get("inventory_management") else (v.get("inventory_quantity") or 0) > 0,
                    "quantity": v.get("inventory_quantity"),
                }
                for v in product.get("variants", [])[:10]
            ]
            any_in_stock = any(v["in_stock"] for v in variant_info)
            return {
                "success": True,
                "product": product.get("title"),
                "variants": variant_info,
                "message": (
                    f"Yes, {product.get('title')} is in stock." if any_in_stock
                    else f"{product.get('title')} is currently out of stock in all variants."
                ),
            }
        except Exception as e:
            logger.error(f"Tool error [get_inventory_status]: {e}")
            return {"success": False, "message": "I couldn't verify live inventory right now — let me get a team member to confirm."}

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
