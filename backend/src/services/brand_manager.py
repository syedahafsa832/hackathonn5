"""
Multi-Brand Manager
===================
Central service for managing multiple Shopify brands/stores.
Each brand has its own Shopify credentials, email settings, and branding.
"""
import os
import logging
import requests
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from cryptography.fernet import Fernet
import base64
import hashlib

from src.lib.supabase_client import supabase_select, supabase_insert, supabase_update

logger = logging.getLogger(__name__)


def _get_encryption_key() -> bytes:
    """Get or derive encryption key for API credentials."""
    secret = os.getenv("ENCRYPTION_SECRET", os.getenv("SECRET_KEY", "default-dev-key-change-in-prod"))
    # Derive a valid Fernet key from the secret
    key = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(key)


def _encrypt(value: str) -> str:
    """Encrypt sensitive data."""
    if not value:
        return ""
    f = Fernet(_get_encryption_key())
    return f.encrypt(value.encode()).decode()


def _decrypt(value: str) -> str:
    """Decrypt sensitive data."""
    if not value:
        return ""
    try:
        f = Fernet(_get_encryption_key())
        return f.decrypt(value.encode()).decode()
    except Exception:
        # If decryption fails, return as-is (might be unencrypted)
        return value


class BrandManager:
    """
    Manages multiple Shopify brands with isolated credentials and settings.
    """

    def __init__(self):
        self._brand_cache: Dict[str, Dict] = {}

    async def create_brand(
        self,
        name: str,
        shopify_shop_name: str,
        shopify_access_token: str,
        support_email: str,
        sender_name: str = None,
        email_signature: str = None,
        logo_url: str = None,
        primary_color: str = "#000000",
        return_policy_days: int = 30,
        auto_approve_threshold: float = 50.0
    ) -> Dict[str, Any]:
        """
        Create a new brand with Shopify integration.

        Args:
            name: Brand display name (e.g., "Aurelio & Finch")
            shopify_shop_name: Shopify store name (without .myshopify.com)
            shopify_access_token: Shopify Admin API access token
            support_email: Email address for customer support
            sender_name: Display name for outgoing emails
            email_signature: HTML signature for emails
            logo_url: Brand logo URL
            primary_color: Brand primary color (hex)
            return_policy_days: Days allowed for returns
            auto_approve_threshold: Max order value for auto-approval

        Returns:
            Created brand object
        """
        try:
            # Validate Shopify credentials before saving
            if not await self._validate_shopify_credentials(shopify_shop_name, shopify_access_token):
                return {
                    "success": False,
                    "error": "Invalid Shopify credentials. Please check your shop name and access token."
                }

            # Encrypt the access token
            encrypted_token = _encrypt(shopify_access_token)

            brand_data = {
                "name": name,
                "shopify_shop_name": shopify_shop_name,
                "shopify_access_token": encrypted_token,
                "shopify_api_version": os.getenv("SHOPIFY_API_VERSION", "2024-01"),
                "support_email": support_email,
                "sender_name": sender_name or name,
                "email_signature": email_signature or f"— The {name} Team",
                "logo_url": logo_url,
                "primary_color": primary_color,
                "return_policy_days": return_policy_days,
                "auto_approve_threshold": auto_approve_threshold,
                "is_active": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }

            result = supabase_insert("brands", brand_data)

            if result:
                logger.info(f"[BrandManager] Created brand: {name} (ID: {result.get('id')})")
                # Clear cache
                self._brand_cache.clear()
                return {
                    "success": True,
                    "brand": {
                        "id": result.get("id"),
                        "name": name,
                        "shopify_shop_name": shopify_shop_name,
                        "support_email": support_email,
                        "is_active": True
                    }
                }
            else:
                return {"success": False, "error": "Failed to insert brand"}

        except Exception as e:
            logger.error(f"[BrandManager] Error creating brand: {e}")
            return {"success": False, "error": str(e)}

    async def get_brand(self, brand_id: str) -> Optional[Dict[str, Any]]:
        """Get a brand by ID with decrypted credentials."""
        # Check cache first
        if brand_id in self._brand_cache:
            return self._brand_cache[brand_id]

        try:
            brands = supabase_select("brands", {"id": f"eq.{brand_id}"})
            if brands:
                brand = brands[0]
                # Decrypt token for use
                brand["_decrypted_token"] = _decrypt(brand.get("shopify_access_token", ""))
                self._brand_cache[brand_id] = brand
                return brand
            return None
        except Exception as e:
            logger.error(f"[BrandManager] Error getting brand {brand_id}: {e}")
            return None

    async def get_brand_by_shop_name(self, shop_name: str) -> Optional[Dict[str, Any]]:
        """Get a brand by Shopify shop name."""
        try:
            brands = supabase_select("brands", {"shopify_shop_name": f"eq.{shop_name}"})
            if brands:
                brand = brands[0]
                brand["_decrypted_token"] = _decrypt(brand.get("shopify_access_token", ""))
                return brand
            return None
        except Exception as e:
            logger.error(f"[BrandManager] Error getting brand by shop: {e}")
            return None

    async def list_brands(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """List all brands (without sensitive credentials)."""
        try:
            filters = {}
            if active_only:
                filters["is_active"] = "eq.true"

            brands = supabase_select("brands", filters)

            # Remove sensitive data
            safe_brands = []
            for brand in brands:
                safe_brands.append({
                    "id": brand.get("id"),
                    "name": brand.get("name"),
                    "shopify_shop_name": brand.get("shopify_shop_name"),
                    "support_email": brand.get("support_email"),
                    "sender_name": brand.get("sender_name"),
                    "logo_url": brand.get("logo_url"),
                    "primary_color": brand.get("primary_color"),
                    "is_active": brand.get("is_active"),
                    "return_policy_days": brand.get("return_policy_days"),
                    "auto_approve_threshold": brand.get("auto_approve_threshold"),
                    "created_at": brand.get("created_at"),
                    "gmail_connected": brand.get("gmail_connected", False),
                    "gmail_email": brand.get("gmail_email"),
                })

            return safe_brands
        except Exception as e:
            logger.error(f"[BrandManager] Error listing brands: {e}")
            return []

    async def update_brand(self, brand_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update brand settings."""
        try:
            # If updating access token, encrypt it
            if "shopify_access_token" in updates:
                updates["shopify_access_token"] = _encrypt(updates["shopify_access_token"])

            updates["updated_at"] = datetime.now(timezone.utc).isoformat()

            supabase_update("brands", {"id": f"eq.{brand_id}"}, updates)

            # Clear cache
            self._brand_cache.pop(brand_id, None)

            return {"success": True, "message": "Brand updated"}
        except Exception as e:
            logger.error(f"[BrandManager] Error updating brand: {e}")
            return {"success": False, "error": str(e)}

    async def delete_brand(self, brand_id: str, soft_delete: bool = True) -> Dict[str, Any]:
        """Delete or deactivate a brand."""
        try:
            if soft_delete:
                supabase_update("brands", {"id": f"eq.{brand_id}"}, {
                    "is_active": False,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                })
            else:
                # Hard delete - be careful
                from src.lib.supabase_client import supabase_delete
                supabase_delete("brands", {"id": f"eq.{brand_id}"})

            self._brand_cache.pop(brand_id, None)
            return {"success": True, "message": "Brand deleted"}
        except Exception as e:
            logger.error(f"[BrandManager] Error deleting brand: {e}")
            return {"success": False, "error": str(e)}

    async def test_connection(self, brand_id: str) -> Dict[str, Any]:
        """Test Shopify API connection for a brand."""
        brand = await self.get_brand(brand_id)
        if not brand:
            return {"success": False, "error": "Brand not found"}

        return await self._validate_shopify_credentials(
            brand.get("shopify_shop_name"),
            brand.get("_decrypted_token"),
            return_details=True
        )

    async def _validate_shopify_credentials(
        self,
        shop_name: str,
        access_token: str,
        return_details: bool = False
    ) -> Any:
        """Validate Shopify credentials by making a test API call."""
        try:
            api_version = os.getenv("SHOPIFY_API_VERSION", "2024-01")
            url = f"https://{shop_name}.myshopify.com/admin/api/{api_version}/shop.json"
            headers = {
                "X-Shopify-Access-Token": access_token,
                "Content-Type": "application/json"
            }

            resp = requests.get(url, headers=headers, timeout=10)

            if resp.status_code == 200:
                shop_data = resp.json().get("shop", {})
                if return_details:
                    return {
                        "success": True,
                        "shop_name": shop_data.get("name"),
                        "shop_domain": shop_data.get("domain"),
                        "plan": shop_data.get("plan_name"),
                        "currency": shop_data.get("currency")
                    }
                return True
            else:
                logger.error(f"[BrandManager] Shopify validation failed: {resp.status_code} - {resp.text}")
                if return_details:
                    return {"success": False, "error": f"API error: {resp.status_code}"}
                return False

        except Exception as e:
            logger.error(f"[BrandManager] Shopify validation error: {e}")
            if return_details:
                return {"success": False, "error": str(e)}
            return False

    def get_shopify_client(self, brand: Dict[str, Any]) -> "BrandShopifyClient":
        """Get a Shopify client configured for a specific brand."""
        return BrandShopifyClient(
            shop_name=brand.get("shopify_shop_name"),
            access_token=brand.get("_decrypted_token"),
            api_version=brand.get("shopify_api_version", "2024-01")
        )


class BrandShopifyClient:
    """
    Shopify API client scoped to a specific brand.
    Used for executing actions (refunds, cancellations, address changes).
    """

    def __init__(self, shop_name: str, access_token: str, api_version: str = "2024-01"):
        self.shop_name = shop_name
        self.access_token = access_token
        self.api_version = api_version
        self.base_url = f"https://{shop_name}.myshopify.com/admin/api/{api_version}"
        self.headers = {
            "X-Shopify-Access-Token": access_token,
            "Content-Type": "application/json"
        }

    def _request(self, method: str, endpoint: str, data: dict = None) -> Dict[str, Any]:
        """Make authenticated request to Shopify."""
        url = f"{self.base_url}/{endpoint}"
        logger.info(f"[Shopify:{self.shop_name}] {method} {endpoint}")

        try:
            if method == "GET":
                resp = requests.get(url, headers=self.headers, params=data, timeout=30)
            elif method == "POST":
                resp = requests.post(url, headers=self.headers, json=data, timeout=30)
            elif method == "PUT":
                resp = requests.put(url, headers=self.headers, json=data, timeout=30)
            else:
                return {"success": False, "error": f"Unsupported method: {method}"}

            logger.info(f"[Shopify:{self.shop_name}] Response: {resp.status_code}")

            if resp.status_code in [200, 201]:
                return {"success": True, "data": resp.json()}
            else:
                return {"success": False, "error": resp.text, "status_code": resp.status_code}

        except Exception as e:
            logger.error(f"[Shopify:{self.shop_name}] Request failed: {e}")
            return {"success": False, "error": str(e)}

    async def get_order(self, order_id: str) -> Dict[str, Any]:
        """Get order by ID or order number."""
        # Try by order name first (e.g., #1001)
        result = self._request("GET", f"orders.json?name=%23{order_id}&status=any")
        if result.get("success") and result.get("data", {}).get("orders"):
            return {"success": True, "order": result["data"]["orders"][0]}

        # Try direct ID
        result = self._request("GET", f"orders/{order_id}.json")
        if result.get("success"):
            return {"success": True, "order": result["data"].get("order")}

        return {"success": False, "error": "Order not found"}

    async def process_refund(
        self,
        order_id: str,
        amount: float = None,
        note: str = None,
        restock: bool = False
    ) -> Dict[str, Any]:
        """
        Process a refund for an order.

        Args:
            order_id: Shopify order ID (numeric)
            amount: Refund amount (None = full refund)
            note: Admin note for the refund
            restock: Whether to restock items
        """
        # Get order to determine refund amount if not specified
        order_result = await self.get_order(order_id)
        if not order_result.get("success"):
            return order_result

        order = order_result.get("order", {})
        shopify_order_id = order.get("id")

        if amount is None:
            amount = float(order.get("total_price", 0))

        refund_data = {
            "refund": {
                "note": note or "Refund processed via AI Action System",
                "notify": True,
                "transactions": [
                    {
                        "kind": "refund",
                        "amount": str(amount)
                    }
                ]
            }
        }

        if restock:
            # Add line items for restock
            line_items = []
            for item in order.get("line_items", []):
                line_items.append({
                    "line_item_id": item.get("id"),
                    "quantity": item.get("quantity"),
                    "restock_type": "return"
                })
            refund_data["refund"]["refund_line_items"] = line_items

        result = self._request("POST", f"orders/{shopify_order_id}/refunds.json", refund_data)

        if result.get("success"):
            refund = result.get("data", {}).get("refund", {})
            return {
                "success": True,
                "refund_id": refund.get("id"),
                "amount": amount,
                "order_id": order_id
            }
        return result

    async def cancel_order(
        self,
        order_id: str,
        reason: str = "customer",
        email: bool = True,
        restock: bool = True
    ) -> Dict[str, Any]:
        """
        Cancel an order.

        Args:
            order_id: Shopify order ID
            reason: Cancellation reason (customer, fraud, inventory, declined, other)
            email: Send cancellation email to customer
            restock: Restock inventory
        """
        order_result = await self.get_order(order_id)
        if not order_result.get("success"):
            return order_result

        order = order_result.get("order", {})
        shopify_order_id = order.get("id")

        # Check if order can be cancelled
        if order.get("cancelled_at"):
            return {"success": False, "error": "Order is already cancelled"}

        if order.get("fulfillment_status") == "fulfilled":
            return {"success": False, "error": "Cannot cancel a fulfilled order. Process a refund instead."}

        cancel_data = {
            "reason": reason,
            "email": email,
            "restock": restock
        }

        result = self._request("POST", f"orders/{shopify_order_id}/cancel.json", cancel_data)

        if result.get("success"):
            return {
                "success": True,
                "order_id": order_id,
                "cancelled_at": result.get("data", {}).get("order", {}).get("cancelled_at")
            }
        return result

    async def update_shipping_address(
        self,
        order_id: str,
        new_address: Dict[str, str]
    ) -> Dict[str, Any]:
        """
        Update shipping address for an unfulfilled order.

        Args:
            order_id: Shopify order ID
            new_address: Dict with address1, address2, city, province, country, zip, phone
        """
        order_result = await self.get_order(order_id)
        if not order_result.get("success"):
            return order_result

        order = order_result.get("order", {})
        shopify_order_id = order.get("id")

        # Check if order is already fulfilled
        if order.get("fulfillment_status") == "fulfilled":
            return {"success": False, "error": "Cannot change address for fulfilled orders"}

        # Prepare address update
        update_data = {
            "order": {
                "id": shopify_order_id,
                "shipping_address": {
                    "address1": new_address.get("address1"),
                    "address2": new_address.get("address2", ""),
                    "city": new_address.get("city"),
                    "province": new_address.get("province", new_address.get("state")),
                    "country": new_address.get("country"),
                    "zip": new_address.get("zip", new_address.get("postal_code")),
                    "phone": new_address.get("phone", "")
                }
            }
        }

        result = self._request("PUT", f"orders/{shopify_order_id}.json", update_data)

        if result.get("success"):
            return {
                "success": True,
                "order_id": order_id,
                "new_address": new_address,
                "message": "Shipping address updated successfully"
            }
        return result


# Singleton instance
brand_manager = BrandManager()
