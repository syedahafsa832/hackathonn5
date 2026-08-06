"""
Shopify OAuth Callback
=======================
Public endpoint — Shopify drives this redirect directly, so there is no
user-auth context here (same reasoning as brand_gmail.py's /gmail/callback).
The signed state token (see services/shopify_oauth.py) proves which
brand/tenant initiated the connection; tampering with it fails verification
and no token is ever saved.

The OAuth-start step (authenticated, returns the authorize URL for the
frontend to navigate to) lives in v2_brands.py as
GET /api/v2/brands/{brand_id}/shopify/oauth/start, alongside the rest of
the brand-scoped Shopify routes.
"""
import os
import logging
import httpx
from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse
from typing import Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

# No prefix — registered directly in main.py so this is reachable at
# {API_BASE_URL}/shopify/oauth/callback, matching services/shopify_oauth.py's callback_uri().
router = APIRouter(tags=["shopify_auth"])


def _frontend_url() -> str:
    return os.getenv("FRONTEND_URL", "http://localhost:5173")


@router.get("/shopify/oauth/callback")
async def shopify_oauth_callback(
    code: Optional[str] = Query(None),
    shop: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    """Step 2 of the OAuth flow: Shopify redirects here after the merchant
    approves (or denies) the install. Verifies state, exchanges the code
    for an access token, and reuses the same connect logic the manual
    access-token flow uses."""
    frontend_url = _frontend_url()

    if error:
        logger.warning(f"[ShopifyOAuth] Denied by merchant: {error}")
        return RedirectResponse(f"{frontend_url}/onboarding?shopify_error=denied")

    if not code or not state or not shop:
        return RedirectResponse(f"{frontend_url}/onboarding?shopify_error=missing_params")

    from src.services.shopify_oauth import verify_state, normalize_shop_domain

    state_data = verify_state(state)
    if not state_data:
        return RedirectResponse(f"{frontend_url}/onboarding?shopify_error=invalid_state")

    brand_id = state_data.get("brand_id")
    requested_shop = state_data.get("shop")
    callback_shop = normalize_shop_domain(shop)
    if callback_shop != requested_shop:
        # Shopify's own `shop` param should always match what we sent it to
        # authorize against — if it doesn't, something is wrong enough to
        # not trust this exchange.
        logger.warning(f"[ShopifyOAuth] Shop mismatch: requested {requested_shop}, callback said {callback_shop}")
        return RedirectResponse(f"{frontend_url}/onboarding?shopify_error=invalid_state")

    client_id = os.getenv("SHOPIFY_CLIENT_ID")
    client_secret = os.getenv("SHOPIFY_CLIENT_SECRET")

    try:
        async with httpx.AsyncClient(timeout=15.0) as http_client:
            token_resp = await http_client.post(
                f"https://{callback_shop}/admin/oauth/access_token",
                json={"client_id": client_id, "client_secret": client_secret, "code": code},
            )
        if token_resp.status_code != 200:
            logger.error(f"[ShopifyOAuth] Token exchange failed ({token_resp.status_code}): {token_resp.text[:300]}")
            return RedirectResponse(f"{frontend_url}/onboarding?shopify_error=token_exchange_failed")
        access_token = token_resp.json().get("access_token")
        if not access_token:
            return RedirectResponse(f"{frontend_url}/onboarding?shopify_error=token_exchange_failed")
    except Exception as e:
        logger.error(f"[ShopifyOAuth] Token exchange error: {e}")
        return RedirectResponse(f"{frontend_url}/onboarding?shopify_error=connection_failed")

    # Reuse the exact same validate + save + domain-conflict-claim + scope-recording
    # logic the manual access-token flow uses — see v2_brands.py.
    from src.api.routes.v2_brands import _connect_shopify_credentials
    from src.lib.supabase_client import supabase_select

    brands = supabase_select("brands", {"id": f"eq.{brand_id}"})
    if not brands:
        return RedirectResponse(f"{frontend_url}/onboarding?shopify_error=invalid_state")
    tenant_id = brands[0].get("tenant_id")

    try:
        result = await _connect_shopify_credentials(brand_id, tenant_id, callback_shop, access_token)
    except Exception as e:
        logger.error(f"[ShopifyOAuth] Connect error: {e}")
        return RedirectResponse(f"{frontend_url}/onboarding?shopify_error=connection_failed")

    if not result.get("success"):
        error_code = result.get("error_code", "connection_failed")
        return RedirectResponse(f"{frontend_url}/onboarding?shopify_error={error_code}")

    # Best-effort product/order counts for the success screen — never block
    # a successful connection on this.
    products = orders = None
    client = result.get("client")
    if client:
        try:
            counts = await client.get_counts()
            products, orders = counts.get("products"), counts.get("orders")
        except Exception as e:
            logger.warning(f"[ShopifyOAuth] Could not fetch counts: {e}")

    shop_name = quote(result.get("shop_name") or "")
    params = f"shopify_connected=1&shop_name={shop_name}&shop_domain={result.get('shop_domain')}"
    if products is not None:
        params += f"&products={products}"
    if orders is not None:
        params += f"&orders={orders}"
    logger.info(f"[ShopifyOAuth] Connected {result.get('shop_domain')} to brand {result.get('brand_id')}")
    return RedirectResponse(f"{frontend_url}/onboarding?{params}")
