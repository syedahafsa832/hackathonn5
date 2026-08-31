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
from datetime import datetime
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
# brand_id -> per-resource outcome list, e.g.
# [{"resource": "Products", "status": "skipped", "count": 0, "reason": "missing the read_products scope"}, ...]
# so the UI can render "Imported 18 products / Skipped Pages (missing read_content)"
# instead of one all-or-nothing message.
_import_report: Dict[str, List[Dict[str, Any]]] = {}


def get_import_status(brand_id: str) -> str:
    return _import_status.get(brand_id, "not_started")


def get_missing_scopes(brand_id: str) -> List[str]:
    return _import_missing_scopes.get(brand_id, [])


def get_import_report(brand_id: str) -> List[Dict[str, Any]]:
    return _import_report.get(brand_id, [])


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
    shopify_sync sources cascade-delete their rag_chunks via the FK.

    Exception: a source the merchant has manually edited in the Knowledge
    Base (metadata.merchant_edited, set by
    brand_knowledge_service.update_source_content) is left alone. Without
    this, any resync would silently overwrite an edit the merchant made on
    purpose the next time this runs."""
    existing = supabase_select("knowledge_base_sources", {
        "brand_id": f"eq.{brand_id}",
        "source_type": f"eq.{SOURCE_TYPE}",
    })
    for row in existing or []:
        if (row.get("metadata") or {}).get("merchant_edited"):
            continue
        supabase_delete("knowledge_base_sources", {"id": f"eq.{row['id']}"})


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


_SHOP_NAME_TAG_RE = re.compile(r"\{\{\s*shop_name\s*\}\}")
_LAST_UPDATED_TAG_RE = re.compile(r"\{\{\s*last_updated\s*\}\}")


def _format_policy_date(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%B %d, %Y")
    except Exception:
        return None


def _render_known_liquid_tags(body: str, shop_name: Optional[str], last_updated: Optional[str]) -> str:
    """Shopify's built-in policy templates (Settings > Policies > "Create
    from template", left uncustomized) are Liquid source, not finished
    prose - policies.json returns it exactly as stored, complete with
    {{ shop_name }} / {{ last_updated }} merge tags meant to be rendered by
    Shopify's own theme engine at storefront display time, plus (further
    into the body, not touched here) {% if %} region-specific legal
    conditionals only Shopify's real merchant-settings context can evaluate.

    Nothing here is missing or truncated - verified end-to-end against the
    stored rag_chunks for an actual imported privacy policy: every
    character Shopify returned is present, in order, from this literal
    opening through the document's natural end. It just reads as broken
    because these two tags were never substituted, right at the top of the
    document where a merchant looks first. Only the two tags we have a
    real, non-fabricated value for are replaced; anything else Shopify's
    template might use is left as-is rather than guessed at.
    """
    if not body:
        return body
    if shop_name:
        body = _SHOP_NAME_TAG_RE.sub(shop_name, body)
    if last_updated:
        body = _LAST_UPDATED_TAG_RE.sub(last_updated, body)
    return body


def _is_scope_error(message: str) -> bool:
    """Shopify's exact wording when an app's custom-app token doesn't have a
    scope granted/approved: '[API] This action requires merchant approval
    for <scope> scope.' No amount of retrying fixes this - the merchant has
    to add the scope in Shopify Admin and reinstall the app. Distinguishing
    this from a generic failure so the UI can say that instead of a dead-end
    "nothing found"."""
    m = (message or "").lower()
    return "merchant approval" in m or "access scope" in m


async def _import_store_info(client: ShopifyClient, brand_id: str) -> Dict[str, Any]:
    """shop.json needs no extra scope beyond the base connection - the same
    call validate_connection() already makes at connect time - so store
    name/contact/currency/location was available and simply wasn't being
    put into the knowledge base. One short "Store Information" source."""
    try:
        resp = await asyncio.to_thread(client._request, "GET", "shop.json")
    except Exception as e:
        logger.warning(f"[ShopifyImport] shop.json fetch failed for brand {brand_id}: {e}")
        return {"found": False, "count": 0}

    shop = (resp.get("data") or {}).get("shop", {})
    if not shop:
        return {"found": False, "count": 0}

    lines = [f"Store name: {shop.get('name')}"]
    if shop.get("email"):
        lines.append(f"Contact email: {shop['email']}")
    address_bits = [shop.get(k) for k in ("address1", "city", "province", "zip", "country_name") if shop.get(k)]
    if address_bits:
        lines.append(f"Address: {', '.join(address_bits)}")
    if shop.get("currency"):
        lines.append(f"Currency: {shop['currency']}")
    if shop.get("domain"):
        lines.append(f"Store URL: https://{shop['domain']}")

    content = "\n".join(lines)
    result = await brand_knowledge_service.upload_text(
        brand_id, name="Store Information", content=content,
        metadata={"type": "shopify_store_info"}, source_type=SOURCE_TYPE,
    )
    return {"found": bool(result.get("success")), "count": 1 if result.get("success") else 0}


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
            # Price and inventory are intentionally NOT included here. This
            # text gets embedded into RAG chunks with no freshness/TTL
            # mechanism, so a price/stock count baked in at import time goes
            # stale the moment the merchant changes it in Shopify - and
            # unlike this static text, nothing marks RAG content as
            # non-authoritative before it reaches the prompt. Live
            # price/inventory must come from the live-Shopify product-lookup
            # tool, never from RAG.
            handle = p.get("handle")
            lines = [f"Product: {title}", f"Description: {description}"]
            if handle:
                lines.append(f"URL: https://{client.shop_domain}/products/{handle}")
            image_src = (p.get("image") or {}).get("src")
            if image_src:
                lines.append(f"Image: {image_src}")
            parts.append("\n".join(lines))
        content = "\n\n".join(parts)
        result = await brand_knowledge_service.upload_text(
            brand_id, name=f"Products (batch {i // PRODUCTS_PER_CHUNK_DOC + 1})", content=content,
            metadata={"type": "shopify_product_batch", "count": len(batch)}, source_type=SOURCE_TYPE,
        )
        if result.get("success"):
            imported += len(batch)

    return {"found": imported > 0, "count": imported}


async def _import_collections(client: ShopifyClient, brand_id: str) -> Dict[str, Any]:
    """Custom + smart collections both live under the same read_products
    scope as products.json - not a separate permission."""
    collections: List[Dict[str, Any]] = []
    for endpoint in ("custom_collections.json", "smart_collections.json"):
        try:
            resp = await asyncio.to_thread(client._request, "GET", endpoint, None, {"limit": 250})
        except ShopifyError as e:
            logger.warning(f"[ShopifyImport] {endpoint} fetch failed for brand {brand_id}: {e.message}")
            if _is_scope_error(e.message):
                return {"found": False, "count": 0, "scope_error": "read_products"}
            continue
        except Exception as e:
            logger.warning(f"[ShopifyImport] {endpoint} fetch failed for brand {brand_id}: {e}")
            continue
        key = "custom_collections" if "custom" in endpoint else "smart_collections"
        collections.extend((resp.get("data") or {}).get(key, []))

    if not collections:
        return {"found": False, "count": 0}

    parts = []
    for c in collections:
        title = c.get("title", "Untitled collection")
        description = _strip_html(c.get("body_html", ""))
        parts.append(f"Collection: {title}" + (f"\nDescription: {description}" if description else ""))
    content = "\n\n".join(parts)
    result = await brand_knowledge_service.upload_text(
        brand_id, name="Collections", content=content,
        metadata={"type": "shopify_collections", "count": len(collections)}, source_type=SOURCE_TYPE,
    )
    return {"found": bool(result.get("success")), "count": len(collections) if result.get("success") else 0}


async def _import_policies(client: ShopifyClient, brand_id: str) -> Dict[str, Any]:
    """Imports every policy Shopify's policies.json actually returns for
    this store - return/refund and shipping as before, but also privacy
    policy, terms of service, and any other published policy - instead of
    only the two the old keyword match recognized. Named directly from
    each policy's own title, so whatever the merchant has published (in
    whatever wording Shopify used) becomes a real, findable KB source."""
    results: Dict[str, Any] = {"count": 0, "found": False}
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

    shop_name = None
    try:
        shop_resp = await asyncio.to_thread(client._request, "GET", "shop.json")
        shop_name = ((shop_resp.get("data") or {}).get("shop") or {}).get("name")
    except Exception:
        pass  # merge-tag substitution is best-effort — raw {{ shop_name }} stays if unavailable

    policies = (resp.get("data") or {}).get("policies", [])
    imported = 0
    for policy in policies:
        title = (policy.get("title") or "Store Policy").strip()
        last_updated = _format_policy_date(policy.get("updated_at"))
        body = _strip_html(policy.get("body", ""))
        body = _render_known_liquid_tags(body, shop_name, last_updated)
        if not body:
            continue
        r = await brand_knowledge_service.upload_text(
            brand_id, name=title, content=body,
            metadata={"type": "shopify_policy", "policy_title": title}, source_type=SOURCE_TYPE,
        )
        if r.get("success"):
            imported += 1

    results["count"] = imported
    results["found"] = imported > 0
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


def _build_import_report(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Turns the raw per-category dicts into a flat, UI-ready list so a
    missing scope on one resource (e.g. pages) doesn't hide the resources
    that DID succeed (e.g. products) behind one all-or-nothing message."""
    report: List[Dict[str, Any]] = []

    def _add(resource: str, count: int, scope_error: Optional[str]):
        if scope_error:
            report.append({
                "resource": resource, "status": "skipped", "count": 0,
                "reason": f"the Shopify app is missing the {scope_error} scope",
            })
        elif count:
            report.append({"resource": resource, "status": "imported", "count": count, "reason": None})
        else:
            report.append({"resource": resource, "status": "empty", "count": 0, "reason": None})

    store_info = summary.get("store_info", {})
    _add("Store Information", store_info.get("count", 0), None)

    products = summary.get("products", {})
    _add("Products", products.get("count", 0), products.get("scope_error"))

    collections = summary.get("collections", {})
    _add("Collections", collections.get("count", 0), collections.get("scope_error"))

    policies = summary.get("policies", {})
    _add("Policies", policies.get("count", 0), policies.get("scope_error"))

    pages = summary.get("pages", {})
    _add("Pages", pages.get("count", 0), pages.get("scope_error"))

    return report


async def run_shopify_import(brand_id: str) -> None:
    """The background job. Never raises — each category is independently
    best-effort so one failing (e.g. store has no policies configured)
    doesn't abort the rest."""
    _import_status[brand_id] = "running"
    _import_missing_scopes.pop(brand_id, None)
    _import_report.pop(brand_id, None)
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

        summary["store_info"] = await _import_store_info(client, brand_id)
        summary["products"] = await _import_products(client, brand_id)
        summary["collections"] = await _import_collections(client, brand_id)
        summary["policies"] = await _import_policies(client, brand_id)
        summary["pages"] = await _import_pages(client, brand_id)

        missing_scopes = set()
        for category in summary.values():
            scope = category.get("scope_error") if isinstance(category, dict) else None
            if scope:
                missing_scopes.add(scope)
        if missing_scopes:
            _import_missing_scopes[brand_id] = sorted(missing_scopes)

        _import_report[brand_id] = _build_import_report(summary)
        _import_status[brand_id] = "done"
        await supabase_service.log_onboarding_event(brand_id, "shopify_import_completed", summary)
    except Exception as e:
        logger.error(f"[ShopifyImport] Import failed for brand {brand_id}: {e}")
        _import_status[brand_id] = "failed"
        await supabase_service.log_onboarding_event(brand_id, "shopify_import_failed", {"error": str(e)})
