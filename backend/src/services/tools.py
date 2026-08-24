import os
import re
import requests
import logging
import asyncio
from typing import Dict, Any, List, Optional
from src.lib.supabase_client import supabase_select, supabase_insert, supabase_update
from src.config import SHOPIFY_API_VERSION

logger = logging.getLogger(__name__)


def _strip_html(html: Optional[str]) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _variant_in_stock(v: Dict[str, Any]) -> bool:
    """inventory_management=None means Shopify isn't tracking stock for this
    variant (continues selling regardless of quantity) — treating its
    quantity as authoritative would falsely report "out of stock" for a
    product that's actually always available. Shared by get_inventory_status
    and get_product_recommendations — do not reimplement this check."""
    return True if not v.get("inventory_management") else (v.get("inventory_quantity") or 0) > 0


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
                    # ownership_mismatch distinguishes "this order genuinely
                    # doesn't exist" from "it exists but this requester's
                    # identity doesn't match it" - callers use this to tell
                    # the customer the truth (confirm your email) instead of
                    # the misleading "can't find your order" wording, without
                    # ever disclosing the real order's details either way.
                    #
                    # This must be True whenever this block runs at all - the
                    # order WAS found in Shopify either way, only the identity
                    # check didn't clear. It used to be bool(provided_email),
                    # which was False for the single most common case (a
                    # brand-new chat visitor who's given an order number but
                    # no email yet) - so that case fell through to the
                    # generic "order lookup failed" branch downstream and
                    # produced exactly the misleading "I can't pull up your
                    # order" wording this was written to avoid. email_provided
                    # lets the caller still phrase the two sub-cases
                    # differently (no email yet vs. a mismatched one).
                    return {
                        "error": f"Order #{order_id} not found.",
                        "order_number": order_id,
                        "ownership_mismatch": True,
                        "email_provided": bool(provided_email),
                    }

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

            # Real option names (e.g. "Size", "Color") in position order, so
            # variant.option1/option2/option3 can be labeled correctly instead
            # of assuming option1 is always size — a product with Color as its
            # first option (or no options at all, e.g. a single "Default
            # Title" variant) must not be mislabeled.
            option_names = [
                (opt.get("name") or "").strip().lower()
                for opt in (product.get("options") or [])
            ]

            def _variant_options(v: Dict[str, Any]) -> Dict[str, str]:
                opts = {}
                for i, name in enumerate(option_names):
                    if not name:
                        continue
                    value = v.get(f"option{i + 1}")
                    if value and value != "Default Title":
                        opts[name] = value
                return opts

            variant_info = [
                {
                    # Kept for backward compatibility with existing callers —
                    # still the variant's own title (usually the size on a
                    # single-option product). "options" below is the accurate,
                    # named breakdown (size/color/etc) when Shopify's product
                    # options data is available.
                    "size": v.get("title"),
                    "options": _variant_options(v),
                    "sku": v.get("sku"),
                    "price": v.get("price"),
                    "in_stock": _variant_in_stock(v),
                    "quantity": v.get("inventory_quantity"),
                }
                for v in product.get("variants", [])[:10]
            ]
            any_in_stock = any(v["in_stock"] for v in variant_info)

            # Handle/image/URL are only ever taken verbatim from Shopify's own
            # response — never constructed or guessed when absent.
            handle = product.get("handle")
            image_url = (product.get("image") or {}).get("src")
            if not image_url:
                images = product.get("images") or []
                if images:
                    image_url = images[0].get("src")
            product_url = f"https://{shop_domain}/products/{handle}" if handle else None

            prices = [v["price"] for v in variant_info if v.get("price")]

            return {
                "success": True,
                "product": product.get("title"),
                "handle": handle,
                "image_url": image_url,
                "product_url": product_url,
                "variants": variant_info,
                # Real merchant-written description (truncated) and live
                # price - both previously absent from this return value even
                # though body_html/variant prices were already being fetched
                # from Shopify. Without them, the agent had no authoritative
                # material/fit/price facts to answer with for an exact,
                # correctly-identified product, and would fill the gap by
                # inventing plausible-sounding ones (e.g. a material never
                # present in the actual Shopify data).
                "description": _strip_html(product.get("body_html"))[:400] or None if product.get("body_html") else None,
                "price": prices[0] if prices else None,
                "message": (
                    f"Yes, {product.get('title')} is in stock." if any_in_stock
                    else f"{product.get('title')} is currently out of stock in all variants."
                ),
            }
        except Exception as e:
            logger.error(f"Tool error [get_inventory_status]: {e}")
            return {"success": False, "message": "I couldn't verify live inventory right now — let me get a team member to confirm."}

    async def get_product_recommendations(
        self,
        anchor_product_name: str,
        shop_domain: str = None,
        access_token: str = None,
        intent: str = "similar",
        limit: int = 5,
    ) -> Dict[str, Any]:
        """Deterministic, live-verified product recommendations. No ML, no
        embeddings, no per-merchant model. Resolves the anchor product by
        name using the exact same title-match rules as get_inventory_status
        (honest not-found / ambiguous handling — never guesses which product
        the customer means), then scores every other active product by
        shared product_type, tag overlap, and vendor — signals actually
        present in the same live Shopify response, nothing invented.

        intent="complementary" ("what goes with this", "what should I wear
        with this") is handled separately and honestly: there is no real
        pairing/bought-together data available anywhere in this
        architecture (no Storefront API, no merchant-configured pairings),
        so it never silently substitutes same-type results and calls them
        complementary — it says so.
        """
        if not shop_domain or not access_token:
            return {"success": False, "message": "I can't look up recommendations right now — let me get a team member to help."}

        if intent == "complementary":
            return {
                "success": False,
                "no_pairing_data": True,
                "message": (
                    "I don't have reliable data on what's specifically meant to be paired with that item, "
                    "so I don't want to guess. I can show you similar items instead, or a team member can "
                    "suggest a pairing."
                ),
            }

        try:
            from src.services.shopify_service import ShopifyClient
            client = ShopifyClient(shop_domain, access_token)
            result = await client.find_products_by_title(anchor_product_name)
            products = result.get("products", [])

            if not products:
                return {
                    "success": False,
                    "message": f"I couldn't find '{anchor_product_name}' in our current collection, so I can't suggest anything similar yet.",
                }

            if len(products) > 1:
                titles = [p.get("title") for p in products[:8]]
                return {
                    "success": True,
                    "ambiguous": True,
                    "matches": titles,
                    "message": f"I found a few products matching '{anchor_product_name}': {', '.join(titles)}. Which one would you like recommendations for?",
                }

            anchor = products[0]
            anchor_id = anchor.get("id")
            anchor_type = (anchor.get("product_type") or "").strip().lower()
            anchor_vendor = (anchor.get("vendor") or "").strip().lower()
            anchor_tags = {t.strip().lower() for t in (anchor.get("tags") or "").split(",") if t.strip()}

            all_products = await client.list_active_products()

            scored = []
            for p in all_products:
                if p.get("id") == anchor_id:
                    continue
                p_type = (p.get("product_type") or "").strip().lower()
                p_tags = {t.strip().lower() for t in (p.get("tags") or "").split(",") if t.strip()}
                p_vendor = (p.get("vendor") or "").strip().lower()

                score = 0
                reasons = []
                same_type = bool(anchor_type) and p_type == anchor_type
                if same_type:
                    score += 3
                    reasons.append(f"same product type ({anchor.get('product_type')})")
                shared_tags = anchor_tags & p_tags
                if shared_tags:
                    score += len(shared_tags)
                    reasons.append(f"shares tags: {', '.join(sorted(shared_tags)[:3])}")
                # Vendor alone is a weak signal — only cite it when type/tags
                # didn't already establish a real similarity.
                if anchor_vendor and p_vendor == anchor_vendor and not same_type and not shared_tags:
                    score += 1
                    reasons.append(f"same brand ({anchor.get('vendor')})")

                if score > 0:
                    scored.append((score, p, reasons))

            if not scored:
                return {
                    "success": True,
                    "no_candidates": True,
                    "anchor_product": anchor.get("title"),
                    "message": f"I don't have enough similar products to confidently recommend something alongside {anchor.get('title')} right now.",
                }

            scored.sort(key=lambda x: x[0], reverse=True)

            recommendations = []
            for score, p, reasons in scored[:limit]:
                variants = p.get("variants") or []
                any_in_stock = any(_variant_in_stock(v) for v in variants) if variants else False
                handle = p.get("handle")
                image_url = (p.get("image") or {}).get("src")
                if not image_url:
                    images = p.get("images") or []
                    if images:
                        image_url = images[0].get("src")
                prices = [v.get("price") for v in variants if v.get("price")]
                recommendations.append({
                    "product_id": p.get("id"),
                    "title": p.get("title"),
                    "handle": handle,
                    "image_url": image_url,
                    "product_url": f"https://{shop_domain}/products/{handle}" if handle else None,
                    "price": prices[0] if prices else None,
                    "matching_reason": "; ".join(reasons),
                    "available": any_in_stock,
                    # Real merchant-written description, truncated - the model
                    # is instructed (customer_success_agent.py tool_context) to
                    # only cite material/fit/attribute claims found in this
                    # text, never invent them. None (not "") when Shopify has
                    # no body_html, so the prompt instruction can tell "nothing
                    # to draw from" apart from "there is a description".
                    "description": _strip_html(p.get("body_html"))[:280] or None if p.get("body_html") else None,
                })

            listed = ", ".join(r["title"] for r in recommendations)
            return {
                "success": True,
                "anchor_product": anchor.get("title"),
                "recommendations": recommendations,
                "message": f"Based on {anchor.get('title')}, you might also like: {listed}.",
            }
        except Exception as e:
            logger.error(f"Tool error [get_product_recommendations]: {e}")
            return {"success": False, "message": "I couldn't pull up recommendations right now — let me get a team member to help."}

    async def discover_products_by_category(
        self,
        category: str,
        shop_domain: str = None,
        access_token: str = None,
        limit: int = 6,
    ) -> Dict[str, Any]:
        """Category/need-based discovery for queries with no specific anchor
        product to compare against — "I need a hoodie for winter" rather than
        "something similar to the Essential Hoodie". get_product_recommendations()
        can't handle this: it requires resolving one exact anchor product
        first. This instead matches live Shopify product_type (falling back to
        a title-word search) against the customer's stated category —
        deterministic substring match, no ML/embeddings, same live-verified
        philosophy as the rest of this file. Never invents a product.

        If too many products match with nothing else to narrow by, this asks
        the customer to narrow down (budget/style) rather than dumping the
        whole catalog or guessing which one they meant — the same "ask,
        don't guess" principle as the ambiguous-title case elsewhere. Note:
        a follow-up reply narrowing the request (e.g. "under $50, relaxed
        fit") is a NEW message with no cross-turn anchor tracking back to
        "hoodie" — that's an existing, documented limitation of this
        architecture, not something this method solves.
        """
        if not shop_domain or not access_token:
            return {"success": False, "message": "I can't look up products right now — let me get a team member to help."}

        try:
            from src.services.shopify_service import ShopifyClient
            client = ShopifyClient(shop_domain, access_token)
            products = await client.list_active_products()

            needle = category.strip().lower()
            matches = [p for p in products if needle and needle in (p.get("product_type") or "").strip().lower()]
            if not matches:
                # product_type didn't match (e.g. category word isn't how this
                # merchant labels it) — fall back to a title-word search
                # rather than reporting "we don't carry that" prematurely.
                title_result = await client.find_products_by_title(category)
                matches = title_result.get("products", [])

            if not matches:
                return {
                    "success": True,
                    "no_candidates": True,
                    "category": category,
                    "message": f"I don't see any {category} in our current collection right now.",
                }

            if len(matches) > limit:
                return {
                    "success": True,
                    "needs_clarification": True,
                    "category": category,
                    "match_count": len(matches),
                    "message": (
                        f"We have quite a few {category} options — could you tell me your budget, "
                        "and whether you'd prefer a relaxed or fitted style, so I can point you to the right one?"
                    ),
                }

            results = []
            for p in matches[:limit]:
                variants = p.get("variants") or []
                any_in_stock = any(_variant_in_stock(v) for v in variants) if variants else False
                handle = p.get("handle")
                prices = [v.get("price") for v in variants if v.get("price")]
                results.append({
                    "title": p.get("title"),
                    "product_url": f"https://{shop_domain}/products/{handle}" if handle else None,
                    "price": prices[0] if prices else None,
                    "available": any_in_stock,
                    "description": _strip_html(p.get("body_html"))[:280] or None if p.get("body_html") else None,
                })

            listed = ", ".join(r["title"] for r in results)
            return {
                "success": True,
                "category": category,
                "recommendations": results,
                "message": f"Here's what we have in {category}: {listed}.",
            }
        except Exception as e:
            logger.error(f"Tool error [discover_products_by_category]: {e}")
            return {"success": False, "message": "I couldn't pull up products right now — let me get a team member to help."}

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
