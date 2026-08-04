"""
Shopify scope verification
===========================
Separate from shopify_import_service.py (the importer) on purpose: this
module answers "what can we actually do with this token" — checked at
connect time and again before an import runs — so a missing scope surfaces
as a clear, specific message instead of the importer silently skipping
resources with no explanation.

Two live Shopify integration points write to brands.shopify_access_token
today (v2_brands.py's onboarding connect, and saas_settings.py's Settings
page connect) — both call check_and_store_scopes() after saving the token
so either path produces the same tracked state.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from src.lib.supabase_client import supabase_update
from src.services.shopify_service import ShopifyClient

logger = logging.getLogger(__name__)

# Every scope tResolv's Shopify integration can use, and what it unlocks —
# shown on the connection health page.
REQUIRED_SCOPES: Dict[str, str] = {
    "read_products": "Products",
    "read_content": "Store content (pages, blogs, policies)",
    "read_orders": "Orders",
}

# Subset the knowledge-base importer actually consumes. A missing
# read_orders doesn't block importing — nothing in shopify_import_service.py
# reads orders — so it's tracked for connection health but not the import gate.
IMPORT_SCOPES: List[str] = ["read_products", "read_content"]

# brand_id -> missing import scopes, set by the /shopify/import route when it
# declines to start a run that's guaranteed to import nothing. Kept here
# (not in shopify_import_service.py) so the importer itself is untouched.
_blocked_imports: Dict[str, List[str]] = {}


def set_blocked(brand_id: str, missing: List[str]) -> None:
    _blocked_imports[brand_id] = missing


def get_blocked(brand_id: str) -> Optional[List[str]]:
    return _blocked_imports.get(brand_id)


def clear_blocked(brand_id: str) -> None:
    _blocked_imports.pop(brand_id, None)


def missing_scopes(granted: List[str], required: List[str]) -> List[str]:
    granted_set = set(granted or [])
    return [s for s in required if s not in granted_set]


async def fetch_granted_scopes(client: ShopifyClient) -> List[str]:
    """GET /admin/oauth/access_scopes.json — Shopify's live, authoritative
    answer for what this specific token can actually do right now. Not
    under /admin/api/{version}/ like every other Admin REST call, so it
    can't go through ShopifyClient._request()'s base_url."""
    import asyncio
    url = f"https://{client.shop_domain}/admin/oauth/access_scopes.json"
    resp = await asyncio.to_thread(requests.get, url, headers=client.headers, timeout=20)
    resp.raise_for_status()
    scopes = resp.json().get("access_scopes", [])
    return sorted(s["handle"] for s in scopes if s.get("handle"))


async def fetch_app_name(client: ShopifyClient) -> Optional[str]:
    """Admin GraphQL currentAppInstallation — identifies which Shopify app
    actually issued the token in use, for the connection health page."""
    import asyncio
    try:
        result = await asyncio.to_thread(
            client._request, "POST", "graphql.json",
            {"query": "{ currentAppInstallation { app { title } } }"},
        )
        return (result.get("data") or {}).get("currentAppInstallation", {}).get("app", {}).get("title")
    except Exception as e:
        logger.warning(f"[ShopifyScopes] Could not fetch app name: {e}")
        return None


async def check_and_store_scopes(brand_id: str, client: ShopifyClient) -> Dict[str, Any]:
    """Best-effort: fetch granted scopes + issuing app name and persist them
    on the brand row. Never raises — a scope-check failure shouldn't fail
    the Shopify connection itself, since the token was already validated
    separately (shop.json) before this is called."""
    try:
        granted = await fetch_granted_scopes(client)
        app_name = await fetch_app_name(client)
        checked_at = datetime.now(timezone.utc).isoformat()
        supabase_update("brands", {"id": f"eq.{brand_id}"}, {
            "shopify_granted_scopes": granted,
            "shopify_app_name": app_name,
            "shopify_scopes_checked_at": checked_at,
        })
        clear_blocked(brand_id)
        return {"granted_scopes": granted, "app_name": app_name, "checked_at": checked_at}
    except Exception as e:
        logger.warning(f"[ShopifyScopes] Could not check scopes for brand {brand_id}: {e}")
        return {"granted_scopes": None, "app_name": None, "checked_at": None}
