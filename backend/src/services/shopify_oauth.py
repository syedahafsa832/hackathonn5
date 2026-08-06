"""
Shopify OAuth State Signing
===========================
Mirrors brand_gmail_service.py's HMAC-signed-state pattern exactly — the
state parameter carries brand_id + the requested shop domain through
Shopify's redirect so the callback (which has no user-auth context, Shopify
drives that request) can verify which brand initiated the connection
without trusting anything client-supplied.
"""
import os
import re
import json
import base64
import hmac
import hashlib
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_STATE_TTL = 600  # 10-minute OAuth window, same as the Gmail flow


def _state_key() -> bytes:
    return os.getenv("SECRET_KEY", os.getenv("JWT_SECRET", "change-me-in-production")).encode()


def normalize_shop_domain(domain: str) -> str:
    """'mybrand' / 'mybrand.myshopify.com' / 'https://mybrand.myshopify.com/' -> 'mybrand.myshopify.com'."""
    domain = domain.strip().lower()
    domain = re.sub(r'^https?://', '', domain)
    domain = domain.rstrip('/')
    if not domain.endswith('.myshopify.com'):
        domain = f"{domain}.myshopify.com"
    return domain


def sign_state(brand_id: str, shop_domain: str) -> str:
    """HMAC-signed state token: base64(payload).signature — tampering is detectable,
    so the callback never has to trust a bare brand_id/shop from the query string."""
    payload = json.dumps({
        "brand_id": brand_id,
        "shop": shop_domain,
        "exp": int(time.time()) + _STATE_TTL,
    }).encode()
    sig = hmac.new(_state_key(), payload, hashlib.sha256).hexdigest()[:24]
    b64 = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"{b64}.{sig}"


def verify_state(state: str) -> Optional[dict]:
    """Verify a signed state token. Returns {"brand_id", "shop"} or None if
    invalid, expired, or tampered with."""
    try:
        b64, sig = state.rsplit(".", 1)
        pad = 4 - len(b64) % 4
        if pad != 4:
            b64 += "=" * pad
        payload = base64.urlsafe_b64decode(b64.encode())
        expected = hmac.new(_state_key(), payload, hashlib.sha256).hexdigest()[:24]
        if not hmac.compare_digest(sig, expected):
            logger.warning("[ShopifyOAuth] State signature mismatch — possible tampering")
            return None
        data = json.loads(payload)
        if data.get("exp", 0) < int(time.time()):
            logger.warning("[ShopifyOAuth] State expired")
            return None
        return {"brand_id": data.get("brand_id"), "shop": data.get("shop")}
    except Exception as e:
        logger.warning(f"[ShopifyOAuth] State verification error: {e}")
        return None


def callback_uri() -> str:
    base = os.getenv("API_BASE_URL", "http://localhost:8001")
    return f"{base}/shopify/oauth/callback"


def get_authorize_url(brand_id: str, shop: str) -> str:
    """Build the Shopify OAuth authorization URL a merchant is redirected to."""
    client_id = os.getenv("SHOPIFY_CLIENT_ID")
    if not client_id:
        raise ValueError("SHOPIFY_CLIENT_ID is not configured")
    scopes = os.getenv("SHOPIFY_SCOPES", "read_products,write_products")
    shop_domain = normalize_shop_domain(shop)
    state = sign_state(brand_id, shop_domain)
    return (
        f"https://{shop_domain}/admin/oauth/authorize"
        f"?client_id={client_id}"
        f"&scope={scopes}"
        f"&redirect_uri={callback_uri()}"
        f"&state={state}"
    )
