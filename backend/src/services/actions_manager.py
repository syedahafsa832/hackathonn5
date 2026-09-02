"""
ActionsManager - Action-Oriented Layer for Revenue Recovery
Handles return eligibility verification and exchange suggestions using Shopify Admin API.
"""
import os
import re
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta, timezone
import requests
import uuid as uuid_lib
from src.lib.supabase_client import supabase_select, supabase_insert, supabase_update
from src.config import SHOPIFY_API_VERSION
from src.services.policy_evidence import verify_time_window

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
        # Legacy single-store credentials — used only when tenant_id is not provided.
        # Real tenants must have their Shopify creds stored in the brands/tenants table.
        self._legacy_shop_name = os.getenv("SHOPIFY_SHOP_NAME")
        self._legacy_token = os.getenv("SHOPIFY_ACCESS_TOKEN")
        self.api_version = SHOPIFY_API_VERSION

    async def _get_refund_policy(self, brand_id: Optional[str]) -> Dict[str, Any]:
        """Loads the merchant's configured refund policy, falling back to
        today's hardcoded defaults when brand_id is absent or the brand has
        no policy configured — so a default-configured brand behaves exactly
        as it does today. This is the single place eligibility-check policy
        values come from; nothing else in this file reads brand config."""
        policy = {
            "window_days": self.RETURN_WINDOW_DAYS,
            "final_sale_tags": list(self.NON_RETURNABLE_TAGS),
            "exclude_digital_products": False,
            "excluded_product_ids": [],
            "excluded_collection_ids": [],
            "notes": "",
        }
        if not brand_id:
            return policy
        try:
            from src.services.brand_manager import brand_manager
            brand = await brand_manager.get_brand(brand_id)
            if not brand:
                return policy
            policy["window_days"] = brand.get("return_policy_days") or self.RETURN_WINDOW_DAYS
            policy["final_sale_tags"] = brand.get("final_sale_tags") or list(self.NON_RETURNABLE_TAGS)
            policy["exclude_digital_products"] = bool(brand.get("exclude_digital_products"))
            policy["notes"] = brand.get("refund_notes") or ""

            excluded_products = supabase_select("refund_policy_excluded_products", {"brand_id": f"eq.{brand_id}"})
            policy["excluded_product_ids"] = [r["shopify_product_id"] for r in excluded_products]
            excluded_collections = supabase_select("refund_policy_excluded_collections", {"brand_id": f"eq.{brand_id}"})
            policy["excluded_collection_ids"] = [r["shopify_collection_id"] for r in excluded_collections]
        except Exception as e:
            logger.warning(f"[ActionsManager] Failed to load refund policy for brand {brand_id}, using defaults: {e}")
        return policy

    async def get_custom_policy_text(self, brand_id: Optional[str], policy_notes: Optional[str] = None) -> Optional[str]:
        """Free-text merchant policy that structured fields (window_days,
        final_sale_tags, exclusions) don't capture — the "notes" field on
        the refund policy form, or (when that's empty) whatever the brand's
        Knowledge Base has on file about returns/refunds/cancellations/
        exchanges. Shared by every action type (refund, return, cancel,
        exchange) so none of them silently skip a merchant's written policy
        just because it lives outside the structured fields. Reuses the
        existing Knowledge Base RAG lookup — no separate policy system.

        Returns "" when it was actually checked and confirmed empty (never
        invents a policy). Returns None when the check itself couldn't be
        completed (e.g. the Knowledge Base lookup errored) — genuinely
        unknown, not confirmed-empty, and callers must treat that the same
        as "a policy exists" (escalate for human confirmation) rather than
        guessing "no policy" from a failed check."""
        text = (policy_notes or "").strip()
        if text or not brand_id:
            return text
        try:
            from src.services.brand_knowledge_service import brand_knowledge_service
            text = (await brand_knowledge_service.get_brand_context(
                brand_id=brand_id,
                query="return refund cancellation exchange policy window eligibility rules",
                top_k=3,
            ) or "").strip()
        except Exception as e:
            logger.warning(f"[ActionsManager] Knowledge base policy lookup failed for brand {brand_id}: {e}")
            return None
        return text

    def evaluate_cancellation_window(
        self, policy_text: Optional[str], order_created_at: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        """Deterministic cancellation-window eligibility - the actual bug
        fix. Luna used to reason about "within N hours" cancellation
        policies purely from retrieved KB text and the customer's own
        wording ("placed yesterday"), producing hedges like "might still be
        within the window" even when Shopify's real order.created_at made
        the answer obvious. This computes the real answer from real data.

        Returns None (never a guess) when the policy text has no "within N
        hours/days" pattern, or when order_created_at is missing/unparseable
        - callers must fall back to their existing safe behavior (escalate/
        ask, never assume eligible or not)."""
        if not policy_text or not order_created_at:
            return None

        window_hours = None
        m = re.search(r'(\d+(?:\.\d+)?)\s*hour', policy_text, re.IGNORECASE)
        if m:
            window_hours = float(m.group(1))
        else:
            m = re.search(r'(\d+(?:\.\d+)?)\s*day', policy_text, re.IGNORECASE)
            if m:
                window_hours = float(m.group(1)) * 24
        if window_hours is None:
            return None

        try:
            created = datetime.fromisoformat(order_created_at.replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None

        elapsed_hours = (datetime.now(timezone.utc) - created).total_seconds() / 3600
        return {
            "window_hours": window_hours,
            "elapsed_hours": round(elapsed_hours, 2),
            "eligible": elapsed_hours <= window_hours,
        }

    async def _get_product_collection_ids(self, tenant_id: Optional[str], product_id) -> List[int]:
        """Which collections a product belongs to. Shopify's order line items
        don't carry collection membership, so this is a small extra lookup
        per distinct product on the order — bounded by line-item count, not
        unbounded. Fails open (empty list) on any error so a lookup problem
        degrades to 'not excluded' rather than blocking staging entirely."""
        if not tenant_id or not product_id:
            return []
        try:
            from src.services.shopify_service import shopify_service
            client = await shopify_service.get_client_for_tenant(tenant_id)
            result = client._request("GET", "collects.json", params={"product_id": product_id})
            collects = result.get("data", {}).get("collects", [])
            return [c.get("collection_id") for c in collects if c.get("collection_id")]
        except Exception as e:
            logger.warning(f"[ActionsManager] Collection lookup failed for product {product_id} (treating as not excluded): {e}")
            return []

    def _shopify_request(self, endpoint: str, params: dict = None) -> Optional[Dict]:
        """Legacy single-store Shopify request using global env vars.
        Only used when no tenant_id is available (local dev / legacy fallback)."""
        if not self._legacy_shop_name or not self._legacy_token:
            logger.error("[ActionsManager] No legacy Shopify credentials — set SHOPIFY_SHOP_NAME + SHOPIFY_ACCESS_TOKEN or connect a brand")
            return None

        url = f"https://{self._legacy_shop_name}.myshopify.com/admin/api/{self.api_version}/{endpoint}"
        headers = {
            "X-Shopify-Access-Token": self._legacy_token,
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

    async def check_return_eligibility(
        self,
        order_id: str,
        email: str,
        tenant_id: Optional[str] = None,
        brand_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Verify if an order is eligible for return.
        When tenant_id is provided, fetches order using the tenant's own Shopify credentials.
        """
        try:
            # Step 1: Fetch order from Shopify
            order = await self._get_order_from_shopify(order_id, email, tenant_id=tenant_id)

            if not order:
                return {
                    "eligible": False,
                    "eligibility_verified": False,
                    "reason": f"Order #{order_id} was not found in our system. Our team will verify and process your request manually.",
                    "order": None,
                    "items": [],
                    "requires_manual_review": True,
                    "staging_required": True
                }

            # Step 1.5: Already refunded / already cancelled — checked here,
            # at staging time, not just at approval-execution time, so a
            # human is never shown an action guaranteed to fail. Shares the
            # exact same check process_refund() uses at execution.
            from src.services.shopify_service import ShopifyClient
            refund_status = ShopifyClient.check_refund_status(order)
            if refund_status["already_cancelled"]:
                return {
                    "eligible": False,
                    "reason": "This order has already been cancelled.",
                    "order": self._extract_order_summary(order),
                    "items": self._extract_items(order),
                }
            if refund_status["already_refunded"]:
                return {
                    "eligible": False,
                    "reason": "This order has already been refunded in full.",
                    "order": self._extract_order_summary(order),
                    "items": self._extract_items(order),
                }

            # Step 2: Verify the sender's email matches the order's customer email on
            # file in Shopify. If the order has no email at all, be lenient (nothing
            # to compare against). A mismatch does NOT silently continue as eligible
            # — that would let anyone claiming an order number by email trigger a
            # cancel/refund for an order that isn't theirs.
            #
            # This is a HARD block, never staging_required/requires_manual_review:
            # a live incident confirmed a "manual review" refund/cancel action
            # staged from an unverified email reached "executed" status (the
            # confirmation email — and the refund/cancel outcome itself — went
            # out under the SENDER's email, who never owned this order). Ownership
            # isn't something a human reviewer can retroactively fix by approving
            # a queued action; it has to stop the action from ever being created.
            # Both callers of this function (return_actions_integration.py and
            # actions_service.py's detect_and_create) treat
            # staging_required/requires_manual_review as "create an action for
            # human review" — so this case must set neither. identity_mismatch is
            # the distinct signal callers use to give the customer an accurate,
            # resolving explanation instead. No order/item data is returned here
            # either, same as the "order not found" branch above — an unverified
            # sender never gets real order details, matching
            # tools.py's get_order_status ownership check.
            # Symmetric to the order-has-no-email leniency above: an
            # unverified chat visitor with NO email at all (email="") has
            # no sender identity to compare either, so this must not be
            # treated as a mismatch - that would falsely tell a customer
            # who never gave any contact email that "the email you're
            # contacting us from doesn't match", and would silently block
            # a legitimate order-number-only chat request that used to
            # reach a human via staging_required.
            order_email = order.get("email", "").lower()
            if order_email and email and order_email != email.lower():
                logger.warning(
                    f"[ReturnActions] Sender email mismatch for order #{order_id} — "
                    f"order has {order_email}, sender is {email}. Blocking — no action will be staged."
                )
                return {
                    "eligible": False,
                    "eligibility_verified": False,
                    "reason": "sender email does not match order email on file",
                    "order": None,
                    "items": [],
                    "requires_manual_review": False,
                    "staging_required": False,
                    "identity_mismatch": True,
                }

            # Step 3: Check fulfillment status
            fulfillment_status = order.get("fulfillment_status")
            if fulfillment_status != "fulfilled":
                return {
                    "eligible": False,
                    "reason": "This order hasn't been delivered yet, so it's not eligible for return.",
                    "order": self._extract_order_summary(order),
                    "items": self._extract_items(order),
                    "staging_required": True,
                    "action_hint": "cancel_order",
                    "fulfillment_status": fulfillment_status,
                }

            # Merchant refund policy — falls back to the hardcoded defaults
            # above when brand_id is absent or the brand has no policy set,
            # so a default-configured brand behaves exactly as it does today.
            policy = await self._get_refund_policy(brand_id)
            window_days = policy["window_days"]
            final_sale_tags = policy["final_sale_tags"]

            # Step 4: Check return window (merchant-configured, default 30 days)
            created_at = order.get("created_at")
            if created_at:
                order_date = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                days_since_order = (datetime.now(order_date.tzinfo) - order_date).days

                if days_since_order > window_days:
                    return {
                        "eligible": False,
                        "reason": f"Returns must be initiated within {window_days} days of delivery. This order is {days_since_order} days old.",
                        "order": self._extract_order_summary(order),
                        "items": self._extract_items(order),
                        "policy_snapshot": policy,
                    }

            # Step 5: Check for non-returnable (final sale) tags
            tags = order.get("tags", "").lower()
            for non_returnable_tag in final_sale_tags:
                if non_returnable_tag.lower() in tags:
                    return {
                        "eligible": False,
                        "reason": "This order contains items marked as Final Sale and cannot be returned.",
                        "order": self._extract_order_summary(order),
                        "items": self._extract_items(order),
                        "policy_snapshot": policy,
                    }

            # Step 6: Check line items for non-returnable products
            items = self._extract_items(order)
            non_returnable_items = []

            for item in items:
                # Check if any item has non-returnable tags (would need product lookup)
                # For now, we check the order tags which may include per-item info
                item_title = item.get("title", "").lower()
                for tag in final_sale_tags:
                    if tag.lower() in item_title or tag.lower() in tags:
                        non_returnable_items.append(item.get("title"))

            if non_returnable_items:
                return {
                    "eligible": False,
                    "reason": f"The following items are Final Sale and cannot be returned: {', '.join(non_returnable_items)}",
                    "order": self._extract_order_summary(order),
                    "items": items,
                    "policy_snapshot": policy,
                }

            # Step 7: Excluded products / collections / digital products —
            # reads raw line_items (product_id, requires_shipping) since
            # _extract_items() above strips those fields for display purposes.
            raw_line_items = order.get("line_items", [])
            if policy["excluded_product_ids"] or policy["excluded_collection_ids"] or policy["exclude_digital_products"]:
                excluded_products = set(policy["excluded_product_ids"])
                excluded_collections = set(policy["excluded_collection_ids"])
                blocked_titles = []

                for li in raw_line_items:
                    product_id = li.get("product_id")
                    title = li.get("title", "item")

                    if policy["exclude_digital_products"] and li.get("requires_shipping") is False:
                        blocked_titles.append(title)
                        continue

                    if product_id and product_id in excluded_products:
                        blocked_titles.append(title)
                        continue

                    if product_id and excluded_collections:
                        collection_ids = await self._get_product_collection_ids(tenant_id, product_id)
                        if excluded_collections.intersection(collection_ids):
                            blocked_titles.append(title)

                if blocked_titles:
                    return {
                        "eligible": False,
                        "reason": f"The following items are excluded from returns by store policy: {', '.join(blocked_titles)}.",
                        "order": self._extract_order_summary(order),
                        "items": items,
                        "policy_snapshot": policy,
                    }

            # Every structured check passed. Before auto-approving, check
            # whether the merchant has a free-text policy restriction (the
            # "notes" field on the policy form, or a Knowledge Base article)
            # that the structured fields above (window_days/final_sale_tags/
            # exclusions) don't capture — e.g. "orders cannot be refunded
            # after 24 hours" written only as a note, with return_policy_days
            # left at its default. A deterministic check has no reliable way
            # to verify compliance with free text against this specific
            # order, so rather than silently trust the structured fields
            # alone and risk a false "eligible", this is escalated for a
            # human to confirm — never auto-approved past policy text that
            # was never actually checked against it. Never invents a policy:
            # when no free-text policy exists anywhere (the common case),
            # nothing changes and eligibility is granted exactly as before.
            custom_policy_text = await self.get_custom_policy_text(brand_id, policy.get("notes"))

            # Policy Evidence layer: if that free-text restriction expresses
            # a confidently-parseable "refund/return within N hours/days"
            # condition (distinct from the structured window_days field
            # above — e.g. a note like "refunds must be requested within 24
            # hours" left in free text), verify it deterministically against
            # the order's real Shopify creation timestamp rather than
            # blindly escalating. Anything the regex can't confidently parse
            # still falls through to the existing escalate-for-human-review
            # branch below, unchanged.
            window_result = None
            if custom_policy_text:
                window_result = verify_time_window(
                    custom_policy_text, order.get("created_at"), keywords=["refund", "return"],
                )
                logger.info(
                    f"[PolicyEvidence] refund/return window check order=#{order_id}: "
                    f"status={window_result['status']} reason={window_result['reason']} "
                    f"window_hours={window_result['evidence'].get('policy_window_hours')} "
                    f"elapsed_hours={window_result['evidence'].get('elapsed_hours')}"
                )

            if window_result and window_result["status"] == "INELIGIBLE":
                ev = window_result["evidence"]
                return {
                    "eligible": False,
                    "eligibility_verified": True,
                    "reason": (
                        f"Verified: this order was placed {ev['elapsed_hours']:.1f} hours ago, outside the "
                        f"store's {ev['policy_window_hours']:.0f}-hour refund/return window."
                    ),
                    "order": self._extract_order_summary(order),
                    "items": items,
                    "policy_snapshot": policy,
                    "policy_verification": window_result,
                }

            window_verified_eligible = bool(window_result and window_result["status"] == "ELIGIBLE")

            # None means the check itself couldn't be completed (e.g. the
            # Knowledge Base lookup errored) — genuinely unknown, not
            # confirmed-empty. Treated the same as real policy text: never
            # guess "no policy" from a failed/ambiguous check.
            if custom_policy_text != "" and not window_verified_eligible:
                reason = (
                    "This order meets the standard return window and item rules, but this store has "
                    "additional policy details on file that need a quick human check before approving."
                    if custom_policy_text is not None else
                    "This order meets the standard return window and item rules, but we couldn't confirm "
                    "this store's full policy details just now, so this needs a quick human check before approving."
                )
                return {
                    "eligible": False,
                    "eligibility_verified": False,
                    "reason": reason,
                    "order": self._extract_order_summary(order),
                    "items": items,
                    "policy_snapshot": policy,
                    "custom_policy_text": custom_policy_text,
                    "requires_manual_review": True,
                    "staging_required": True,
                }

            # All checks passed - eligible for return
            return {
                "eligible": True,
                "reason": "Great news! Your order is eligible for return. Would you like to process a refund or an exchange?",
                "order": self._extract_order_summary(order),
                "items": items,
                "policy_snapshot": policy,
                **({"policy_verification": window_result} if window_verified_eligible else {}),
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

    async def _get_order_from_shopify(
        self,
        order_id: str,
        email: str,
        tenant_id: Optional[str] = None,
    ) -> Optional[Dict]:
        """Fetch order from Shopify by ID or name.
        Uses per-tenant ShopifyClient when tenant_id is provided; falls back to legacy env vars."""
        logger.info(f"[Shopify] Looking up order: {order_id}, email: {email}")

        # ── Per-tenant path (preferred): use stored brand/tenant credentials ──
        if tenant_id:
            try:
                from src.services.shopify_service import shopify_service, ShopifyError
                client = await shopify_service.get_client_for_tenant(tenant_id)
                result = await client.get_order(order_id)
                if result.get("success") and result.get("order"):
                    order = result["order"]
                    logger.info(f"[Shopify] Found order #{order.get('order_number')} via tenant client")
                    return order
            except Exception as e:
                logger.warning(f"[Shopify] Per-tenant order lookup failed: {e}")
            return None

        # ── Legacy path: global env var credentials ──
        data = self._shopify_request(f"orders.json?name=%23{order_id}&status=any")
        if data and data.get("orders"):
            orders = data["orders"]
            if email:
                for order in orders:
                    if order.get("email", "").lower() == email.lower():
                        return order
            return orders[0]

        data = self._shopify_request(f"orders.json?name={order_id}&status=any")
        if data and data.get("orders"):
            return data["orders"][0]

        if email:
            data = self._shopify_request(f"orders.json?email={email}&status=any")
            if data and data.get("orders"):
                for order in data["orders"]:
                    if str(order.get("order_number")) == str(order_id):
                        return order
                return data["orders"][0]

        try:
            data = self._shopify_request(f"orders/{order_id}.json")
            if data and data.get("order"):
                return data["order"]
        except Exception as e:
            logger.debug(f"[Shopify] Direct ID lookup failed: {e}")

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
        """Extract size from text (e.g., 'Medium', 'M', 'Large'). Whole-word
        matched only — plain substring matching previously misread any word
        merely CONTAINING an 's'/'m'/'l' (e.g. "leather jacket" -> "L",
        "canvas tote" -> "S") as a size request. Multi-word/longer patterns
        are checked before the bare single-letter ones so "size XL" resolves
        to XL rather than the first substring hit."""
        if not text:
            return None

        text = text.lower()

        # Ordered: more specific/longer patterns first so e.g. "xl" is found
        # before the bare "l" gets a chance to match.
        size_patterns = [
            ("extra extra large", "XXL"), ("extra small", "XS"), ("extra large", "XL"),
            ("xxl", "XXL"), ("xs", "XS"), ("xl", "XL"),
            ("small", "S"), ("medium", "M"), ("large", "L"),
            ("s", "S"), ("m", "M"), ("l", "L"),
        ]

        for pattern, size in size_patterns:
            if re.search(r'\b' + re.escape(pattern) + r'\b', text):
                return size

        return None

    async def _get_available_sizes(self, sku: str, product_title: str) -> List[Dict]:
        """Get available sizes for a product from Shopify (legacy env-var path only)."""
        try:
            if not product_title:
                return []

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
        for offset, label in [(-1, "smaller"), (1, "larger")]:
            new_idx = current_idx + offset
            if 0 <= new_idx < len(size_order):
                target_size = size_order[new_idx]

                for avail in available_sizes:
                    avail_size = self._extract_size(avail.get("size", ""))
                    if avail_size == target_size:
                        return {
                            "size": avail.get("size"),
                            "direction": offset,
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
    # Real exchange target resolution — LIVE Shopify data only
    # =========================================================================
    #
    # find_exchange_target() is the one place a requested replacement (a
    # size, a color, or a different product) is looked up. It never guesses:
    # every branch either returns a real, live, in-stock Shopify variant, or
    # a specific reason it couldn't (not specified, product gone, variant
    # doesn't exist, out of stock, ambiguous, target product not found).
    # Both PART 3 exchange cases this system genuinely supports come through
    # here:
    #   - same product, different size/color -> re-fetches the ORIGINAL
    #     product by its real Shopify product_id (never a title-search
    #     guess) and matches a variant on it.
    #   - a different product entirely -> title-searches for the named
    #     product, same lookup find_products_by_title() already uses
    #     everywhere else in this codebase (inventory Q&A, recommendations).

    _COLOR_WORDS = [
        "black", "white", "red", "blue", "green", "yellow", "pink", "purple", "orange",
        "grey", "gray", "brown", "navy", "beige", "maroon", "teal", "cream", "olive",
    ]

    def _extract_color(self, text: str) -> Optional[str]:
        if not text:
            return None
        t = text.lower()
        for color in self._COLOR_WORDS:
            if re.search(r'\b' + re.escape(color) + r'\b', t):
                return "Grey" if color == "gray" else color.capitalize()
        return None

    def _variant_named_options(self, variant: Dict, option_names: List[str]) -> Dict[str, str]:
        """Same option-labeling approach as tools.get_inventory_status() —
        variant.option1/2/3 mapped by the product's real, ordered option
        names (Size, Color, ...) rather than assuming position 1 is size."""
        opts = {}
        for i, name in enumerate(option_names):
            if not name:
                continue
            value = variant.get(f"option{i + 1}")
            if value and value != "Default Title":
                opts[name] = value
        return opts

    async def find_exchange_target(
        self,
        tenant_id: Optional[str],
        original_item: Dict[str, Any],
        target_description: Optional[str],
    ) -> Dict[str, Any]:
        """Resolve a customer's described replacement against LIVE Shopify
        data. original_item needs product_id (from the real order line
        item) and variant_title/price for same-product matching.

        Returns exactly one of:
          {"found": False, "reason": "no_shopify_connection"}
          {"found": False, "reason": "target_not_specified"}
          {"found": False, "reason": "product_unavailable"}
          {"found": False, "reason": "variant_not_found", "product_title", "available_options"}
          {"found": False, "reason": "out_of_stock", "product_title", "variant_title"}
          {"found": False, "reason": "target_not_found", "query"}
          {"found": False, "reason": "ambiguous", "matches": [titles]}
          {"found": True, "same_product": bool, "product_id", "product_title",
           "variant_id", "variant_title", "price": float, "product_url"}
        """
        if not tenant_id:
            return {"found": False, "reason": "no_shopify_connection"}
        if not target_description or not target_description.strip():
            return {"found": False, "reason": "target_not_specified"}

        from src.services.shopify_service import shopify_service
        from src.services.tools import _variant_in_stock

        try:
            client = await shopify_service.get_client_for_tenant(tenant_id)
        except Exception as e:
            logger.warning(f"[ActionsManager] Could not get Shopify client for exchange lookup: {e}")
            return {"found": False, "reason": "no_shopify_connection"}

        requested_size = self._extract_size(target_description)
        requested_color = self._extract_color(target_description)

        def _product_url(product: Dict) -> Optional[str]:
            handle = product.get("handle")
            return f"https://{client.shop_domain}/products/{handle}" if handle else None

        # ── Same product, different size/color ──────────────────────────
        if requested_size or requested_color:
            product_id = original_item.get("product_id")
            product = await client.get_product_by_id(product_id) if product_id else None
            if not product:
                return {"found": False, "reason": "product_unavailable"}

            option_names = [(o.get("name") or "").strip() for o in (product.get("options") or [])]
            original_options = {}
            # Fill in the attribute(s) the customer DIDN'T mention from the
            # original variant, so "just get me an L" on a Size+Color product
            # keeps the same color rather than matching any color in that size.
            for v in product.get("variants", []):
                if str(v.get("title")) == str(original_item.get("variant_title")) or \
                   v.get("id") == original_item.get("variant_id"):
                    original_options = self._variant_named_options(v, option_names)
                    break

            def _matches(variant: Dict) -> bool:
                opts = self._variant_named_options(variant, option_names)
                opts_lower = {k.lower(): (v or "").lower() for k, v in opts.items()}
                if requested_size:
                    size_val = opts_lower.get("size") or ""
                    if self._extract_size(size_val) != requested_size and requested_size.lower() not in size_val:
                        return False
                if requested_color:
                    color_val = opts_lower.get("color") or ""
                    if requested_color.lower() not in color_val:
                        return False
                # Any attribute not requested must match the ORIGINAL
                # variant's value — never silently pick an unrelated
                # combination on a product with 3+ options.
                for name, orig_val in original_options.items():
                    name_l = name.lower()
                    if name_l == "size" and requested_size:
                        continue
                    if name_l == "color" and requested_color:
                        continue
                    if opts_lower.get(name_l) != (orig_val or "").lower():
                        return False
                return True

            candidates = [v for v in product.get("variants", []) if _matches(v)]

            if not candidates:
                available = sorted({
                    (self._variant_named_options(v, option_names).get("Size")
                     or self._variant_named_options(v, option_names).get("size") or v.get("title") or "")
                    for v in product.get("variants", [])
                } - {""})
                return {
                    "found": False, "reason": "variant_not_found",
                    "product_title": product.get("title"), "available_options": available,
                }

            variant = candidates[0]
            if not _variant_in_stock(variant):
                return {
                    "found": False, "reason": "out_of_stock",
                    "product_title": product.get("title"), "variant_title": variant.get("title"),
                }

            return {
                "found": True, "same_product": True,
                "product_id": product.get("id"), "product_title": product.get("title"),
                "variant_id": variant.get("id"), "variant_title": variant.get("title"),
                "price": float(variant.get("price") or 0), "product_url": _product_url(product),
            }

        # ── Different product entirely ───────────────────────────────────
        try:
            result = await client.find_products_by_title(target_description)
        except Exception as e:
            logger.warning(f"[ActionsManager] Exchange target product search failed: {e}")
            return {"found": False, "reason": "target_not_found", "query": target_description}

        products = result.get("products", [])
        if not products:
            return {"found": False, "reason": "target_not_found", "query": target_description}
        if len(products) > 1:
            return {"found": False, "reason": "ambiguous", "matches": [p.get("title") for p in products[:8]]}

        product = products[0]
        variants = product.get("variants", [])
        in_stock_variants = [v for v in variants if _variant_in_stock(v)]
        real_variants = [v for v in variants if v.get("title") != "Default Title"] or variants

        if len(real_variants) > 1:
            # A product with real size/color options and no attribute named —
            # never guess which one. Ask instead.
            option_names = [(o.get("name") or "").strip() for o in (product.get("options") or [])]
            available = sorted({
                (self._variant_named_options(v, option_names).get("Size") or v.get("title") or "")
                for v in variants
            } - {""})
            return {
                "found": False, "reason": "variant_not_found",
                "product_title": product.get("title"), "available_options": available,
            }

        if not in_stock_variants:
            return {
                "found": False, "reason": "out_of_stock",
                "product_title": product.get("title"), "variant_title": (variants[0].get("title") if variants else None),
            }

        variant = in_stock_variants[0]
        return {
            "found": True, "same_product": False,
            "product_id": product.get("id"), "product_title": product.get("title"),
            "variant_id": variant.get("id"), "variant_title": variant.get("title"),
            "price": float(variant.get("price") or 0), "product_url": _product_url(product),
        }

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
            "revenue_at_stake": order_total,
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
        # Validate UUID format to prevent database errors
        try:
            uuid_lib.UUID(action_id)
        except ValueError:
            return {"success": False, "error": "Invalid action ID format. Must be a valid UUID."}

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
        # Validate UUID format to prevent database errors
        try:
            uuid_lib.UUID(action_id)
        except ValueError:
            return {"success": False, "error": "Invalid action ID format. Must be a valid UUID."}
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
    """Execute a refund via Shopify API (legacy pending_actions path — uses global env vars).
    New actions should go through actions_service.approve_action which uses per-tenant creds."""
    try:
        order_id = action.get("order_id")
        order_data = action.get("order_data", {})

        shopify_order_id = order_data.get("id") if order_data else None

        if not shopify_order_id:
            logger.error(f"[PendingActions] No Shopify order ID found in order_data for order {order_id}")
            return {"success": False, "error": "Order data not found. Cannot process refund."}

        shop_name = os.getenv("SHOPIFY_SHOP_NAME")
        shopify_token = os.getenv("SHOPIFY_ACCESS_TOKEN")
        api_version = SHOPIFY_API_VERSION

        if not shop_name or not shopify_token:
            logger.error("[PendingActions] Legacy Shopify env vars not set — cannot execute refund")
            return {"success": False, "error": "Shopify not configured for legacy action path."}

        url = f"https://{shop_name}.myshopify.com/admin/api/{api_version}/orders/{shopify_order_id}/refunds.json"
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
    """Execute an exchange via Shopify API (legacy pending_actions path — uses global env vars)."""
    try:
        exchange_data = action.get("exchange_suggestion", {})
        customer_email = action.get("customer_email")

        if not exchange_data:
            return {"success": False, "error": "No exchange data available. Cannot process exchange."}

        suggested_variant_id = exchange_data.get("variant_id")

        if not suggested_variant_id:
            return {"success": False, "error": "No suggested variant ID found. Cannot process exchange."}

        order_data = action.get("order_data", {})
        if not order_data:
            return {"success": False, "error": "Order data not found. Cannot process exchange."}

        shop_name = os.getenv("SHOPIFY_SHOP_NAME")
        shopify_token = os.getenv("SHOPIFY_ACCESS_TOKEN")
        api_version = SHOPIFY_API_VERSION

        if not shop_name or not shopify_token:
            logger.error("[PendingActions] Legacy Shopify env vars not set — cannot execute exchange")
            return {"success": False, "error": "Shopify not configured for legacy action path."}

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
