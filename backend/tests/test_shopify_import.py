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


class TestBuildImportReport:
    def test_mixed_success_and_scope_skips(self):
        summary = {
            "products": {"found": True, "count": 18},
            "collections": {"found": True, "count": 3},
            "policies": {"return_policy": {"found": False}, "shipping_policy": {"found": False}, "scope_error": "read_content"},
            "pages": {"found": False, "count": 0, "scope_error": "read_content"},
        }
        report = import_mod._build_import_report(summary)

        by_resource = {r["resource"]: r for r in report}
        assert by_resource["Products"] == {"resource": "Products", "status": "imported", "count": 18, "reason": None}
        assert by_resource["Collections"] == {"resource": "Collections", "status": "imported", "count": 3, "reason": None}
        assert by_resource["Policies"]["status"] == "skipped"
        assert "read_content" in by_resource["Policies"]["reason"]
        assert by_resource["Pages"]["status"] == "skipped"
        assert "read_content" in by_resource["Pages"]["reason"]

    def test_empty_but_no_scope_error_reports_empty_not_skipped(self):
        """Policies endpoint succeeded (200) but no Refund/Shipping policy body
        matched — that's a content gap, not a permissions problem, and the
        report should say so distinctly from a scope-blocked resource."""
        summary = {
            "products": {"found": False, "count": 0},
            "collections": {"found": False, "count": 0},
            "policies": {"return_policy": {"found": False}, "shipping_policy": {"found": False}},
            "pages": {"found": False, "count": 0},
        }
        report = import_mod._build_import_report(summary)
        assert all(r["status"] == "empty" for r in report)
        assert all(r["reason"] is None for r in report)


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

        async def fake_products(client, brand_id):
            return {"found": True, "count": 18}

        async def fake_collections(client, brand_id):
            return {"found": True, "count": 3}

        async def fake_policies(client, brand_id):
            return {"return_policy": {"found": False}, "shipping_policy": {"found": False}, "scope_error": "read_content"}

        async def fake_pages(client, brand_id):
            return {"found": False, "count": 0, "scope_error": "read_content"}

        with patch.object(import_mod, "supabase_select", return_value=[brand]), \
             patch.object(import_mod, "_get_client_for_brand", return_value=_client()), \
             patch.object(import_mod, "_clear_previous_import", new=AsyncMock()), \
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
