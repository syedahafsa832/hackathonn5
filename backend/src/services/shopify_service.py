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

from src.lib.supabase_client import supabase_select, supabase_update

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


def _get_encryption_key() -> bytes:
    """Get or derive encryption key for API credentials."""
    secret = os.getenv("ENCRYPTION_SECRET", os.getenv("SECRET_KEY", "default-dev-key-change-in-prod"))
    key = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(key)


def encrypt_token(value: str) -> str:
    """Encrypt sensitive data."""
    if not value:
        return ""
    f = Fernet(_get_encryption_key())
    return f.encrypt(value.encode()).decode()


def decrypt_token(value: str) -> str:
    """Decrypt sensitive data."""
    if not value:
        return ""
    try:
        f = Fernet(_get_encryption_key())
        return f.decrypt(value.encode()).decode()
    except Exception:
        # If decryption fails, return as-is (might be unencrypted)
        return value


class ShopifyClient:
    """
    Shopify API client with robust error handling and retry logic.
    """

    def __init__(
        self,
        shop_domain: str,
        access_token: str,
        api_version: str = "2024-01"
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
            restock: Whether to restock items
            notify_customer: Send notification email
        """
        # Get the order first
        order_result = await self.get_order(order_id)
        order = order_result.get("order", {})
        shopify_order_id = order.get("id")

        # Check order status
        if order.get("cancelled_at"):
            raise ShopifyError(
                "Cannot refund a cancelled order.",
                ShopifyErrorCode.ORDER_ALREADY_CANCELLED
            )

        # Calculate refund amount
        if amount is None:
            # Full refund - get total minus existing refunds
            total_price = float(order.get("total_price", 0))
            refunded = sum(
                float(r.get("transactions", [{}])[0].get("amount", 0))
                for r in order.get("refunds", [])
            )
            amount = total_price - refunded

        if amount <= 0:
            raise ShopifyError(
                "Order has already been fully refunded.",
                ShopifyErrorCode.ORDER_ALREADY_REFUNDED
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

        return {
            "success": True,
            "order_id": order_id,
            "order_name": order.get("name"),
            "new_address": updated_order.get("shipping_address"),
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
        brands = supabase_select("brands", {"is_active": "eq.true", "limit": "1"})
        if brands:
            brand = brands[0]
            shop_name = brand.get("shopify_shop_name") or brand.get("shopify_domain", "")
            raw_token = brand.get("shopify_access_token", "")
            access_token = decrypt_token(raw_token) if raw_token else ""
            if shop_name and access_token:
                domain = f"{shop_name}.myshopify.com" if not shop_name.endswith(".myshopify.com") else shop_name
                return ShopifyClient(
                    domain,
                    access_token,
                    brand.get("shopify_api_version", "2024-01")
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
            tenant.get("shopify_api_version", "2024-01")
        )


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

        return {
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
    except Exception as e:
        logger.error(f"[fetch_shopify_order] Error for order {order_identifier}: {e}")
        return None


# Singleton instance
shopify_service = ShopifyService()
