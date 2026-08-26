"""
Knowledge Base "Add Document" file upload — POST /brands/{id}/knowledge/upload-file.

Extracts plain text from an uploaded .txt/.md/.pdf/.docx file, then hands
off to the exact same brand_knowledge_service.upload_text() pipeline the
existing paste-text /upload endpoint already uses (chunking, embedding,
knowledge_base_sources/rag_chunks writes) — no second ingestion system.
Covers text extraction per file type, validation (empty/oversized/
unsupported), and that the route is brand/tenant isolated the same way
every other v2_knowledge.py route already is (see
test_knowledge_base_brand_isolation.py, same pattern reused here).
"""
import io
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes import v2_knowledge  # noqa: E402
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext  # noqa: E402

app = FastAPI()
app.include_router(v2_knowledge.router, prefix="/api/v2")
client = TestClient(app)

TENANT_ID = "tenant-1"
BRAND_ID = "brand-1"
OTHER_TENANT = "tenant-attacker"


def _override_tenant(tenant_id=TENANT_ID):
    async def _dep():
        return TenantContext(tenant_id=tenant_id, email="merchant@example.com")
    return _dep


def _with_tenant(fn, tenant_id=TENANT_ID):
    app.dependency_overrides[get_current_tenant] = _override_tenant(tenant_id)
    try:
        return fn()
    finally:
        app.dependency_overrides.clear()


def _fake_brand_lookup(table, params=None):
    params = params or {}
    if table == "brands" and params.get("id") == f"eq.{BRAND_ID}" and params.get("tenant_id") == f"eq.{TENANT_ID}":
        return [{"id": BRAND_ID, "tenant_id": TENANT_ID}]
    return []


# ══════════════════════════════════════════════════════════════════════════
# _extract_text_from_file — pure text-extraction logic, per file type
# ══════════════════════════════════════════════════════════════════════════

def test_extracts_text_from_plain_txt_file():
    text = v2_knowledge._extract_text_from_file("policy.txt", b"Returns accepted within 30 days.")
    assert text == "Returns accepted within 30 days."


def test_extracts_text_from_markdown_file():
    text = v2_knowledge._extract_text_from_file("faq.md", "# FAQ\n\nWe ship worldwide.".encode("utf-8"))
    assert "We ship worldwide." in text


def test_extracts_text_from_pdf_via_pypdf():
    fake_page1 = MagicMock()
    fake_page1.extract_text.return_value = "Page one content."
    fake_page2 = MagicMock()
    fake_page2.extract_text.return_value = "Page two content."
    fake_reader = MagicMock()
    fake_reader.pages = [fake_page1, fake_page2]

    with patch("pypdf.PdfReader", return_value=fake_reader):
        text = v2_knowledge._extract_text_from_file("policy.pdf", b"%PDF-fake-bytes")

    assert "Page one content." in text
    assert "Page two content." in text


def test_extracts_text_from_docx_via_python_docx():
    fake_p1 = MagicMock(text="Shipping takes 3-5 business days.")
    fake_p2 = MagicMock(text="International orders may take longer.")
    fake_doc = MagicMock()
    fake_doc.paragraphs = [fake_p1, fake_p2]

    with patch("docx.Document", return_value=fake_doc):
        text = v2_knowledge._extract_text_from_file("shipping.docx", b"fake-docx-bytes")

    assert "Shipping takes 3-5 business days." in text
    assert "International orders may take longer." in text


def test_rejects_unsupported_file_extension():
    with pytest.raises(ValueError, match="Unsupported file type"):
        v2_knowledge._extract_text_from_file("virus.exe", b"whatever")


def test_pdf_extraction_failure_raises_clear_error():
    with patch("pypdf.PdfReader", side_effect=Exception("corrupt PDF")):
        with pytest.raises(ValueError, match="Could not read this PDF"):
            v2_knowledge._extract_text_from_file("broken.pdf", b"not-a-real-pdf")


# ══════════════════════════════════════════════════════════════════════════
# POST /upload-file — route-level: success, validation, reuse of the
# existing upload_text() pipeline, and brand/tenant isolation
# ══════════════════════════════════════════════════════════════════════════

def test_upload_file_success_reuses_existing_upload_text_pipeline():
    mock_upload = AsyncMock(return_value={
        "success": True, "source_id": "src-123", "chunk_count": 2, "total_tokens": 40,
    })
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=_fake_brand_lookup), \
         patch.object(v2_knowledge.brand_knowledge_service, "upload_text", new=mock_upload):
        resp = _with_tenant(lambda: client.post(
            f"/api/v2/brands/{BRAND_ID}/knowledge/upload-file",
            files={"file": ("return-policy.txt", io.BytesIO(b"Returns accepted within 30 days of purchase."), "text/plain")},
        ))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["source_id"] == "src-123"
    assert body["chunk_count"] == 2

    # Reused the SAME pipeline the paste-text endpoint uses - never a second
    # ingestion path.
    mock_upload.assert_awaited_once()
    _, kwargs = mock_upload.call_args
    assert kwargs["brand_id"] == BRAND_ID
    assert "Returns accepted within 30 days" in kwargs["content"]
    assert kwargs["name"] == "return-policy.txt"
    assert kwargs["metadata"]["type"] == "file_upload"
    assert kwargs["metadata"]["original_filename"] == "return-policy.txt"


def test_upload_file_uses_custom_name_when_provided():
    mock_upload = AsyncMock(return_value={"success": True, "source_id": "src-1", "chunk_count": 1, "total_tokens": 5})
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=_fake_brand_lookup), \
         patch.object(v2_knowledge.brand_knowledge_service, "upload_text", new=mock_upload):
        resp = _with_tenant(lambda: client.post(
            f"/api/v2/brands/{BRAND_ID}/knowledge/upload-file",
            files={"file": ("raw-export.txt", io.BytesIO(b"Some real policy content here."), "text/plain")},
            data={"name": "Return Policy"},
        ))

    assert resp.status_code == 200, resp.text
    _, kwargs = mock_upload.call_args
    assert kwargs["name"] == "Return Policy"


def test_upload_file_rejects_empty_file():
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=_fake_brand_lookup):
        resp = _with_tenant(lambda: client.post(
            f"/api/v2/brands/{BRAND_ID}/knowledge/upload-file",
            files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")},
        ))
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()


def test_upload_file_rejects_unsupported_extension():
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=_fake_brand_lookup):
        resp = _with_tenant(lambda: client.post(
            f"/api/v2/brands/{BRAND_ID}/knowledge/upload-file",
            files={"file": ("malware.exe", io.BytesIO(b"binary junk"), "application/octet-stream")},
        ))
    assert resp.status_code == 400
    assert "unsupported" in resp.json()["detail"].lower()


def test_upload_file_rejects_content_too_short_to_be_useful():
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=_fake_brand_lookup):
        resp = _with_tenant(lambda: client.post(
            f"/api/v2/brands/{BRAND_ID}/knowledge/upload-file",
            files={"file": ("tiny.txt", io.BytesIO(b"hi"), "text/plain")},
        ))
    assert resp.status_code == 400
    assert "readable text" in resp.json()["detail"].lower()


def test_upload_file_rejects_oversized_file():
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=_fake_brand_lookup), \
         patch.object(v2_knowledge, "_MAX_UPLOAD_FILE_BYTES", 10):
        resp = _with_tenant(lambda: client.post(
            f"/api/v2/brands/{BRAND_ID}/knowledge/upload-file",
            files={"file": ("big.txt", io.BytesIO(b"this file is way bigger than ten bytes"), "text/plain")},
        ))
    assert resp.status_code == 400
    assert "too large" in resp.json()["detail"].lower()


def test_upload_file_surfaces_pipeline_failure_as_client_error():
    mock_upload = AsyncMock(return_value={"success": False, "error": "Failed to generate embeddings"})
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=_fake_brand_lookup), \
         patch.object(v2_knowledge.brand_knowledge_service, "upload_text", new=mock_upload):
        resp = _with_tenant(lambda: client.post(
            f"/api/v2/brands/{BRAND_ID}/knowledge/upload-file",
            files={"file": ("policy.txt", io.BytesIO(b"Some real policy content here."), "text/plain")},
        ))
    assert resp.status_code == 400
    assert "embeddings" in resp.json()["detail"].lower()


def test_upload_file_to_another_tenants_brand_is_blocked():
    """Same IDOR check as every other v2_knowledge.py route
    (test_knowledge_base_brand_isolation.py) - an attacker-scoped tenant
    must never be able to write into a brand it doesn't own."""
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=_fake_brand_lookup):
        resp = _with_tenant(
            lambda: client.post(
                f"/api/v2/brands/{BRAND_ID}/knowledge/upload-file",
                files={"file": ("policy.txt", io.BytesIO(b"Some real policy content here."), "text/plain")},
            ),
            tenant_id=OTHER_TENANT,
        )
    assert resp.status_code == 404
