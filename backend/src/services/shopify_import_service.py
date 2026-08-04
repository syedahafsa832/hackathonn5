"""
Shopify auto-import — after a merchant connects Shopify, pull the store's
products, return policy, shipping policy, and pages/FAQ into the brand's
knowledge base (via brand_knowledge_service.upload_text) with no manual
document upload required.

Runs as a fire-and-forget background task (see run_shopify_import). Progress
is tracked two ways:
- Per-category: the knowledge_base_sources rows upload_text() already
  creates (status processing/completed/failed), tagged source_type=
  'shopify_sync' so they're distinguishable from manual-paste sources.
- Overall job state: an in-memory dict (_import_status). Single-process
  only - fine for this deployment, same tradeoff shopify_auth.py's
  OAUTH_STATES dict already makes elsewhere in this codebase.
"""
import asyncio
import logging
import re
import requests
from typing import Any, Dict, List, Optional

from src.lib.supabase_client import supabase_select, supabase_delete
from src.services.shopify_service import ShopifyClient, decrypt_token, ShopifyError
from src.services.brand_knowledge_service import brand_knowledge_service
from src.services.supabase_service import supabase_service

logger = logging.getLogger(__name__)

SOURCE_TYPE = "shopify_sync"
MAX_PRODUCTS = 500  # cap so a huge catalog can't blow the 10-minute onboarding budget
PRODUCTS_PER_CHUNK_DOC = 25  # products bundled into one upload_text() call

# brand_id -> "running" | "done" | "failed"
_import_status: Dict[str, str] = {}
# brand_id -> sorted list of scopes Shopify rejected with "requires merchant
# approval" - populated only when that specific error is seen, so the UI can
# tell a merchant exactly what to fix instead of a dead-end "nothing found".
_import_missing_scopes: Dict[str, List[str]] = {}


def get_import_status(brand_id: str) -> str:
    return _import_status.get(brand_id, "not_started")


def get_missing_scopes(brand_id: str) -> List[str]:
    return _import_missing_scopes.get(brand_id, [])


def _get_client_for_brand(brand: Dict[str, Any]) -> Optional[ShopifyClient]:
    domain = brand.get("shopify_domain")
    encrypted_token = brand.get("shopify_access_token")
    if not domain or not encrypted_token:
        return None
    try:
        token = decrypt_token(encrypted_token)
    except Exception as e:
        logger.error(f"[ShopifyImport] Token decrypt failed for brand {brand.get('id')}: {e}")
        return None
    return ShopifyClient(domain, token)


async def _clear_previous_import(brand_id: str) -> None:
    """Re-running import shouldn't pile up duplicate/stale RAG chunks — old
    shopify_sync sources cascade-delete their rag_chunks via the FK."""
    existing = supabase_select("knowledge_base_sources", {
        "brand_id": f"eq.{brand_id}",
        "source_type": f"eq.{SOURCE_TYPE}",
    })
    for row in existing or []:
        supabase_delete("knowledge_base_sources", {"id": f"eq.{row['id']}"})


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _is_scope_error(message: str) -> bool:
    """Shopify's exact wording when an app's custom-app token doesn't have a
    scope granted/approved: '[API] This action requires merchant approval
    for <scope> scope.' No amount of retrying fixes this - the merchant has
    to add the scope in Shopify Admin and reinstall the app. Distinguishing
    this from a generic failure so the UI can say that instead of a dead-end
    "nothing found"."""
    m = (message or "").lower()
    return "merchant approval" in m or "access scope" in m


async def _import_products(client: ShopifyClient, brand_id: str) -> Dict[str, Any]:
    products: List[Dict[str, Any]] = []
    url = f"{client.base_url}/products.json"
    params = {"limit": 250}
    try:
        while url and len(products) < MAX_PRODUCTS:
            resp = await asyncio.to_thread(
                requests.get, url, headers=client.headers, params=params, timeout=30
            )
            if resp.status_code != 200:
                logger.warning(f"[ShopifyImport] products.json returned {resp.status_code} for brand {brand_id}: {resp.text[:300]}")
                if resp.status_code == 403 and _is_scope_error(resp.text):
                    return {"found": False, "count": 0, "scope_error": "read_products"}
                break
            data = resp.json()
            products.extend(data.get("products", []))
            link_header = resp.headers.get("Link")
            url = None
            params = {}
            if link_header and 'rel="next"' in link_header:
                for part in link_header.split(","):
                    if 'rel="next"' in part:
                        url = part.split(";")[0].strip("< >")
                        break
    except Exception as e:
        logger.error(f"[ShopifyImport] Product fetch failed for brand {brand_id}: {e}")
        return {"found": False, "count": 0, "error": str(e)}

    products = products[:MAX_PRODUCTS]
    if not products:
        return {"found": False, "count": 0}

    imported = 0
    for i in range(0, len(products), PRODUCTS_PER_CHUNK_DOC):
        batch = products[i:i + PRODUCTS_PER_CHUNK_DOC]
        parts = []
        for p in batch:
            title = p.get("title", "Untitled product")
            description = _strip_html(p.get("body_html", ""))
            price = ""
            variants = p.get("variants") or []
            if variants:
                price = f"${variants[0].get('price', '')}"
            parts.append(f"Product: {title}\nPrice: {price}\nDescription: {description}")
        content = "\n\n".join(parts)
        result = await brand_knowledge_service.upload_text(
            brand_id, name=f"Products (batch {i // PRODUCTS_PER_CHUNK_DOC + 1})", content=content,
            metadata={"type": "shopify_product_batch", "count": len(batch)}, source_type=SOURCE_TYPE,
        )
        if result.get("success"):
            imported += len(batch)

    return {"found": imported > 0, "count": imported}


async def _import_policies(client: ShopifyClient, brand_id: str) -> Dict[str, Any]:
    results = {"return_policy": {"found": False}, "shipping_policy": {"found": False}}
    try:
        resp = await asyncio.to_thread(client._request, "GET", "policies.json")
    except ShopifyError as e:
        logger.warning(f"[ShopifyImport] policies.json fetch failed for brand {brand_id}: {e.message}")
        if _is_scope_error(e.message):
            results["scope_error"] = "read_content"
        return results
    except Exception as e:
        logger.warning(f"[ShopifyImport] policies.json fetch failed for brand {brand_id}: {e}")
        return results

    policies = (resp.get("data") or {}).get("policies", [])
    for policy in policies:
        title = (policy.get("title") or "").lower()
        body = _strip_html(policy.get("body", ""))
        if not body:
            continue
        if "refund" in title or "return" in title:
            r = await brand_knowledge_service.upload_text(
                brand_id, name="Return Policy", content=body,
                metadata={"type": "shopify_policy"}, source_type=SOURCE_TYPE,
            )
            results["return_policy"] = {"found": bool(r.get("success"))}
        elif "shipping" in title:
            r = await brand_knowledge_service.upload_text(
                brand_id, name="Shipping Policy", content=body,
                metadata={"type": "shopify_policy"}, source_type=SOURCE_TYPE,
            )
            results["shipping_policy"] = {"found": bool(r.get("success"))}

    return results


async def _import_pages(client: ShopifyClient, brand_id: str) -> Dict[str, Any]:
    try:
        resp = await asyncio.to_thread(client._request, "GET", "pages.json", None, {"limit": 100})
    except ShopifyError as e:
        logger.warning(f"[ShopifyImport] pages.json fetch failed for brand {brand_id}: {e.message}")
        if _is_scope_error(e.message):
            return {"found": False, "count": 0, "scope_error": "read_content"}
        return {"found": False, "count": 0}
    except Exception as e:
        logger.warning(f"[ShopifyImport] pages.json fetch failed for brand {brand_id}: {e}")
        return {"found": False, "count": 0}

    pages = (resp.get("data") or {}).get("pages", [])
    faq_pages = [p for p in pages if "faq" in (p.get("title", "") + p.get("handle", "")).lower()]
    other_pages = [p for p in pages if p not in faq_pages]

    imported = 0
    if faq_pages:
        content = "\n\n".join(
            f"{p.get('title', '')}\n{_strip_html(p.get('body_html', ''))}" for p in faq_pages
        )
        r = await brand_knowledge_service.upload_text(
            brand_id, name="FAQ Pages", content=content,
            metadata={"type": "shopify_faq"}, source_type=SOURCE_TYPE,
        )
        if r.get("success"):
            imported += len(faq_pages)

    if other_pages:
        content = "\n\n".join(
            f"{p.get('title', '')}\n{_strip_html(p.get('body_html', ''))}" for p in other_pages
        )
        if content.strip():
            r = await brand_knowledge_service.upload_text(
                brand_id, name="Store Pages", content=content,
                metadata={"type": "shopify_pages"}, source_type=SOURCE_TYPE,
            )
            if r.get("success"):
                imported += len(other_pages)

    return {"found": imported > 0, "count": imported, "faq_count": len(faq_pages)}


async def run_shopify_import(brand_id: str) -> None:
    """The background job. Never raises — each category is independently
    best-effort so one failing (e.g. store has no policies configured)
    doesn't abort the rest."""
    _import_status[brand_id] = "running"
    _import_missing_scopes.pop(brand_id, None)
    summary: Dict[str, Any] = {}
    try:
        brands = supabase_select("brands", {"id": f"eq.{brand_id}"})
        if not brands:
            _import_status[brand_id] = "failed"
            return
        brand = brands[0]

        client = _get_client_for_brand(brand)
        if not client:
            _import_status[brand_id] = "failed"
            return

        await _clear_previous_import(brand_id)

        summary["products"] = await _import_products(client, brand_id)
        summary["policies"] = await _import_policies(client, brand_id)
        summary["pages"] = await _import_pages(client, brand_id)

        missing_scopes = set()
        for category in summary.values():
            scope = category.get("scope_error") if isinstance(category, dict) else None
            if scope:
                missing_scopes.add(scope)
        if missing_scopes:
            _import_missing_scopes[brand_id] = sorted(missing_scopes)

        _import_status[brand_id] = "done"
        await supabase_service.log_onboarding_event(brand_id, "shopify_import_completed", summary)
    except Exception as e:
        logger.error(f"[ShopifyImport] Import failed for brand {brand_id}: {e}")
        _import_status[brand_id] = "failed"
        await supabase_service.log_onboarding_event(brand_id, "shopify_import_failed", {"error": str(e)})
