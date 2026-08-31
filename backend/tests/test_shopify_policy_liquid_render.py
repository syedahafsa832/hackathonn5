"""
Shopify policy import — Liquid merge-tag rendering.

Root cause: Shopify's built-in policy templates (Settings > Policies >
"Create from template", left uncustomized) are Liquid *source*, not
finished prose. policies.json returns that source as-is, complete with
{{ shop_name }} / {{ last_updated }} merge tags meant to be rendered by
Shopify's own theme engine at storefront display time. Nothing was ever
being truncated or dropped — the imported KB document contains the full
policy text, in order, from its literal Shopify-authored opening through
its natural end (confirmed against a real imported privacy policy's stored
rag_chunks: 25 chunks, zero gaps). It just *looked* cut off/broken because
the very first thing a merchant sees is a raw, unsubstituted "{{ last_updated }}
{{ shop_name }}" instead of a real date and store name.

_render_known_liquid_tags() only substitutes the two tags we have a real,
non-fabricated value for (shop name from shop.json, last-updated date from
the policy's own updated_at). Everything else — including the mid-document
{% if %} region-specific legal conditionals no REST endpoint can evaluate —
is left completely untouched. All Shopify/DB/RAG calls are mocked, no live
services required.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import src.services.shopify_import_service as import_mod  # noqa: E402


def _client():
    c = MagicMock()
    c.base_url = "https://test.myshopify.com/admin/api/2024-01"
    c.headers = {"X-Shopify-Access-Token": "test"}
    return c


# ── Unit coverage on the renderer itself ────────────────────────────────────

def test_substitutes_known_tags_with_real_values():
    body = "Last updated: {{ last_updated }} {{ shop_name }} operates this store."
    out = import_mod._render_known_liquid_tags(body, "Acme Co", "August 27, 2026")
    assert out == "Last updated: August 27, 2026 Acme Co operates this store."


def test_missing_values_leave_tags_untouched_not_fabricated():
    """No shop name / date available — don't invent one, don't crash."""
    body = "Last updated: {{ last_updated }} {{ shop_name }} operates this store."
    out = import_mod._render_known_liquid_tags(body, None, None)
    assert out == body


def test_unknown_tags_and_if_blocks_are_never_touched():
    """Only shop_name/last_updated are in scope — mid-document conditionals
    Shopify's own merchant-settings context would need to evaluate must be
    left exactly as Shopify sent them, not guessed at or stripped."""
    body = "Call {{ phone }}. {% if selling_to_europe %}EU rights apply.{% endif %}"
    out = import_mod._render_known_liquid_tags(body, "Acme Co", "August 27, 2026")
    assert out == body


def test_format_policy_date_handles_iso_and_missing():
    assert import_mod._format_policy_date("2026-08-27T09:22:34Z") == "August 27, 2026"
    assert import_mod._format_policy_date(None) is None
    assert import_mod._format_policy_date("not-a-date") is None


# ── End-to-end: _import_policies renders tags and preserves full content ───

@pytest.mark.asyncio
async def test_import_policies_renders_tags_and_keeps_full_body():
    client = _client()
    raw_body = (
        "Last updated: {{ last_updated }} {{ shop_name }} operates this store "
        "and website. {% if selling_to_europe %}EU rights apply.{% endif %} "
        "Contact us at {{ email }}."
    )

    def fake_request(method, endpoint, data=None, params=None):
        if endpoint == "policies.json":
            return {"data": {"policies": [
                {"title": "Privacy policy", "body": raw_body, "updated_at": "2026-08-27T09:22:34Z"},
            ]}}
        if endpoint == "shop.json":
            return {"data": {"shop": {"name": "Syedahafsa1983's Store"}}}
        raise AssertionError(f"unexpected endpoint {endpoint}")

    client._request = MagicMock(side_effect=fake_request)

    with patch.object(import_mod.brand_knowledge_service, "upload_text",
                       new=AsyncMock(return_value={"success": True})) as mock_upload:
        result = await import_mod._import_policies(client, "brand-1")

    assert result == {"count": 1, "found": True}
    _, kwargs = mock_upload.call_args
    content = kwargs["content"]

    # The two known tags are rendered with real values.
    assert "{{ shop_name }}" not in content
    assert "{{ last_updated }}" not in content
    assert "Syedahafsa1983's Store operates this store and website" in content
    assert "Last updated: August 27, 2026" in content

    # Nothing else was dropped: the conditional block and the unresolvable
    # {{ email }} tag (no real value available here) survive untouched, and
    # the tail of the document ("Contact us at") is still present — i.e.
    # this isn't a truncation fix, it's a targeted substitution.
    assert "{% if selling_to_europe %}EU rights apply.{% endif %}" in content
    assert "Contact us at {{ email }}." in content


@pytest.mark.asyncio
async def test_import_policies_still_works_when_shop_json_fails():
    """shop.json is best-effort for merge-tag rendering — its failure must
    not block the policy import itself (existing behavior, still true)."""
    client = _client()
    raw_body = "Last updated: {{ last_updated }} {{ shop_name }} operates this store."

    def fake_request(method, endpoint, data=None, params=None):
        if endpoint == "policies.json":
            return {"data": {"policies": [{"title": "Privacy policy", "body": raw_body}]}}
        if endpoint == "shop.json":
            raise Exception("network error")
        raise AssertionError(f"unexpected endpoint {endpoint}")

    client._request = MagicMock(side_effect=fake_request)

    with patch.object(import_mod.brand_knowledge_service, "upload_text",
                       new=AsyncMock(return_value={"success": True})) as mock_upload:
        result = await import_mod._import_policies(client, "brand-1")

    assert result == {"count": 1, "found": True}
    _, kwargs = mock_upload.call_args
    # No shop name available — tag left in place rather than fabricated.
    assert "{{ shop_name }}" in kwargs["content"]


@pytest.mark.asyncio
async def test_resync_of_a_real_previously_broken_document_no_longer_has_raw_tags():
    """Regression for the actual live document (brand 549ee056…, source
    21296eb0…): its stored chunk 0 opens with the exact raw-tag text below.
    A re-sync (_clear_previous_import, then _import_policies again — the
    same path POST /{brand_id}/shopify/import?force=true uses) must
    replace the one un-edited source rather than add a second, keep every
    sentence of the real body intact, and produce an opening with no
    remaining {{ }} tags once shop_name/last_updated are available."""
    real_opening = (
        'Last updated: {{ last_updated }} {{ shop_name }} operates this store and website, '
        'including all related information, content, features, tools, products and services, '
        'in order to provide you, the customer, with a curated shopping experience (the "Services"). '
        '{{ shop_name }} is powered by Shopify, which enables us to provide the Services to you.'
    )
    real_tail = "For the purpose of applicable data protection laws, we are the data controller of your personal information."
    real_body = f"{real_opening} ... {real_tail}"  # full doc elided; opening/tail are what's asserted

    with patch.object(import_mod, "supabase_select", return_value=[
        {"id": "21296eb0-0fe6-42ef-be19-887c7bf7ea7f", "metadata": {"type": "shopify_policy"}},
    ]) as mock_select, \
         patch.object(import_mod, "supabase_delete") as mock_delete:
        await import_mod._clear_previous_import("549ee056-c5c4-4c4e-8eed-e6d47c6591f7")

    # Not merchant-edited -> the one existing source is removed (cascade-deletes
    # its chunks), never left alongside a newly-created one -> no duplicate.
    mock_select.assert_called_once()
    mock_delete.assert_called_once_with(
        "knowledge_base_sources", {"id": "eq.21296eb0-0fe6-42ef-be19-887c7bf7ea7f"}
    )

    client = _client()

    def fake_request(method, endpoint, data=None, params=None):
        if endpoint == "policies.json":
            return {"data": {"policies": [
                {"title": "Privacy policy", "body": real_body, "updated_at": "2026-08-27T09:22:34Z"},
            ]}}
        if endpoint == "shop.json":
            return {"data": {"shop": {"name": "Syedahafsa1983's Store"}}}
        raise AssertionError(f"unexpected endpoint {endpoint}")

    client._request = MagicMock(side_effect=fake_request)

    with patch.object(import_mod.brand_knowledge_service, "upload_text",
                       new=AsyncMock(return_value={"success": True})) as mock_upload:
        result = await import_mod._import_policies(client, "549ee056-c5c4-4c4e-8eed-e6d47c6591f7")

    assert result == {"count": 1, "found": True}
    mock_upload.assert_awaited_once()  # exactly one re-created source — no duplicate
    call_kwargs = mock_upload.call_args.kwargs
    content = call_kwargs["content"]

    # Brand isolation: re-created source is written for the same brand, none other.
    assert mock_upload.call_args.args[0] == "549ee056-c5c4-4c4e-8eed-e6d47c6591f7"

    # The known broken opening is gone.
    assert "{{" not in content.split("...")[0]
    assert "Syedahafsa1983's Store operates this store and website" in content
    assert "Last updated: August 27, 2026" in content

    # Not truncated: the document's real tail sentence is still present.
    assert real_tail in content
