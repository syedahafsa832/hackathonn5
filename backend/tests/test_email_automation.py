"""
Custom Email Automation (v2_brands.py + email_automation_service.py +
actions_service._post_execution_notify's custom-automation hook).

Covers: template rendering (no arbitrary field access), CRUD + tenant
isolation on the /email-automations endpoints, and the safety property
that a custom automation email can only ever be sent/queued from inside
the existing already-successful action-execution path — never for a
failed or still-pending action.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes import v2_brands  # noqa: E402
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext  # noqa: E402
from src.services import email_automation_service  # noqa: E402
from src.services.actions_service import actions_service  # noqa: E402
import src.services.financial_audit as financial_audit  # noqa: E402

app = FastAPI()
app.include_router(v2_brands.router, prefix="/api/v2")
client = TestClient(app)

BRAND_ID = "brand-1"
TENANT_ID = "tenant-1"
BRAND = {"id": BRAND_ID, "tenant_id": TENANT_ID, "name": "Test Brand"}
AUTOMATION_ID = "auto-1"
AUTOMATION_ROW = {
    "id": AUTOMATION_ID, "brand_id": BRAND_ID, "name": "Cancellation email",
    "trigger": "cancel_order", "subject": "Order {{order_number}} cancelled",
    "body": "Hi {{customer_name}}, your order {{order_number}} from {{brand_name}} is {{order_status}}.",
    "enabled": True, "requires_approval": False,
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


def _fake_select_factory(brand=BRAND, automations=None):
    def fn(table, params=None):
        params = params or {}
        if table == "brands":
            tid = params.get("tenant_id", "").replace("eq.", "")
            if tid and tid != brand.get("tenant_id"):
                return []
            return [brand]
        if table == "email_automations":
            rows = automations if automations is not None else [AUTOMATION_ROW]
            if "id" in params:
                rows = [r for r in rows if f"eq.{r['id']}" == params["id"]]
            if "trigger" in params:
                rows = [r for r in rows if f"eq.{r['trigger']}" == params["trigger"]]
            if "brand_id" in params:
                rows = [r for r in rows if f"eq.{r['brand_id']}" == params["brand_id"]]
            return rows
        return []
    return fn


# ══════════════════════════════════════════════════════════════════════════
# render_template — pure function, no eval/format, only the given dict
# ══════════════════════════════════════════════════════════════════════════

def test_render_template_substitutes_known_variables():
    out = email_automation_service.render_template(
        "Hi {{customer_name}}, order {{order_number}} is {{order_status}}.",
        {"customer_name": "Alex", "order_number": "#1234", "order_status": "Cancelled"},
    )
    assert out == "Hi Alex, order #1234 is Cancelled."


def test_render_template_never_evaluates_or_reaches_arbitrary_fields():
    # An attempted format-string/attribute-access injection must render
    # literally, never execute or raise — proves this is plain string
    # replacement, not str.format/Jinja/eval.
    out = email_automation_service.render_template(
        "{{customer_name.__class__}} {os.environ} {{secret_field}}",
        {"customer_name": "Alex"},
    )
    assert out == "{{customer_name.__class__}} {os.environ} {{secret_field}}"  # nothing here matches a known {{var}} exactly, so nothing is substituted or evaluated


def test_render_template_missing_variable_is_left_literal_not_crashed():
    out = email_automation_service.render_template("Hi {{customer_name}}, {{unresolvable}}", {"customer_name": "Alex"})
    assert out == "Hi Alex, {{unresolvable}}"


def test_variables_for_trigger_only_offers_refund_amount_on_refund():
    assert "refund_amount" in email_automation_service.variables_for_trigger("refund")
    assert "refund_amount" not in email_automation_service.variables_for_trigger("cancel_order")
    assert "tracking_number" not in email_automation_service.variables_for_trigger("refund")  # not backend-resolvable, never offered


# ══════════════════════════════════════════════════════════════════════════
# CRUD + tenant isolation
# ══════════════════════════════════════════════════════════════════════════

def test_create_email_automation():
    fake = _fake_select_factory(automations=[])
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake), \
         patch("src.api.routes.v2_brands.supabase_insert", return_value={**AUTOMATION_ROW}):
        resp = _with_tenant(lambda: client.post(
            f"/api/v2/brands/{BRAND_ID}/email-automations",
            json={"name": "Cancellation email", "trigger": "cancel_order",
                  "subject": "s", "body": "b", "enabled": True, "requires_approval": False},
        ))
    assert resp.status_code == 200, resp.text
    assert resp.json()["automation"]["trigger"] == "cancel_order"


def test_create_email_automation_rejects_duplicate_trigger():
    fake = _fake_select_factory(automations=[AUTOMATION_ROW])
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake):
        resp = _with_tenant(lambda: client.post(
            f"/api/v2/brands/{BRAND_ID}/email-automations",
            json={"name": "Dup", "trigger": "cancel_order", "subject": "s", "body": "b"},
        ))
    assert resp.status_code == 400


def test_edit_email_automation():
    fake = _fake_select_factory(automations=[AUTOMATION_ROW])
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake), \
         patch("src.api.routes.v2_brands.supabase_update", return_value={**AUTOMATION_ROW, "subject": "New subject"}):
        resp = _with_tenant(lambda: client.put(
            f"/api/v2/brands/{BRAND_ID}/email-automations/{AUTOMATION_ID}",
            json={"subject": "New subject"},
        ))
    assert resp.status_code == 200, resp.text
    assert resp.json()["automation"]["subject"] == "New subject"


def test_enable_disable_email_automation():
    fake = _fake_select_factory(automations=[{**AUTOMATION_ROW, "enabled": True}])
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake), \
         patch("src.api.routes.v2_brands.supabase_update", return_value={**AUTOMATION_ROW, "enabled": False}):
        resp = _with_tenant(lambda: client.put(
            f"/api/v2/brands/{BRAND_ID}/email-automations/{AUTOMATION_ID}",
            json={"enabled": False},
        ))
    assert resp.status_code == 200
    assert resp.json()["automation"]["enabled"] is False


def test_unauthorized_tenant_cannot_edit_another_tenants_automation():
    fake = _fake_select_factory(automations=[AUTOMATION_ROW])
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake):
        resp = _with_tenant(
            lambda: client.put(f"/api/v2/brands/{BRAND_ID}/email-automations/{AUTOMATION_ID}", json={"subject": "hijacked"}),
            tenant_id="tenant-OTHER",
        )
    assert resp.status_code == 404


def test_unauthorized_tenant_cannot_list_another_tenants_automations():
    fake = _fake_select_factory(automations=[AUTOMATION_ROW])
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake):
        resp = _with_tenant(
            lambda: client.get(f"/api/v2/brands/{BRAND_ID}/email-automations"),
            tenant_id="tenant-OTHER",
        )
    assert resp.status_code == 404


def test_preview_uses_realistic_sample_data_not_real_customer_data():
    fake = _fake_select_factory(automations=[AUTOMATION_ROW])
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake):
        resp = _with_tenant(lambda: client.post(f"/api/v2/brands/{BRAND_ID}/email-automations/{AUTOMATION_ID}/preview"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["subject"] == "Order #1234 cancelled"
    assert "Alex" in body["body"]
    assert body["status"] == "enabled_auto_send"


# ══════════════════════════════════════════════════════════════════════════
# The safety property: custom automation email only ever fires from the
# already-successful action-execution hook, and respects its own
# requires_approval gate independently of the action's own approval.
# ══════════════════════════════════════════════════════════════════════════

def _action(status="pending", action_type="cancel_order"):
    return {
        "id": "action-1", "tenant_id": TENANT_ID, "brand_id": BRAND_ID,
        "ticket_id": None, "status": status, "action_type": action_type,
        "order_id": "1001", "customer_email": "customer@example.com", "extracted_data": {},
    }


@pytest.mark.asyncio
async def test_successful_cancellation_with_auto_send_automation_sends_the_configured_email():
    captured = {}

    async def fake_send_email(brand, to, subject, body):
        captured["subject"], captured["body"] = subject, body
        return {"success": True}

    with patch("src.services.actions_service.supabase_select", return_value=[BRAND]), \
         patch("src.services.actions_service.supabase_update"), \
         patch("src.services.email_automation_service.supabase_select", side_effect=_fake_select_factory()), \
         patch("src.services.brand_gmail_service.brand_gmail_service.send_email", new=fake_send_email):
        await actions_service._post_execution_notify(
            _action(status="executed"), "cancel_order", {"order_name": "#1013"},
        )

    # The CONFIGURED template rendered, not the hardcoded default copy.
    assert "#1013" in captured["subject"]
    assert "is Cancelled" in captured["body"]
    assert "successfully cancelled" not in captured["body"]  # hardcoded default copy did NOT fire


@pytest.mark.asyncio
async def test_requires_approval_automation_queues_instead_of_sending():
    send_mock = AsyncMock(return_value={"success": True})
    insert_calls = []

    def fake_insert(table, data):
        insert_calls.append((table, data))
        return {"id": "pending-1", **data}

    pending_automation = {**AUTOMATION_ROW, "requires_approval": True}
    with patch("src.services.actions_service.supabase_select", return_value=[BRAND]), \
         patch("src.services.actions_service.supabase_update"), \
         patch("src.services.email_automation_service.supabase_select", side_effect=_fake_select_factory(automations=[pending_automation])), \
         patch("src.services.email_automation_service.supabase_insert", side_effect=fake_insert), \
         patch("src.services.brand_gmail_service.brand_gmail_service.send_email", new=send_mock):
        await actions_service._post_execution_notify(
            _action(status="executed"), "cancel_order", {"order_name": "#1013"},
        )

    send_mock.assert_not_called()  # never auto-sent
    assert len(insert_calls) == 1
    assert insert_calls[0][0] == "email_automation_pending"
    assert insert_calls[0][1]["status"] == "pending"


@pytest.mark.asyncio
async def test_no_custom_automation_falls_back_to_existing_default_copy():
    captured = {}

    async def fake_send_email(brand, to, subject, body):
        captured["body"] = body
        return {"success": True}

    with patch("src.services.actions_service.supabase_select", return_value=[BRAND]), \
         patch("src.services.actions_service.supabase_update"), \
         patch("src.services.email_automation_service.supabase_select", side_effect=_fake_select_factory(automations=[])), \
         patch("src.services.brand_gmail_service.brand_gmail_service.send_email", new=fake_send_email):
        await actions_service._post_execution_notify(
            _action(status="executed"), "cancel_order", {"order_name": "#1013"},
        )

    assert "successfully cancelled" in captured["body"]  # unchanged existing default behavior


@pytest.fixture(autouse=True)
def _reset_rate_buckets():
    financial_audit._rate_buckets.clear()
    yield
    financial_audit._rate_buckets.clear()


@pytest.mark.asyncio
async def test_failed_action_execution_never_triggers_any_confirmation_email():
    """A Shopify failure must never reach _post_execution_notify at all —
    proven at the real approve_action() entry point, not just by calling
    the notify function directly."""
    action = _action(status="pending")

    def fake_select(table, params=None):
        if table != "actions":
            return []
        return [dict(action)]

    def fake_update(table, match, data):
        if table != "actions":
            return {}
        action.update(data)
        return dict(action)

    with patch("src.services.actions_service.supabase_select", side_effect=fake_select), \
         patch("src.services.actions_service.supabase_update", side_effect=fake_update), \
         patch("src.services.financial_audit.supabase_select", return_value=[]), \
         patch("src.services.financial_audit.supabase_insert", return_value={}), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock()) as mock_getter, \
         patch.object(actions_service, "_log_event", new=AsyncMock()), \
         patch.object(actions_service, "_post_execution_notify", new=AsyncMock()) as notify_mock:
        mock_client = MagicMock()
        from src.services.shopify_service import ShopifyError
        mock_client.cancel_order = AsyncMock(side_effect=ShopifyError("Order already cancelled", "order_already_cancelled"))
        mock_getter.return_value = mock_client
        result = await actions_service.approve_action(TENANT_ID, "action-1", "staff@example.com")

    assert result["success"] is False
    notify_mock.assert_not_called()
