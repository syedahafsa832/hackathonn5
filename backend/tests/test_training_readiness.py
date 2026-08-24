"""
GET /api/v2/brands/{brand_id}/training-readiness (v2_brands.py).

A pure read-only composition over data that already exists elsewhere:
Reply Style (reply_style_service), Knowledge Base sources, ticket review
outcomes (tickets._compute_review_status), and Cancellation/Refund
Autopilot readiness (_compute_cancellation_readiness /
_compute_refund_readiness). No new learning system, no invented
thresholds — see history/prompts for the "Brand Training & Human-Verified
AI Rollout" feature this backs.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes import v2_brands  # noqa: E402
from src.api.routes.v2_brands import (  # noqa: E402
    _compute_generic_category_readiness,
    _compute_cancellation_readiness,
    _AUTOPILOT_MIN_SAMPLE,
)
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext  # noqa: E402

app = FastAPI()
app.include_router(v2_brands.router, prefix="/api/v2")
client = TestClient(app)

BRAND_ID = "brand-1"
TENANT_ID = "tenant-1"

BRAND = {
    "id": BRAND_ID, "tenant_id": TENANT_ID, "name": "Test Brand",
    "reply_style_mode": "preset", "reply_style_profile": None,
    "cancellation_autopilot_enabled": False, "refund_autopilot_enabled": False,
    "return_policy_days": None, "refund_notes": None, "final_sale_tags": None,
}


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


def _fake_select_factory(brand, kb_sources=None, examples=None, tickets=None, feedback=None, actions=None):
    """Mirrors real Postgres filtering closely enough for this endpoint:
    brands is scoped by tenant_id, and the tickets 'or' filter (reply_style
    service's organic-approved-reply query) actually excludes rows that
    wouldn't match server-side, unlike a naive return-everything fake."""
    def fn(table, params=None):
        params = params or {}
        if table == "brands":
            tid = params.get("tenant_id", "").replace("eq.", "")
            if tid and tid != brand.get("tenant_id"):
                return []
            return [brand]
        if table == "knowledge_base_sources":
            return kb_sources or []
        if table == "reply_style_examples":
            return examples or []
        if table == "tickets":
            rows = tickets or []
            if "or" in params:  # reply_style_service's approved-reply query
                return [t for t in rows if t.get("human_approved") or t.get("human_response")]
            return rows
        if table == "chat_feedback":
            return feedback or []
        if table == "actions":
            action_type = params.get("action_type", "").replace("eq.", "")
            return [a for a in (actions or []) if a.get("action_type") == action_type]
        return []
    return fn


def test_training_readiness_composes_train_verify_automate_from_real_data():
    tickets = [
        {"id": "t1", "ai_reply": "hi", "human_approved": True},
        {"id": "t2", "ai_reply": "hi", "human_approved": True, "human_response": "edited"},
        {"id": "t3", "ai_reply": "hi", "human_rejected": True},
        {"id": "t4", "ai_reply": "hi"},          # needs review
        {"id": "t5"},                            # no ai reply — not applicable to review
    ]
    examples = [{"id": "ex-1", "content": "..."}]
    kb_sources = [{"id": "k1", "status": "completed"}, {"id": "k2", "status": "failed"}]
    fake = _fake_select_factory(BRAND, kb_sources=kb_sources, examples=examples, tickets=tickets, feedback=[], actions=[])

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake), \
         patch("src.services.reply_style_service.supabase_select", side_effect=fake):
        resp = _with_tenant(lambda: client.get(f"/api/v2/brands/{BRAND_ID}/training-readiness"))

    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["train"]["knowledge"] == {"sources_count": 2, "completed_count": 1, "has_any": True}
    assert data["train"]["examples"]["count"] == 1
    # 1. Uploaded examples never inflate the organic 20-approved-replies counter (5)
    assert data["train"]["reply_style"]["approved_reply_count"] == 2  # t1, t2 only
    assert data["train"]["reply_style"]["min_replies_required"] == 20

    assert data["verify"]["total_ai_conversations"] == 4
    assert data["verify"]["conversations_needing_review"] == 1
    assert data["verify"]["conversations_reviewed"] == 3
    assert data["verify"]["rejection_rate"] == round(100 * 1 / 3, 1)
    assert data["verify"]["edit_rate"] == round(100 * 1 / 3, 1)

    assert data["automate"]["cancellation"]["autopilot_capable"] is True
    assert data["automate"]["refund"]["autopilot_capable"] is True
    assert data["automate"]["exchange"]["autopilot_capable"] is False
    assert data["automate"]["exchange"]["mode"] == "copilot"
    assert data["automate"]["address_change"]["autopilot_capable"] is False


# 5. Uploaded examples do not inflate approved_reply_count (zero organic approvals, one example)
def test_uploaded_examples_alone_never_inflate_approved_reply_count():
    fake = _fake_select_factory(BRAND, examples=[{"id": "ex-1", "content": "x"}], tickets=[])
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake), \
         patch("src.services.reply_style_service.supabase_select", side_effect=fake):
        resp = _with_tenant(lambda: client.get(f"/api/v2/brands/{BRAND_ID}/training-readiness"))

    data = resp.json()
    assert data["train"]["examples"]["count"] == 1
    assert data["train"]["reply_style"]["approved_reply_count"] == 0


# 10. Brand isolation — a tenant cannot fetch another tenant's brand readiness
def test_training_readiness_brand_isolation():
    fake = _fake_select_factory(BRAND)
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake):
        resp = _with_tenant(lambda: client.get(f"/api/v2/brands/{BRAND_ID}/training-readiness"), tenant_id="tenant-OTHER")

    assert resp.status_code == 404


# 11. Aggregate analytics never leak customer PII
def test_training_readiness_never_leaks_customer_pii():
    tickets = [{
        "id": "t1", "ai_reply": "hi", "human_approved": True,
        "customer_email": "leak@example.com", "customer_name": "Real Customer",
        "message": "a very private customer message",
    }]
    fake = _fake_select_factory(BRAND, tickets=tickets)
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake), \
         patch("src.services.reply_style_service.supabase_select", side_effect=fake):
        resp = _with_tenant(lambda: client.get(f"/api/v2/brands/{BRAND_ID}/training-readiness"))

    body = resp.text
    assert "leak@example.com" not in body
    assert "Real Customer" not in body
    assert "a very private customer message" not in body


# 8. Category readiness reuses the existing readiness calculation/thresholds
def test_generic_category_readiness_reuses_existing_thresholds():
    actions_exchange = [{"action_type": "exchange", "status": "executed"} for _ in range(_AUTOPILOT_MIN_SAMPLE)]
    actions_cancel = [{"action_type": "cancel_order", "status": "executed"} for _ in range(_AUTOPILOT_MIN_SAMPLE)]

    with patch("src.api.routes.v2_brands.supabase_select", return_value=actions_exchange):
        exchange_readiness = _compute_generic_category_readiness(BRAND_ID, "exchange", "Exchanges")
    with patch("src.api.routes.v2_brands.supabase_select", return_value=actions_cancel):
        cancel_readiness = _compute_cancellation_readiness(BRAND_ID)

    assert exchange_readiness["status"] == cancel_readiness["status"] == "ready_for_review"
    assert exchange_readiness["min_sample"] == cancel_readiness["min_sample"] == _AUTOPILOT_MIN_SAMPLE
    assert "autopilot" not in exchange_readiness  # no execution capability for this category
