"""
Reship escalation UX/lifecycle fixes.

1. Dedup: return_actions_integration.py's reship branch never called
   _find_active_action before staging (unlike refund/cancel/exchange,
   which all already did) - a customer reporting "still no package" twice
   in one conversation could stage two competing reship escalations for
   the same order. Fixed to use the exact same guard + duplicate-status
   messaging the other action types already have.

2. Order enrichment: a reship escalation carried nothing but a bare order
   number (eligibility={} - reship never runs the return-eligibility
   check). Fixed to best-effort fetch the live Shopify order and attach
   items/fulfillment status/tracking/shipping address to extracted_data,
   so a human reviewer sees what's actually being requested.

3. Confirmation-email honesty: approve_action's RESHIP branch always
   returns execution_result.manual_action_required=True (no automated
   Shopify reship operation exists), but the post-approval confirmation
   email unconditionally told the CUSTOMER "arranged... you'll receive a
   tracking update once it ships" - overclaiming a step nothing had
   actually done. Fixed to mirror the CHANGE_ADDRESS branch's existing
   manual_action_required-aware wording.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services.return_actions_integration import ReturnActionsIntegration  # noqa: E402
from src.services.intent_detector import IntentResult  # noqa: E402
from src.services.actions_service import actions_service  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _run_reship(order_id="1234", existing_action=None, shopify_order=None):
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="reship", order_id=order_id, raw_address=None, confidence=0.9)

    fake_client = MagicMock()
    fake_client.get_order = AsyncMock(return_value={"success": True, "order": shopify_order} if shopify_order else {"success": False})

    with patch.object(integration, "_find_active_action", new=AsyncMock(return_value=existing_action)), \
         patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True, "action_id": "a1"})) as mock_create, \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock(return_value=fake_client)):
        result = run(integration.handle_return_intent(
            query="I never got my package for order 1234", customer_info={"name": "Jane", "email": "jane@example.com"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1",
            ticket_id="ticket-1", intent_result=intent,
        ))
    return result, mock_create


# ── 1. Dedup ──────────────────────────────────────────────────────────────

def test_reship_checks_for_an_existing_active_action_before_staging():
    result, mock_create = _run_reship(existing_action=None)
    mock_create.assert_awaited_once()
    assert mock_create.await_args.kwargs["action_type"] == "reship"
    assert "DELIVERY ISSUE QUEUED" in result["action_context"]


def test_reship_does_not_stage_a_second_action_when_one_is_already_pending():
    existing = {"status": "pending", "action_type": "reship"}
    result, mock_create = _run_reship(existing_action=existing)
    mock_create.assert_not_awaited()
    assert "ALREADY PENDING" in result["action_context"]
    assert "reship" in result["action_context"].lower()


def test_reship_duplicate_context_is_honest_about_manual_completion():
    """An already-executed reship whose execution_result says
    manual_action_required must never claim it's fully done."""
    existing = {"status": "executed", "action_type": "reship", "execution_result": {"manual_action_required": True}}
    result, mock_create = _run_reship(existing_action=existing)
    mock_create.assert_not_awaited()
    assert "Do NOT say it is fully complete" in result["action_context"]


# ── 2. Order enrichment ──────────────────────────────────────────────────

def test_reship_attaches_live_order_snapshot_when_lookup_succeeds():
    shopify_order = {
        "line_items": [{"title": "Black Wrap Maxi Dress", "variant_title": "M", "quantity": 1, "sku": "BWD-M"}],
        "fulfillment_status": "fulfilled",
        "shipping_address": {"address1": "123 Main St", "city": "Austin", "province": "TX", "zip": "78701", "country": "US"},
        "fulfillments": [{"tracking_company": "USPS", "tracking_number": "9400111", "tracking_url": "https://track.example/9400111"}],
    }
    _, mock_create = _run_reship(shopify_order=shopify_order)
    snapshot = mock_create.await_args.kwargs["reship_order_snapshot"]
    assert snapshot["items"][0]["title"] == "Black Wrap Maxi Dress"
    assert snapshot["fulfillment_status"] == "fulfilled"
    assert snapshot["tracking_number"] == "9400111"
    assert snapshot["shipping_address"]["city"] == "Austin"


def test_reship_still_stages_when_order_lookup_fails():
    """A failed/slow Shopify lookup must never block the escalation itself -
    only the enrichment is best-effort."""
    _, mock_create = _run_reship(shopify_order=None)
    mock_create.assert_awaited_once()
    assert mock_create.await_args.kwargs["reship_order_snapshot"] is None


# ── 3. Confirmation-email honesty ────────────────────────────────────────

BRAND_ROW = {"id": "brand-1", "name": "Test Brand", "gmail_connected": True}


def _action(**overrides):
    a = {
        "id": "action-1", "customer_email": "jane@example.com",
        "customer_name": "Jane", "ticket_id": None, "brand_id": "brand-1",
        "tenant_id": "tenant-1",
    }
    a.update(overrides)
    return a


@pytest.mark.asyncio
async def test_reship_confirmation_email_does_not_claim_it_shipped_when_manual_step_remains():
    captured = {}

    async def fake_send_email(brand, to, subject, body):
        captured["body"] = body
        return {"success": True}

    with patch("src.services.actions_service.supabase_select", return_value=[BRAND_ROW]), \
         patch("src.services.actions_service.supabase_update"), \
         patch("src.services.brand_gmail_service.brand_gmail_service.send_email", new=fake_send_email):
        await actions_service._post_execution_notify(
            _action(), "reship", {"manual_action_required": True, "order_name": "#1234"},
        )

    assert "arranged a replacement shipment" not in captured["body"]
    assert "arranging a replacement shipment now" in captured["body"]


@pytest.mark.asyncio
async def test_reship_confirmation_email_says_arranged_only_when_truly_automated():
    """Defensive: if a future automated Shopify reship path ever sets
    manual_action_required=False, the email should say it's actually done -
    proves the branch is a real if/else, not accidentally always-manual."""
    captured = {}

    async def fake_send_email(brand, to, subject, body):
        captured["body"] = body
        return {"success": True}

    with patch("src.services.actions_service.supabase_select", return_value=[BRAND_ROW]), \
         patch("src.services.actions_service.supabase_update"), \
         patch("src.services.brand_gmail_service.brand_gmail_service.send_email", new=fake_send_email):
        await actions_service._post_execution_notify(
            _action(), "reship", {"manual_action_required": False, "order_name": "#1234"},
        )

    assert "has been arranged" in captured["body"]
