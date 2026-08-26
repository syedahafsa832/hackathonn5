"""
Shopify auto-import pipeline tests
===================================
Covers the collections import added alongside products/policies/pages,
the per-resource report builder that drives the onboarding UX, and that
run_shopify_import degrades gracefully (imports what it can, reports
per-resource skip reasons) instead of an all-or-nothing failure when one
Shopify endpoint 403s for a missing scope. All Shopify/DB/RAG calls are
mocked — no live services required.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import src.services.shopify_import_service as import_mod  # noqa: E402
from src.services.shopify_service import ShopifyError  # noqa: E402


def _client():
    c = MagicMock()
    c.base_url = "https://test.myshopify.com/admin/api/2024-01"
    c.headers = {"X-Shopify-Access-Token": "test"}
    return c


class TestImportCollections:
    @pytest.mark.asyncio
    async def test_imports_custom_and_smart_collections(self):
        client = _client()

        def fake_request(method, endpoint, data=None, params=None):
            if "custom_collections" in endpoint:
                return {"data": {"custom_collections": [{"title": "Best Sellers", "body_html": "<p>Top picks</p>"}]}}
            return {"data": {"smart_collections": [{"title": "New Arrivals", "body_html": ""}]}}

        client._request = MagicMock(side_effect=fake_request)

        with patch.object(import_mod.brand_knowledge_service, "upload_text", new=AsyncMock(return_value={"success": True})) as mock_upload:
            result = await import_mod._import_collections(client, "brand-1")

        assert result == {"found": True, "count": 2}
        mock_upload.assert_awaited_once()
        _, kwargs = mock_upload.call_args
        assert "Best Sellers" in kwargs["content"]
        assert "New Arrivals" in kwargs["content"]
        assert kwargs["source_type"] == import_mod.SOURCE_TYPE

    @pytest.mark.asyncio
    async def test_missing_scope_degrades_gracefully(self):
        client = _client()
        client._request = MagicMock(
            side_effect=ShopifyError("[API] This action requires merchant approval for read_products scope.")
        )

        with patch.object(import_mod.brand_knowledge_service, "upload_text", new=AsyncMock()) as mock_upload:
            result = await import_mod._import_collections(client, "brand-1")

        assert result == {"found": False, "count": 0, "scope_error": "read_products"}
        mock_upload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_collections_found(self):
        client = _client()
        client._request = MagicMock(return_value={"data": {}})

        with patch.object(import_mod.brand_knowledge_service, "upload_text", new=AsyncMock()) as mock_upload:
            result = await import_mod._import_collections(client, "brand-1")

        assert result == {"found": False, "count": 0}
        mock_upload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_one_endpoint_failing_does_not_block_the_other(self):
        """custom_collections.json 500s (non-scope error) but smart_collections.json
        still succeeds — a transient failure on one endpoint shouldn't drop
        collections that were actually reachable."""
        client = _client()

        def fake_request(method, endpoint, data=None, params=None):
            if "custom_collections" in endpoint:
                raise ShopifyError("Internal Server Error", status_code=500)
            return {"data": {"smart_collections": [{"title": "Sale", "body_html": ""}]}}

        client._request = MagicMock(side_effect=fake_request)

        with patch.object(import_mod.brand_knowledge_service, "upload_text", new=AsyncMock(return_value={"success": True})):
            result = await import_mod._import_collections(client, "brand-1")

        assert result == {"found": True, "count": 1}


class TestImportStoreInfo:
    @pytest.mark.asyncio
    async def test_imports_shop_json_as_a_store_information_source(self):
        client = _client()
        client._request = MagicMock(return_value={"data": {"shop": {
            "name": "Test Store", "email": "hello@teststore.com", "currency": "USD",
            "domain": "teststore.com", "city": "Austin", "province": "TX",
            "zip": "78701", "country_name": "United States",
        }}})

        with patch.object(import_mod.brand_knowledge_service, "upload_text", new=AsyncMock(return_value={"success": True})) as mock_upload:
            result = await import_mod._import_store_info(client, "brand-1")

        assert result == {"found": True, "count": 1}
        _, kwargs = mock_upload.call_args
        assert kwargs["name"] == "Store Information"
        assert "Test Store" in kwargs["content"]
        assert "hello@teststore.com" in kwargs["content"]
        assert "USD" in kwargs["content"]
        assert kwargs["source_type"] == import_mod.SOURCE_TYPE

    @pytest.mark.asyncio
    async def test_shop_json_failure_is_non_fatal(self):
        client = _client()
        client._request = MagicMock(side_effect=Exception("network error"))

        with patch.object(import_mod.brand_knowledge_service, "upload_text", new=AsyncMock()) as mock_upload:
            result = await import_mod._import_store_info(client, "brand-1")

        assert result == {"found": False, "count": 0}
        mock_upload.assert_not_awaited()


class TestImportPolicies:
    @pytest.mark.asyncio
    async def test_imports_privacy_policy_and_terms_of_service_not_just_return_and_shipping(self):
        """The old matcher only recognized 'refund/return' and 'shipping' in
        the title - privacy policy and terms of service (both real,
        commonly-published Shopify policies) were silently dropped even
        though policies.json already returned them."""
        client = _client()
        client._request = MagicMock(return_value={"data": {"policies": [
            {"title": "Refund Policy", "body": "<p>Returns within 30 days.</p>"},
            {"title": "Shipping Policy", "body": "<p>Ships in 3-5 days.</p>"},
            {"title": "Privacy Policy", "body": "<p>We respect your privacy.</p>"},
            {"title": "Terms of Service", "body": "<p>By using this site...</p>"},
        ]}})

        with patch.object(import_mod.brand_knowledge_service, "upload_text", new=AsyncMock(return_value={"success": True})) as mock_upload:
            result = await import_mod._import_policies(client, "brand-1")

        assert result == {"count": 4, "found": True}
        uploaded_names = {call.kwargs["name"] for call in mock_upload.call_args_list}
        assert uploaded_names == {"Refund Policy", "Shipping Policy", "Privacy Policy", "Terms of Service"}

    @pytest.mark.asyncio
    async def test_missing_scope_degrades_gracefully(self):
        client = _client()
        client._request = MagicMock(
            side_effect=ShopifyError("[API] This action requires merchant approval for read_content scope.")
        )

        with patch.object(import_mod.brand_knowledge_service, "upload_text", new=AsyncMock()) as mock_upload:
            result = await import_mod._import_policies(client, "brand-1")

        assert result["scope_error"] == "read_content"
        mock_upload.assert_not_awaited()


class TestImportProducts:
    @pytest.mark.asyncio
    async def test_product_price_is_not_baked_into_rag_content(self):
        """Price must never be embedded into the RAG-imported product text -
        it's read once at import time with no freshness/TTL mechanism, so a
        stale price could reach the customer via RAG retrieval instead of
        the live Shopify price-lookup tool. Description/title are fine
        (static merchant content); price is a dynamic Shopify fact."""
        client = _client()
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {"products": [
            {"title": "Essential Hoodie", "body_html": "<p>Cozy fleece</p>",
             "handle": "essential-hoodie", "image": {"src": "https://cdn.shopify.com/hoodie.jpg"},
             "variants": [{"price": "49.99"}]},
        ]}
        fake_resp.headers = {}
        client.shop_domain = "test.myshopify.com"

        with patch.object(import_mod.requests, "get", return_value=fake_resp), \
             patch.object(import_mod.brand_knowledge_service, "upload_text", new=AsyncMock(return_value={"success": True})) as mock_upload:
            result = await import_mod._import_products(client, "brand-1")

        assert result["found"] is True
        _, kwargs = mock_upload.call_args
        assert "Essential Hoodie" in kwargs["content"]
        assert "Cozy fleece" in kwargs["content"]
        assert "49.99" not in kwargs["content"]
        assert "Price" not in kwargs["content"]

    @pytest.mark.asyncio
    async def test_product_url_and_image_are_included(self):
        """URL and image are static/cheap (unlike price/inventory) and
        directly useful for customer support - Luna can hand a customer the
        product link or image instead of only a text description."""
        client = _client()
        client.shop_domain = "test.myshopify.com"
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {"products": [
            {"title": "Essential Hoodie", "body_html": "<p>Cozy fleece</p>",
             "handle": "essential-hoodie", "image": {"src": "https://cdn.shopify.com/hoodie.jpg"}},
        ]}
        fake_resp.headers = {}

        with patch.object(import_mod.requests, "get", return_value=fake_resp), \
             patch.object(import_mod.brand_knowledge_service, "upload_text", new=AsyncMock(return_value={"success": True})) as mock_upload:
            await import_mod._import_products(client, "brand-1")

        _, kwargs = mock_upload.call_args
        assert "https://test.myshopify.com/products/essential-hoodie" in kwargs["content"]
        assert "https://cdn.shopify.com/hoodie.jpg" in kwargs["content"]


class TestBuildImportReport:
    def test_mixed_success_and_scope_skips(self):
        summary = {
            "store_info": {"found": True, "count": 1},
            "products": {"found": True, "count": 18},
            "collections": {"found": True, "count": 3},
            "policies": {"count": 0, "found": False, "scope_error": "read_content"},
            "pages": {"found": False, "count": 0, "scope_error": "read_content"},
        }
        report = import_mod._build_import_report(summary)

        by_resource = {r["resource"]: r for r in report}
        assert by_resource["Store Information"] == {"resource": "Store Information", "status": "imported", "count": 1, "reason": None}
        assert by_resource["Products"] == {"resource": "Products", "status": "imported", "count": 18, "reason": None}
        assert by_resource["Collections"] == {"resource": "Collections", "status": "imported", "count": 3, "reason": None}
        assert by_resource["Policies"]["status"] == "skipped"
        assert "read_content" in by_resource["Policies"]["reason"]
        assert by_resource["Pages"]["status"] == "skipped"
        assert "read_content" in by_resource["Pages"]["reason"]

    def test_empty_but_no_scope_error_reports_empty_not_skipped(self):
        """Policies endpoint succeeded (200) but no policy body was found —
        that's a content gap, not a permissions problem, and the report
        should say so distinctly from a scope-blocked resource."""
        summary = {
            "store_info": {"found": False, "count": 0},
            "products": {"found": False, "count": 0},
            "collections": {"found": False, "count": 0},
            "policies": {"count": 0, "found": False},
            "pages": {"found": False, "count": 0},
        }
        report = import_mod._build_import_report(summary)
        assert all(r["status"] == "empty" for r in report)
        assert all(r["reason"] is None for r in report)

    def test_policies_count_reflects_every_published_policy_not_just_two(self):
        """Privacy policy and terms of service (and anything else Shopify
        publishes) now count toward "imported", not just return/shipping."""
        summary = {"policies": {"count": 4, "found": True}}
        report = import_mod._build_import_report(summary)
        by_resource = {r["resource"]: r for r in report}
        assert by_resource["Policies"] == {"resource": "Policies", "status": "imported", "count": 4, "reason": None}


class TestRunShopifyImportDegradesGracefully:
    @pytest.mark.asyncio
    async def test_products_and_collections_succeed_while_pages_and_policies_are_scope_blocked(self):
        """Reproduces the user's exact scenario: a token with read_products
        but not read_content. Products/collections should import; pages should
        be skipped with a named reason; the run should finish 'done', not
        'failed', and _import_report should reflect the mixed outcome."""
        brand = {
            "id": "brand-1",
            "shopify_domain": "test.myshopify.com",
            "shopify_access_token": "encrypted-token",
        }

        async def fake_store_info(client, brand_id):
            return {"found": True, "count": 1}

        async def fake_products(client, brand_id):
            return {"found": True, "count": 18}

        async def fake_collections(client, brand_id):
            return {"found": True, "count": 3}

        async def fake_policies(client, brand_id):
            return {"count": 0, "found": False, "scope_error": "read_content"}

        async def fake_pages(client, brand_id):
            return {"found": False, "count": 0, "scope_error": "read_content"}

        with patch.object(import_mod, "supabase_select", return_value=[brand]), \
             patch.object(import_mod, "_get_client_for_brand", return_value=_client()), \
             patch.object(import_mod, "_clear_previous_import", new=AsyncMock()), \
             patch.object(import_mod, "_import_store_info", new=fake_store_info), \
             patch.object(import_mod, "_import_products", new=fake_products), \
             patch.object(import_mod, "_import_collections", new=fake_collections), \
             patch.object(import_mod, "_import_policies", new=fake_policies), \
             patch.object(import_mod, "_import_pages", new=fake_pages), \
             patch.object(import_mod.supabase_service, "log_onboarding_event", new=AsyncMock()):
            import_mod._import_status.pop("brand-1", None)
            import_mod._import_missing_scopes.pop("brand-1", None)
            import_mod._import_report.pop("brand-1", None)
            await import_mod.run_shopify_import("brand-1")

        assert import_mod.get_import_status("brand-1") == "done"
        assert import_mod.get_missing_scopes("brand-1") == ["read_content"]

        report = import_mod.get_import_report("brand-1")
        by_resource = {r["resource"]: r for r in report}
        assert by_resource["Products"]["status"] == "imported"
        assert by_resource["Products"]["count"] == 18
        assert by_resource["Collections"]["status"] == "imported"
        assert by_resource["Collections"]["count"] == 3
        assert by_resource["Pages"]["status"] == "skipped"
        assert by_resource["Policies"]["status"] == "skipped"
