"""
Shopify Integration Service
===========================
Robust Shopify API integration with proper error handling.
Handles: connection validation, refunds, cancellations, address updates.
"""
import os
import re
import time
import logging
import requests
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from enum import Enum
import hashlib
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.lib.supabase_client import supabase_select, supabase_update
from src.config import SHOPIFY_API_VERSION

logger = logging.getLogger(__name__)


class ShopifyError(Exception):
    """Custom exception for Shopify API errors."""
    def __init__(self, message: str, error_code: str = None, status_code: int = None):
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        super().__init__(self.message)


class ShopifyErrorCode(str, Enum):
    """Standard error codes for Shopify API failures."""
    INVALID_TOKEN = "invalid_token"
    INVALID_DOMAIN = "invalid_domain"
    ORDER_NOT_FOUND = "order_not_found"
    ORDER_ALREADY_CANCELLED = "order_already_cancelled"
    ORDER_ALREADY_FULFILLED = "order_already_fulfilled"
    ORDER_ALREADY_REFUNDED = "order_already_refunded"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    RATE_LIMITED = "rate_limited"
    NETWORK_ERROR = "network_error"
    UNKNOWN_ERROR = "unknown_error"
    INVALID_REQUEST = "invalid_request"
    MISSING_SCOPE = "missing_scope"


def _get_encryption_secret() -> str:
    """The one place a credential-encryption key is ever read from — an env
    var, never the database. Warns loudly (not silently) if operators never
    set a real one, since the fallback is a literal string visible in this
    file and provides no real protection."""
    secret = os.getenv("ENCRYPTION_SECRET") or os.getenv("SECRET_KEY")
    if not secret:
        logger.error(
            "ENCRYPTION_SECRET (or SECRET_KEY) is not set — Shopify access tokens are being "
            "encrypted with a well-known default key, which provides no real protection. "
            "Set ENCRYPTION_SECRET in the deployment environment."
        )
        secret = "default-dev-key-change-in-prod"
    return secret


def _get_aes256_key() -> bytes:
    """Derive a 256-bit AES key from the encryption secret (SHA-256 of the
    secret is exactly 32 bytes — the key size AES-256 requires)."""
    return hashlib.sha256(_get_encryption_secret().encode()).digest()


def _get_fernet_key() -> bytes:
    """Legacy key derivation, kept only so decrypt_token() can still read
    tokens written before the AES-256-GCM migration (Fernet is AES-128-CBC
    + HMAC-SHA256, not AES-256 — this is why it was replaced)."""
    return base64.urlsafe_b64encode(hashlib.sha256(_get_encryption_secret().encode()).digest())


_AES256_PREFIX = "aes256gcm:"  # tags new-format ciphertext so decrypt can tell it apart from legacy Fernet


def encrypt_token(value: str) -> str:
    """Encrypt sensitive data (e.g. a Shopify access token) with AES-256-GCM
    before it is ever written to the database. Called at write time only —
    the database never sees plaintext."""
    if not value:
        return ""
    key = _get_aes256_key()
    nonce = os.urandom(12)  # 96-bit nonce, standard for GCM; unique per encryption
    ciphertext = AESGCM(key).encrypt(nonce, value.encode(), None)
    return _AES256_PREFIX + base64.urlsafe_b64encode(nonce + ciphertext).decode()


def decrypt_token(value: str) -> str:
    """Decrypt a token stored via encrypt_token(). Only ever called at the
    moment a value is actually needed (e.g. immediately before a Shopify API
    call) — never held decrypted longer than that.

    Handles three formats that can legitimately exist in this database:
      1. AES-256-GCM (the _AES256_PREFIX tag) — everything encrypt_token()
         produces from now on.
      2. Legacy Fernet — tokens encrypted before this migration; decryptable
         so existing connected brands don't break.
      3. Legacy plaintext — rows saved before encryption existed at all.
    """
    if not value:
        return ""
    if value.startswith(_AES256_PREFIX):
        try:
            raw = base64.urlsafe_b64decode(value[len(_AES256_PREFIX):].encode())
            nonce, ciphertext = raw[:12], raw[12:]
            return AESGCM(_get_aes256_key()).decrypt(nonce, ciphertext, None).decode()
        except Exception as e:
            logger.error(f"AES-256-GCM token decryption failed (check ENCRYPTION_SECRET matches what encrypted it): {e}")
            return value
    try:
        f = Fernet(_get_fernet_key())
        return f.decrypt(value.encode()).decode()
    except Exception:
        # Not AES-256-GCM, not Fernet — legacy plaintext row from before encryption existed.
        return value


class ShopifyClient:
    """
    Shopify API client with robust error handling and retry logic.
    """

    def __init__(
        self,
        shop_domain: str,
        access_token: str,
        api_version: str = SHOPIFY_API_VERSION
    ):
        # Normalize domain
        self.shop_domain = self._normalize_domain(shop_domain)
        self.access_token = access_token
        self.api_version = api_version
        self.base_url = f"https://{self.shop_domain}/admin/api/{api_version}"
        self.headers = {
            "X-Shopify-Access-Token": access_token,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        self._rate_limit_remaining = 40
        self._rate_limit_reset = 0

    def _normalize_domain(self, domain: str) -> str:
        """Normalize shop domain to 'shop.myshopify.com' format."""
        domain = domain.strip().lower()
        # Remove protocol
        domain = re.sub(r'^https?://', '', domain)
        # Remove trailing slash
        domain = domain.rstrip('/')
        # Add .myshopify.com if not present
        if not domain.endswith('.myshopify.com'):
            domain = f"{domain}.myshopify.com"
        return domain

    def _handle_response(self, resp: requests.Response, context: str = "") -> Dict[str, Any]:
        """
        Process Shopify API response and handle errors gracefully.
        """
        # Track rate limits
        self._rate_limit_remaining = int(resp.headers.get('X-Shopify-Shop-Api-Call-Limit', '40/40').split('/')[0])

        if resp.status_code == 200 or resp.status_code == 201:
            return {"success": True, "data": resp.json()}

        # Handle specific error codes
        error_data = {}
        try:
            error_data = resp.json()
        except Exception:
            pass

        error_message = self._extract_error_message(error_data, resp.text)

        if resp.status_code == 401:
            raise ShopifyError(
                f"Invalid Shopify access token. Please reconnect your store.",
                ShopifyErrorCode.INVALID_TOKEN,
                401
            )

        if resp.status_code == 404:
            raise ShopifyError(
                f"Resource not found: {context}",
                ShopifyErrorCode.ORDER_NOT_FOUND,
                404
            )

        if resp.status_code == 403:
            # Shopify returns 403 when the token's granted scopes don't cover
            # this endpoint - distinct from 401 (token itself invalid/
            # revoked). Previously fell through to the generic
            # UNKNOWN_ERROR branch below, whose message ("Shopify API
            # error: {error_message}") echoes Shopify's raw response text
            # (e.g. "This action requires merchant approval for write_orders
            # scope") straight through - fine for a merchant in a dashboard,
            # not something to relay to a customer verbatim. This message is
            # tResolv's own wording and always actionable the same way
            # regardless of which specific scope Shopify named.
            raise ShopifyError(
                "This Shopify connection is missing a permission this action needs. "
                "Reconnect Shopify to grant the required access.",
                ShopifyErrorCode.MISSING_SCOPE,
                403
            )

        if resp.status_code == 422:
            # Unprocessable entity - check for specific errors
            if "already been refunded" in error_message.lower():
                raise ShopifyError(
                    "This order has already been refunded.",
                    ShopifyErrorCode.ORDER_ALREADY_REFUNDED,
                    422
                )
            if "already been cancelled" in error_message.lower() or "already canceled" in error_message.lower():
                raise ShopifyError(
                    "This order has already been cancelled.",
                    ShopifyErrorCode.ORDER_ALREADY_CANCELLED,
                    422
                )
            if "fulfilled" in error_message.lower():
                raise ShopifyError(
                    "Cannot modify a fulfilled order. Please process a refund instead.",
                    ShopifyErrorCode.ORDER_ALREADY_FULFILLED,
                    422
                )
            raise ShopifyError(
                f"Invalid request: {error_message}",
                ShopifyErrorCode.INVALID_REQUEST,
                422
            )

        if resp.status_code == 429:
            raise ShopifyError(
                "Rate limited by Shopify. Please try again in a few seconds.",
                ShopifyErrorCode.RATE_LIMITED,
                429
            )

        if resp.status_code >= 500:
            raise ShopifyError(
                "Shopify is experiencing issues. Please try again later.",
                ShopifyErrorCode.NETWORK_ERROR,
                resp.status_code
            )

        # Generic error
        raise ShopifyError(
            f"Shopify API error: {error_message}",
            ShopifyErrorCode.UNKNOWN_ERROR,
            resp.status_code
        )

    def _extract_error_message(self, error_data: dict, fallback: str) -> str:
        """Extract human-readable error message from Shopify response."""
        if isinstance(error_data, dict):
            # Check common error structures
            if "errors" in error_data:
                errors = error_data["errors"]
                if isinstance(errors, str):
                    return errors
                if isinstance(errors, dict):
                    messages = []
                    for field, field_errors in errors.items():
                        if isinstance(field_errors, list):
                            messages.extend([f"{field}: {e}" for e in field_errors])
                        else:
                            messages.append(f"{field}: {field_errors}")
                    return "; ".join(messages)
                if isinstance(errors, list):
                    return "; ".join(str(e) for e in errors)
            if "error" in error_data:
                return str(error_data["error"])
        return fallback[:500] if fallback else "Unknown error"

    def _request(
        self,
        method: str,
        endpoint: str,
        data: dict = None,
        params: dict = None,
        retry_count: int = 0
    ) -> Dict[str, Any]:
        """Make authenticated request to Shopify with retry logic."""
        url = f"{self.base_url}/{endpoint}"
        max_retries = 3

        logger.info(f"[Shopify] {method} {endpoint}")

        try:
            if method == "GET":
                resp = requests.get(url, headers=self.headers, params=params, timeout=30)
            elif method == "POST":
                resp = requests.post(url, headers=self.headers, json=data, timeout=30)
            elif method == "PUT":
                resp = requests.put(url, headers=self.headers, json=data, timeout=30)
            elif method == "DELETE":
                resp = requests.delete(url, headers=self.headers, timeout=30)
            else:
                raise ShopifyError(f"Unsupported HTTP method: {method}")

            return self._handle_response(resp, endpoint)

        except ShopifyError as e:
            # Retry on rate limit
            if e.error_code == ShopifyErrorCode.RATE_LIMITED and retry_count < max_retries:
                wait_time = 2 ** retry_count  # Exponential backoff
                logger.warning(f"[Shopify] Rate limited, retrying in {wait_time}s...")
                time.sleep(wait_time)
                return self._request(method, endpoint, data, params, retry_count + 1)
            raise

        except requests.exceptions.Timeout:
            raise ShopifyError(
                "Request timed out. Please try again.",
                ShopifyErrorCode.NETWORK_ERROR
            )

        except requests.exceptions.ConnectionError:
            raise ShopifyError(
                "Could not connect to Shopify. Please check your internet connection.",
                ShopifyErrorCode.NETWORK_ERROR
            )

        except Exception as e:
            logger.error(f"[Shopify] Request error: {e}")
            raise ShopifyError(
                f"Unexpected error: {str(e)}",
                ShopifyErrorCode.UNKNOWN_ERROR
            )

    async def validate_connection(self) -> Dict[str, Any]:
        """
        Validate Shopify credentials by fetching shop info.

        Returns:
            Dict with success status and shop details
        """
        try:
            result = self._request("GET", "shop.json")
            shop = result.get("data", {}).get("shop", {})

            return {
                "success": True,
                "shop_name": shop.get("name"),
                "shop_domain": shop.get("domain"),
                "myshopify_domain": shop.get("myshopify_domain"),
                "plan": shop.get("plan_name"),
                "currency": shop.get("currency"),
                "country": shop.get("country_name"),
                "email": shop.get("email")
            }

        except ShopifyError as e:
            return {
                "success": False,
                "error": e.message,
                "error_code": e.error_code
            }

    async def get_counts(self) -> Dict[str, Any]:
        """Best-effort product/order counts for the post-connect summary
        screen — either can legitimately fail (e.g. missing read_orders
        scope) without the connection itself being a failure, so each is
        caught independently and reported as None rather than raising."""
        counts: Dict[str, Optional[int]] = {"products": None, "orders": None}
        try:
            result = self._request("GET", "products/count.json")
            counts["products"] = result.get("data", {}).get("count")
        except Exception as e:
            logger.warning(f"[Shopify] Could not fetch product count: {e}")
        try:
            result = self._request("GET", "orders/count.json", params={"status": "any"})
            counts["orders"] = result.get("data", {}).get("count")
        except Exception as e:
            logger.warning(f"[Shopify] Could not fetch order count: {e}")
        return counts

    @staticmethod
    def check_refund_status(order: Dict[str, Any]) -> Dict[str, Any]:
        """Already-cancelled / already-refunded check on an already-fetched order.
        Shared by process_refund() (execution time) and the staging-time
        eligibility check in actions_manager.py, so a human is never shown an
        action that's guaranteed to fail on approval, and the two checks can
        never drift apart since they're the same code."""
        if order.get("cancelled_at"):
            return {"already_cancelled": True, "already_refunded": False, "refundable_amount": 0.0}
        total_price = float(order.get("total_price", 0))
        refunded = sum(
            float(r.get("transactions", [{}])[0].get("amount", 0))
            for r in order.get("refunds", [])
        )
        refundable_amount = total_price - refunded
        return {
            "already_cancelled": False,
            "already_refunded": refundable_amount <= 0,
            "refundable_amount": refundable_amount,
        }

    async def get_order(self, order_identifier: str) -> Dict[str, Any]:
        """
        Get order by ID or order number.

        Args:
            order_identifier: Can be order ID (numeric) or order number (#1001)
        """
        # Clean up the identifier
        order_identifier = str(order_identifier).strip().lstrip('#')

        try:
            # First try by order name/number
            result = self._request(
                "GET",
                "orders.json",
                params={"name": order_identifier, "status": "any"}
            )
            orders = result.get("data", {}).get("orders", [])
            if orders:
                return {"success": True, "order": orders[0]}

            # Try with # prefix
            result = self._request(
                "GET",
                "orders.json",
                params={"name": f"#{order_identifier}", "status": "any"}
            )
            orders = result.get("data", {}).get("orders", [])
            if orders:
                return {"success": True, "order": orders[0]}

            # Try direct ID lookup only for real Shopify internal IDs (10+ digits).
            # Order numbers like 1002 are NOT internal IDs — never fetch orders/1002.json.
            if order_identifier.isdigit() and len(order_identifier) >= 10:
                result = self._request("GET", f"orders/{order_identifier}.json")
                order = result.get("data", {}).get("order")
                if order:
                    return {"success": True, "order": order}

            # Final fallback: scan recent orders by integer order_number field.
            # Works even when the store uses custom name prefixes (e.g. "HF-1002").
            if order_identifier.isdigit():
                result = self._request(
                    "GET",
                    "orders.json",
                    params={"status": "any", "limit": 250, "order": "created_at desc"}
                )
                all_orders = result.get("data", {}).get("orders", [])
                sample = [f"#{o.get('order_number')} ({o.get('name')})" for o in all_orders[:10]]
                logger.info(f"[Shopify] Order scan: {len(all_orders)} orders found. Sample: {sample}")
                for candidate in all_orders:
                    if str(candidate.get("order_number")) == order_identifier:
                        logger.info(f"[Shopify] Found order #{order_identifier} via order_number scan")
                        return {"success": True, "order": candidate}

            raise ShopifyError(
                f"Order '{order_identifier}' not found. Please check the order number.",
                ShopifyErrorCode.ORDER_NOT_FOUND
            )

        except ShopifyError:
            raise
        except Exception as e:
            logger.error(f"[Shopify] Error fetching order: {e}")
            raise ShopifyError(str(e), ShopifyErrorCode.UNKNOWN_ERROR)

    async def find_orders_by_email(self, email: str) -> Dict[str, Any]:
        """List a customer's real orders straight from Shopify. Returns every
        match rather than picking one, so callers can surface "which order?"
        instead of guessing when there's more than one."""
        try:
            result = self._request(
                "GET",
                "orders.json",
                params={"email": email, "status": "any", "limit": 50}
            )
            orders = result.get("data", {}).get("orders", [])
            return {"success": True, "orders": orders, "count": len(orders)}
        except ShopifyError:
            raise
        except Exception as e:
            logger.error(f"[Shopify] Error listing orders by email: {e}")
            raise ShopifyError(str(e), ShopifyErrorCode.UNKNOWN_ERROR)

    # Every field any product-facing caller (title search, recommendations)
    # currently needs, fetched once. Adding a field here makes it available
    # everywhere without a second Shopify round trip — do not add a parallel
    # fetch elsewhere for a field that could just be added to this list.
    _PRODUCT_FIELDS = "id,title,variants,status,handle,images,image,options,product_type,vendor,tags,body_html"

    async def list_active_products(self, limit: int = 250) -> List[Dict[str, Any]]:
        """Shared underlying fetch for every product-listing operation
        (title search, recommendations). One Shopify call, reused — never
        duplicate this fetch in a second method. Public because
        tools.get_product_recommendations() calls it directly to build its
        own deterministic candidate scoring on top, the same way
        get_inventory_status() already builds variant/stock logic on top of
        find_products_by_title()'s raw data."""
        result = self._request(
            "GET",
            "products.json",
            params={"limit": limit, "fields": self._PRODUCT_FIELDS}
        )
        products = result.get("data", {}).get("products", [])
        return [p for p in products if p.get("status") == "active"]

    async def get_product_by_id(self, product_id) -> Optional[Dict[str, Any]]:
        """Fetch one product directly by its Shopify product_id (as found on
        a real order's line_items) — precise, never a title-search guess.
        Used by exchange handling to re-fetch an order's original product's
        live variants (current stock/price) without the ambiguity risk of
        matching by title when multiple similarly-named products exist.
        Returns None (not an error) if the product no longer exists."""
        try:
            result = self._request(
                "GET",
                f"products/{product_id}.json",
                params={"fields": self._PRODUCT_FIELDS},
            )
            return result.get("data", {}).get("product")
        except ShopifyError as e:
            if e.error_code == ShopifyErrorCode.ORDER_NOT_FOUND:
                return None
            raise

    async def find_products_by_title(self, query: str, limit: int = 250) -> Dict[str, Any]:
        """Search real Shopify products by (case-insensitive, whole-word)
        title match. Shopify's REST title filter is exact-match only, so this
        scans a bounded recent page client-side, same fallback shape as
        get_order's order_number scan. Returns every match rather than the
        first one — an ambiguous product name (e.g. "hoodie" matching 4
        products) must be surfaced as a choice, never guessed.

        Includes handle/images/options — needed by get_inventory_status() to
        build a real storefront product URL and label variant options (size/
        color/etc) correctly instead of assuming option1 is always size.
        Nothing here is fabricated: any field Shopify doesn't return for a
        product is simply absent, never invented downstream."""
        try:
            products = await self.list_active_products(limit)
            needle = query.strip().lower()
            # Word-boundary match, not a bare substring: a bare `needle in
            # title` check treats "essential hoodie v1" as present inside
            # "essential hoodie v10" (v1 is a literal prefix of v10),
            # producing false ambiguous matches for precisely-named products
            # (confirmed live against a real store). \b still matches at
            # whitespace/punctuation, so partial/prefix title searches like
            # "hoodie" or "essential hoodie" keep matching every product that
            # contains them as whole words, exactly as before — it only
            # refuses to match mid-token (digit-digit, letter-letter).
            pattern = re.compile(r'\b' + re.escape(needle) + r'\b', re.IGNORECASE) if needle else None
            matches = [p for p in products if pattern and pattern.search(p.get("title") or "")]
            return {"success": True, "products": matches, "count": len(matches)}
        except ShopifyError:
            raise
        except Exception as e:
            logger.error(f"[Shopify] Error searching products by title: {e}")
            raise ShopifyError(str(e), ShopifyErrorCode.UNKNOWN_ERROR)

    async def process_refund(
        self,
        order_id: str,
        amount: float = None,
        reason: str = None,
        restock: bool = False,
        notify_customer: bool = True
    ) -> Dict[str, Any]:
        """
        Process a refund for an order.

        Args:
            order_id: Order ID or number
            amount: Refund amount (None = full refund)
            reason: Reason for refund
            restock: NOT CURRENTLY FUNCTIONAL — accepted but has no effect.
                The refund payload built below (see "no refund_line_items"
                comment) is a pure financial refund via `transactions`, never
                a line-item refund via `refund_line_items`, so there is
                nothing for this flag to attach to; restocking is a
                per-line-item, per-location Shopify concept
                (`refund_line_items[].restock_type` + a resolved
                `location_id`), and this integration doesn't resolve a
                location per order today. No caller currently passes
                restock=True. Building real restock support is a product
                decision (does the merchant want it on by default? which
                location?) as much as a code change — flagged, not guessed
                at here. cancel_order()'s `restock` parameter, by contrast,
                *is* wired through correctly (Shopify's cancel endpoint takes
                a simple top-level boolean, no location resolution needed).
            notify_customer: Send notification email
        """
        # Get the order first
        order_result = await self.get_order(order_id)
        order = order_result.get("order", {})
        shopify_order_id = order.get("id")

        # Check order status
        status = self.check_refund_status(order)
        if status["already_cancelled"]:
            raise ShopifyError(
                "Cannot refund a cancelled order.",
                ShopifyErrorCode.ORDER_ALREADY_CANCELLED
            )

        # Calculate refund amount
        if amount is None:
            # Full refund - get total minus existing refunds
            amount = status["refundable_amount"]

        if amount <= 0:
            raise ShopifyError(
                "Order has already been fully refunded.",
                ShopifyErrorCode.ORDER_ALREADY_REFUNDED
            )

        # A caller-provided (partial) amount must never exceed what's
        # actually still refundable on this order — Shopify's own API may or
        # may not reject an over-amount request depending on version/gateway,
        # so this is checked deterministically here rather than relying on
        # that. Never inferred, never guessed: refundable_amount comes
        # straight from this order's live total minus its existing refunds.
        if amount > status["refundable_amount"] + 0.01:  # small epsilon for float rounding
            raise ShopifyError(
                f"Requested refund amount (${amount:.2f}) exceeds the refundable amount "
                f"(${status['refundable_amount']:.2f}) for this order.",
                ShopifyErrorCode.INVALID_REQUEST
            )

        # Fetch the sale/capture transaction to use as parent_id
        parent_transaction_id = None
        try:
            txn_result = self._request("GET", f"orders/{shopify_order_id}/transactions.json")
            txns = txn_result.get("data", {}).get("transactions", [])
            for t in txns:
                if t.get("kind") in ("sale", "capture") and t.get("status") == "success":
                    parent_transaction_id = t.get("id")
                    break
            if not parent_transaction_id and txns:
                parent_transaction_id = txns[0].get("id")
        except Exception as txn_err:
            logger.warning(f"[Shopify] Could not fetch transactions for refund parent_id: {txn_err}")

        # Build refund payload — no refund_line_items to avoid location requirement
        refund_data = {
            "refund": {
                "note": reason or "Refund processed via AI Support System",
                "notify": notify_customer,
                "shipping": {"full_refund": True},
            }
        }
        if parent_transaction_id:
            refund_data["refund"]["transactions"] = [
                {
                    "parent_id": parent_transaction_id,
                    "kind": "refund",
                    "amount": str(round(amount, 2)),
                    "gateway": order.get("gateway", "manual")
                }
            ]

        result = self._request("POST", f"orders/{shopify_order_id}/refunds.json", refund_data)
        refund = result.get("data", {}).get("refund", {})

        # A 200/201 here only confirms Shopify accepted the refund REQUEST —
        # it does not confirm money actually moved. The nested transaction(s)
        # settle against the payment gateway separately and can come back
        # "failure"/"error" (e.g. an expired card) even though the refund
        # record itself was created successfully. Money-refund requests
        # (parent_transaction_id set above) must have at least one
        # transaction, and none of them may report a non-success status —
        # otherwise this must not be reported to the customer as a completed
        # refund, matching the product's core safety rule (never claim an
        # action succeeded without confirmed success).
        refund_transactions = refund.get("transactions", [])
        if parent_transaction_id:
            if not refund_transactions:
                raise ShopifyError(
                    "Shopify did not report a payment transaction for this refund — "
                    "it may not have actually been processed. Please verify manually in Shopify admin.",
                    ShopifyErrorCode.UNKNOWN_ERROR,
                )
            failed = [t for t in refund_transactions if t.get("status") not in (None, "success")]
            if failed:
                raise ShopifyError(
                    f"Shopify reported the refund transaction did not succeed "
                    f"(status: {failed[0].get('status')}). Please verify manually in Shopify admin.",
                    ShopifyErrorCode.UNKNOWN_ERROR,
                )

        return {
            "success": True,
            "refund_id": refund.get("id"),
            "amount": amount,
            "order_id": order_id,
            "order_name": order.get("name"),
            "message": f"Successfully refunded ${amount:.2f}"
        }

    async def cancel_order(
        self,
        order_id: str,
        reason: str = "customer",
        email_customer: bool = True,
        restock: bool = True
    ) -> Dict[str, Any]:
        """
        Cancel an order.

        Args:
            order_id: Order ID or number
            reason: Cancellation reason (customer, fraud, inventory, declined, other)
            email_customer: Send cancellation email
            restock: Restock inventory
        """
        # Get the order first
        order_result = await self.get_order(order_id)
        order = order_result.get("order", {})
        shopify_order_id = order.get("id")

        # Check if already cancelled
        if order.get("cancelled_at"):
            raise ShopifyError(
                "This order has already been cancelled.",
                ShopifyErrorCode.ORDER_ALREADY_CANCELLED
            )

        # Check if fulfilled
        if order.get("fulfillment_status") == "fulfilled":
            raise ShopifyError(
                "Cannot cancel a fulfilled order. Please process a refund instead.",
                ShopifyErrorCode.ORDER_ALREADY_FULFILLED
            )

        cancel_data = {
            "reason": reason,
            "email": email_customer,
            "restock": restock
        }

        result = self._request("POST", f"orders/{shopify_order_id}/cancel.json", cancel_data)
        cancelled_order = result.get("data", {}).get("order", {})

        # Defense in depth: a 200/201 here should always come with a
        # populated cancelled_at, but don't report success on the strength
        # of the HTTP status alone if Shopify's own response doesn't
        # corroborate it.
        if not cancelled_order.get("cancelled_at"):
            raise ShopifyError(
                "Shopify did not confirm the order as cancelled. Please verify manually in Shopify admin.",
                ShopifyErrorCode.UNKNOWN_ERROR,
            )

        return {
            "success": True,
            "order_id": order_id,
            "order_name": order.get("name"),
            "cancelled_at": cancelled_order.get("cancelled_at"),
            "message": f"Successfully cancelled order {order.get('name')}"
        }

    async def update_shipping_address(
        self,
        order_id: str,
        new_address: Dict[str, str],
        customer_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Update shipping address for an unfulfilled order.

        Args:
            order_id: Order ID or number
            new_address: Dict with address fields
            customer_name: Full customer name — split into first/last for Shopify
        """
        # Get the order first
        order_result = await self.get_order(order_id)
        order = order_result.get("order", {})
        shopify_order_id = order.get("id")

        # Check if fulfilled
        if order.get("fulfillment_status") == "fulfilled":
            raise ShopifyError(
                "Cannot change address for a fulfilled order.",
                ShopifyErrorCode.ORDER_ALREADY_FULFILLED
            )

        # Build address payload
        shipping_address = {
            "address1": new_address.get("address1", new_address.get("street")),
            "address2": new_address.get("address2", ""),
            "city": new_address.get("city"),
            "province": new_address.get("province", new_address.get("state")),
            "country": new_address.get("country", "US"),
            "zip": new_address.get("zip", new_address.get("postal_code")),
            "phone": new_address.get("phone", "")
        }

        # Include name fields — Shopify requires at least first_name or last_name
        name_to_use = customer_name or new_address.get("name", "")
        if name_to_use:
            parts = name_to_use.strip().split(None, 1)
            shipping_address["first_name"] = parts[0]
            shipping_address["last_name"] = parts[1] if len(parts) > 1 else ""
        else:
            # Fall back to existing order name so the field is never blank
            existing = order.get("shipping_address") or {}
            shipping_address["first_name"] = existing.get("first_name", "")
            shipping_address["last_name"] = existing.get("last_name", "")

        # Remove None/empty values
        shipping_address = {k: v for k, v in shipping_address.items() if v is not None}

        update_data = {
            "order": {
                "id": shopify_order_id,
                "shipping_address": shipping_address
            }
        }

        result = self._request("PUT", f"orders/{shopify_order_id}.json", update_data)
        updated_order = result.get("data", {}).get("order", {})
        returned_address = updated_order.get("shipping_address") or {}

        # Defense in depth (same pattern as cancel_order/process_refund):
        # a 200 here doesn't guarantee Shopify actually applied every field —
        # a partial validation failure can still return 200 with the old
        # address, or with some fields silently dropped. Compare the fields
        # we actually sent against what Shopify echoes back before reporting
        # success; only fields we explicitly requested a change for are
        # checked, since Shopify may reformat cosmetic fields (e.g. phone).
        mismatched = [
            field for field in ("address1", "city", "country", "zip")
            if field in shipping_address and shipping_address[field] != returned_address.get(field)
        ]
        if mismatched:
            raise ShopifyError(
                f"Shopify did not confirm the address update — {', '.join(mismatched)} "
                f"did not match the requested value. Please verify manually in Shopify admin.",
                ShopifyErrorCode.UNKNOWN_ERROR,
            )

        return {
            "success": True,
            "order_id": order_id,
            "order_name": order.get("name"),
            "new_address": returned_address,
            "message": f"Successfully updated shipping address for order {order.get('name')}"
        }


    async def reopen_order(self, order_id: str) -> Dict[str, Any]:
        """
        Reopen (restore) a cancelled order.
        Shopify API: POST /admin/api/{version}/orders/{id}/reopen.json
        """
        order_result = await self.get_order(order_id)
        order = order_result.get("order", {})
        shopify_order_id = order.get("id")

        if not order.get("cancelled_at"):
            raise ShopifyError(
                "This order is not cancelled — nothing to restore.",
                ShopifyErrorCode.INVALID_REQUEST
            )

        result = self._request("POST", f"orders/{shopify_order_id}/reopen.json", {})
        reopened = result.get("data", {}).get("order", {})

        return {
            "success": True,
            "order_id": order_id,
            "order_name": order.get("name"),
            "message": f"Order {order.get('name')} has been restored and is active again."
        }

    async def create_exchange_draft_order(
        self,
        customer_email: str,
        variant_id: int,
        quantity: int,
        price_difference: float,
        order_name: str,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create the replacement item for an exchange as a real Shopify draft
        order — the only exchange/line-item-swap primitive this REST Admin
        API integration has (there is no Returns-API or Order-Edit call
        implemented here; see return_actions_integration.py for why this is
        the honest automation boundary).

        price_difference is the caller's already-computed, live-data-derived
        difference between the new variant's price and the original item's
        price — never guessed here:
          - <= 0: nothing owed. A 100% line discount is applied and the
            draft order is completed immediately — Shopify requires no
            payment for a $0 order, so this leg needs no customer action at
            all and results in a real, confirmed replacement order.
          - > 0: the customer owes the difference. The draft order is left
            uncompleted with that balance, and Shopify's own invoice email
            is sent so the customer pays through Shopify's real checkout —
            this method never collects, waives, or guesses a payment amount
            itself.

        A negative difference (replacement cheaper — money potentially owed
        back to the customer) must never reach this method: whether to
        refund it, issue store credit, or absorb it is a real business
        decision with no configured store policy anywhere in this system.
        Callers must escalate that case for manual merchant handling instead
        of calling this.
        """
        if price_difference < 0:
            raise ShopifyError(
                "A cheaper-replacement exchange cannot be auto-completed — "
                "the price difference decision requires merchant review.",
                ShopifyErrorCode.INVALID_REQUEST,
            )

        draft_order_payload: Dict[str, Any] = {
            "draft_order": {
                "email": customer_email,
                "note": note or f"Exchange replacement for order {order_name}",
                "line_items": [{"variant_id": variant_id, "quantity": quantity}],
                "use_customer_default_address": True,
            }
        }
        no_balance_due = price_difference <= 0
        if no_balance_due:
            draft_order_payload["draft_order"]["applied_discount"] = {
                "description": "Exchange — no additional cost",
                "value_type": "percentage",
                "value": "100.0",
            }

        result = self._request("POST", "draft_orders.json", draft_order_payload)
        draft_order = result.get("data", {}).get("draft_order", {})
        draft_order_id = draft_order.get("id")

        if not draft_order_id:
            raise ShopifyError(
                "Shopify did not return a draft order id — the exchange replacement order "
                "may not have been created. Please verify manually in Shopify admin.",
                ShopifyErrorCode.UNKNOWN_ERROR,
            )

        if no_balance_due:
            # Nothing owed — complete immediately, no customer action needed.
            complete_result = self._request("POST", f"draft_orders/{draft_order_id}/complete.json", {})
            completed = complete_result.get("data", {}).get("draft_order", {})
            if completed.get("status") != "completed" or not completed.get("order_id"):
                raise ShopifyError(
                    "Shopify did not confirm the exchange replacement order as completed. "
                    "Please verify manually in Shopify admin.",
                    ShopifyErrorCode.UNKNOWN_ERROR,
                )
            return {
                "success": True,
                "completed": True,
                "invoice_sent": False,
                "draft_order_id": draft_order_id,
                "draft_order_name": draft_order.get("name"),
                "replacement_order_id": completed.get("order_id"),
                "balance_due": 0.0,
                "message": f"Exchange replacement order {draft_order.get('name')} created and confirmed — no additional payment needed.",
            }

        # Balance due — send Shopify's own invoice; the customer pays through
        # Shopify's real checkout. Never mark this completed/paid ourselves.
        invoice_sent = False
        try:
            self._request("POST", f"draft_orders/{draft_order_id}/send_invoice.json", {"draft_order_invoice": {}})
            invoice_sent = True
        except ShopifyError as e:
            logger.warning(f"[Shopify] Exchange draft order {draft_order_id} created but invoice send failed: {e.message}")

        return {
            "success": True,
            "completed": False,
            "invoice_sent": invoice_sent,
            "draft_order_id": draft_order_id,
            "draft_order_name": draft_order.get("name"),
            "invoice_url": draft_order.get("invoice_url"),
            "balance_due": price_difference,
            "message": (
                f"Exchange draft order {draft_order.get('name')} created for the ${price_difference:.2f} difference — "
                + ("invoice emailed to the customer to complete payment." if invoice_sent
                   else "invoice could not be sent automatically, send it manually from Shopify admin.")
            ),
        }


class ShopifyService:
    """
    Service for managing Shopify connections for tenants.
    """

    async def connect_store(
        self,
        tenant_id: str,
        shop_domain: str,
        access_token: str
    ) -> Dict[str, Any]:
        """
        Connect a Shopify store to a tenant account.

        Validates the credentials before saving.
        """
        try:
            # Create client to validate
            client = ShopifyClient(shop_domain, access_token)
            validation = await client.validate_connection()

            if not validation.get("success"):
                return {
                    "success": False,
                    "error": validation.get("error", "Could not connect to Shopify store"),
                    "error_code": validation.get("error_code")
                }

            # Encrypt the token before storing
            encrypted_token = encrypt_token(access_token)

            # Update tenant with Shopify info
            update_data = {
                "shopify_domain": client.shop_domain,
                "shopify_access_token": encrypted_token,
                "shopify_connected": True,
                "shopify_shop_name": validation.get("shop_name"),
                "shopify_plan": validation.get("plan"),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }

            supabase_update("tenants", {"id": f"eq.{tenant_id}"}, update_data)

            logger.info(f"[Shopify] Connected store {client.shop_domain} for tenant {tenant_id}")

            return {
                "success": True,
                "message": "Shopify store connected successfully",
                "shop_name": validation.get("shop_name"),
                "shop_domain": validation.get("shop_domain"),
                "plan": validation.get("plan")
            }

        except ShopifyError as e:
            return {
                "success": False,
                "error": e.message,
                "error_code": e.error_code
            }
        except Exception as e:
            logger.error(f"[Shopify] Connect error: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def disconnect_store(self, tenant_id: str) -> Dict[str, Any]:
        """Disconnect Shopify store from tenant."""
        try:
            supabase_update("tenants", {"id": f"eq.{tenant_id}"}, {
                "shopify_domain": None,
                "shopify_access_token": None,
                "shopify_connected": False,
                "shopify_shop_name": None,
                "shopify_plan": None,
                "updated_at": datetime.now(timezone.utc).isoformat()
            })

            # Also clear from brands table (connect mirrors creds there)
            try:
                brands = supabase_select("brands", {"tenant_id": f"eq.{tenant_id}"})
                for brand in brands:
                    supabase_update("brands", {"id": f"eq.{brand['id']}"}, {
                        "shopify_connected": False,
                        "shopify_access_token": None,
                        "shopify_domain": None,
                        "shopify_shop_name": None,
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    })
            except Exception as brand_err:
                logger.warning(f"[Shopify] Could not clear brands table during disconnect: {brand_err}")

            return {"success": True, "message": "Shopify store disconnected"}

        except Exception as e:
            logger.error(f"[Shopify] Disconnect error: {e}")
            return {"success": False, "error": str(e)}

    async def test_connection(self, tenant_id: str) -> Dict[str, Any]:
        """Test the Shopify connection for a tenant."""
        try:
            # Get tenant
            tenants = supabase_select("tenants", {"id": f"eq.{tenant_id}"})
            if not tenants:
                return {"success": False, "error": "Tenant not found"}

            tenant = tenants[0]

            if not tenant.get("shopify_connected"):
                return {"success": False, "error": "No Shopify store connected"}

            # Decrypt token
            access_token = decrypt_token(tenant.get("shopify_access_token", ""))

            if not access_token:
                return {"success": False, "error": "Missing access token"}

            client = ShopifyClient(tenant.get("shopify_domain"), access_token)
            return await client.validate_connection()

        except ShopifyError as e:
            return {"success": False, "error": e.message, "error_code": e.error_code}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_client_for_tenant(self, tenant_id: str) -> ShopifyClient:
        """Get a configured Shopify client for a tenant.
        Checks the brands table first (multi-brand setup), then falls back to tenants table.
        """
        # --- Try brands table first (credentials stored via Brands page) ---
        # Must be scoped to this tenant: the old `is_active=true, limit=1`
        # query had no tenant_id filter at all, so it could return an
        # unrelated tenant's brand (or miss this tenant's own connected
        # brand entirely if it wasn't the arbitrary first row). Prefer an
        # active brand, matching the tenant-scoped lookup pattern already
        # used elsewhere (e.g. saas_settings.py's _get_tenant_brand_async),
        # but fall back to any brand owned by this tenant — "is_active"
        # doesn't correlate with whether Shopify is actually connected, so a
        # connected brand that happens to be inactive must still be found.
        brands = supabase_select("brands", {"tenant_id": f"eq.{tenant_id}", "is_active": "is.true"})
        if not brands:
            brands = supabase_select("brands", {"tenant_id": f"eq.{tenant_id}"})
        brand = next((b for b in brands if b.get("shopify_connected")), None)
        if brand:
            shop_name = brand.get("shopify_shop_name") or brand.get("shopify_domain", "")
            raw_token = brand.get("shopify_access_token", "")
            access_token = decrypt_token(raw_token) if raw_token else ""
            if shop_name and access_token:
                domain = f"{shop_name}.myshopify.com" if not shop_name.endswith(".myshopify.com") else shop_name
                return ShopifyClient(
                    domain,
                    access_token,
                    brand.get("shopify_api_version") or SHOPIFY_API_VERSION
                )

        # --- Fall back to tenants table (legacy single-store setup) ---
        tenants = supabase_select("tenants", {"id": f"eq.{tenant_id}"})
        if not tenants:
            raise ShopifyError("Tenant not found", ShopifyErrorCode.INVALID_TOKEN)

        tenant = tenants[0]

        if not tenant.get("shopify_connected"):
            raise ShopifyError(
                "No Shopify store connected. Go to Brands → Add a brand and connect your Shopify store.",
                ShopifyErrorCode.INVALID_TOKEN
            )

        access_token = decrypt_token(tenant.get("shopify_access_token", ""))

        if not access_token:
            raise ShopifyError(
                "Missing Shopify access token. Please reconnect your store.",
                ShopifyErrorCode.INVALID_TOKEN
            )

        return ShopifyClient(
            tenant.get("shopify_domain"),
            access_token,
            tenant.get("shopify_api_version") or SHOPIFY_API_VERSION
        )


# The Conversation Detail view's "Order Context" panel calls fetch_shopify_order
# on every page load — a single live Shopify API round trip, no caching, so
# every time a ticket is opened (or reopened) it re-fetches the same order.
# Short-TTL cache, same pattern as tracking_service.py's Aftership cache.
# Explicitly invalidated after cancel/refund (see invalidate_order_cache) so a
# post-action reload never shows stale pre-action order status.
_ORDER_CACHE_TTL_SECONDS = 30
_order_cache: dict = {}  # (brand_id, order_num) -> (expires_at_monotonic, order_data)


def _order_cache_key(brand: dict, order_identifier: str) -> tuple:
    order_num = str(order_identifier).replace('#', '').replace('ORD-', '').strip()
    return (brand.get("id"), order_num)


def invalidate_order_cache(brand_id: str, order_identifier: str) -> None:
    """Call after a cancel/refund so the next fetch reflects the new order state."""
    order_num = str(order_identifier).replace('#', '').replace('ORD-', '').strip()
    _order_cache.pop((brand_id, order_num), None)


async def fetch_shopify_order(brand: dict, order_identifier: str) -> Optional[Dict[str, Any]]:
    """
    Look up a Shopify order by number for a given brand dict.
    Returns structured order data or None if not found.
    """
    try:
        domain = brand.get("shopify_domain", "")
        raw_token = brand.get("shopify_access_token", "")
        if not domain or not raw_token:
            return None

        cache_key = _order_cache_key(brand, order_identifier)
        now = time.monotonic()
        cached = _order_cache.get(cache_key)
        if cached and cached[0] > now:
            return cached[1]

        token = decrypt_token(raw_token) if raw_token else raw_token
        client = ShopifyClient(domain, token)

        order_num = str(order_identifier).replace('#', '').replace('ORD-', '').strip()
        result = await client.get_order(order_num)
        if not result.get("success"):
            return None

        order = result["order"]

        tracking_number = tracking_url = carrier = None
        if order.get("fulfillments"):
            f = order["fulfillments"][0]
            tracking_number = f.get("tracking_number")
            tracking_url = f.get("tracking_url")
            carrier = f.get("tracking_company")

        customer = order.get("customer") or {}
        first = customer.get("first_name", "")
        last = customer.get("last_name", "")
        customer_name = f"{first} {last}".strip() or order.get("email", "")

        order_data = {
            "id": str(order["id"]),
            "order_number": order["order_number"],
            "order_name": order.get("name"),
            "financial_status": order.get("financial_status"),
            "fulfillment_status": order.get("fulfillment_status"),
            "total_price": order.get("total_price"),
            "currency": order.get("currency"),
            "created_at": order.get("created_at"),
            "customer_email": order.get("email"),
            "customer_name": customer_name,
            "line_items": [
                {
                    "title": item.get("title"),
                    "quantity": item.get("quantity"),
                    "price": item.get("price"),
                    "variant_title": item.get("variant_title"),
                    "sku": item.get("sku"),
                }
                for item in order.get("line_items", [])
            ],
            "shipping_address": order.get("shipping_address"),
            "tracking_number": tracking_number,
            "tracking_url": tracking_url,
            "carrier": carrier,
            "transactions": [
                {
                    "id": str(t.get("id")),
                    "gateway": t.get("gateway"),
                    "amount": t.get("amount"),
                    "kind": t.get("kind"),
                    "status": t.get("status"),
                }
                for t in order.get("transactions", [])
            ],
            "tags": order.get("tags", ""),
            "note": order.get("note", ""),
            "cancel_reason": order.get("cancel_reason"),
            "cancelled_at": order.get("cancelled_at"),
        }
        _order_cache[cache_key] = (time.monotonic() + _ORDER_CACHE_TTL_SECONDS, order_data)
        return order_data
    except Exception as e:
        logger.error(f"[fetch_shopify_order] Error for order {order_identifier}: {e}")
        return None


# Singleton instance
shopify_service = ShopifyService()
