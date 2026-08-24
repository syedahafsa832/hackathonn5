"""
Cancellation time-window eligibility bug (Tasks 2 & 3).

Root cause: a merchant's free-text KB policy ("orders can only be
cancelled within 2 hours of being placed") was never checked
deterministically against the real Shopify order.created_at - only
consulted as raw text, either (a) via return_actions_integration.py's
cancel-with-policy-text branch, which blanket-escalated to "needs a human
check" regardless of whether the order was obviously outside the window,
or (b) left for the LLM to reason about freehand from RAG text + the
customer's own wording ("placed yesterday"), producing hedges like
"might still be within the window" even when the timestamps made the
answer obvious.

Fix: actions_manager.evaluate_cancellation_window(policy_text,
order_created_at) - a small, pure, deterministic function - extracts a
"within N hours/days" window from the policy text and computes real
elapsed time from order.created_at. Wired into both
return_actions_integration.py (short-circuits a definitive NOT ELIGIBLE
before the blanket-escalate branch; still routes ELIGIBLE through the
existing human-approval staging - safety unchanged) and
customer_success_agent.py (a plain cancel-shaped question about a
still-active order gets the same grounded fact, even when it never
reaches the action-staging flow at all).
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services.actions_manager import actions_manager  # noqa: E402
from src.services.return_actions_integration import ReturnActionsIntegration  # noqa: E402
from src.services.intent_detector import IntentResult  # noqa: E402

POLICY_2H = "Orders can only be cancelled within 2 hours of being placed."


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _created_at(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


# ── 1-4, 6: pure deterministic evaluator ────────────────────────────────────

def test_order_created_30_minutes_ago_is_eligible():
    result = actions_manager.evaluate_cancellation_window(POLICY_2H, _created_at(30))
    assert result["eligible"] is True


def test_order_created_1h59m_ago_is_eligible():
    result = actions_manager.evaluate_cancellation_window(POLICY_2H, _created_at(119))
    assert result["eligible"] is True


def test_order_created_just_beyond_2_hours_ago_is_not_eligible():
    result = actions_manager.evaluate_cancellation_window(POLICY_2H, _created_at(121))
    assert result["eligible"] is False


def test_order_created_yesterday_is_not_eligible():
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    result = actions_manager.evaluate_cancellation_window(POLICY_2H, yesterday)
    assert result["eligible"] is False
    assert result["elapsed_hours"] > 23


def test_timezone_aware_timestamp_with_explicit_offset_is_handled_correctly():
    # +05:00 offset, equivalent to 30 minutes ago in UTC
    local_time = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=5))) - timedelta(minutes=30)
    result = actions_manager.evaluate_cancellation_window(POLICY_2H, local_time.isoformat())
    assert result["eligible"] is True
    assert result["elapsed_hours"] < 1


def test_naive_timestamp_without_timezone_is_treated_as_utc_not_crashed():
    naive = (datetime.now(timezone.utc) - timedelta(minutes=30)).replace(tzinfo=None).isoformat()
    result = actions_manager.evaluate_cancellation_window(POLICY_2H, naive)
    assert result is not None
    assert result["eligible"] is True


# ── 7: missing/unreliable timestamp -> safe fallback, never a guess ───────

def test_missing_order_timestamp_returns_none_never_guesses():
    assert actions_manager.evaluate_cancellation_window(POLICY_2H, None) is None


def test_unparseable_timestamp_returns_none_never_guesses():
    assert actions_manager.evaluate_cancellation_window(POLICY_2H, "not-a-real-date") is None


def test_policy_text_without_a_window_pattern_returns_none():
    assert actions_manager.evaluate_cancellation_window("We care about our customers.", _created_at(30)) is None


def test_no_policy_text_returns_none():
    assert actions_manager.evaluate_cancellation_window("", _created_at(30)) is None
    assert actions_manager.evaluate_cancellation_window(None, _created_at(30)) is None


# ── 5: customer's claimed order age never overrides the real timestamp ────

def test_day_based_policy_text_is_also_extracted():
    result = actions_manager.evaluate_cancellation_window(
        "Cancellations are accepted within 1 day of ordering.", _created_at(30 * 60)  # 30 hours ago
    )
    assert result["window_hours"] == 24
    assert result["eligible"] is False


# ── return_actions_integration.py wiring ────────────────────────────────────

def _eligible_unfulfilled_order(created_at):
    return {
        "eligible": False, "reason": "order not yet fulfilled",
        "order": {"fulfillment_status": "unfulfilled", "created_at": created_at},
        "items": [], "order_total": "45.00",
    }


def _run_cancel(query, order_created_at, policy_text=POLICY_2H):
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="cancel", order_id="1234", raw_address=None, confidence=0.9)

    with patch.object(integration.actions, "check_return_eligibility",
                       new=AsyncMock(return_value=_eligible_unfulfilled_order(order_created_at))), \
         patch.object(integration.actions, "get_custom_policy_text", new=AsyncMock(return_value=policy_text)), \
         patch.object(integration, "_find_active_action", new=AsyncMock(return_value=None)), \
         patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True, "action_id": "a1"})) as mock_create:
        result = run(integration.handle_return_intent(
            query=query, customer_info={"name": "Jane", "email": "customer@example.com"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1",
            intent_result=intent,
        ))
    return result, mock_create


def test_order_placed_yesterday_with_2h_policy_is_flatly_not_eligible_no_action_staged():
    """The exact reported bug, at the action-staging layer: customer says
    'placed yesterday' - real Shopify timestamp confirms it - no action
    is created, and the reply must not hedge."""
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    result, mock_create = _run_cancel("can I cancel my order? it was placed yesterday", yesterday)

    mock_create.assert_not_awaited()
    assert "NOT ELIGIBLE" in result["action_context"]
    assert "no longer be cancelled" in result["action_context"]


def test_customers_claimed_order_age_never_overrides_the_real_shopify_timestamp():
    """Customer claims 'yesterday' but Shopify's own timestamp says 30
    minutes ago - the real timestamp must win, and the order IS eligible."""
    result, mock_create = _run_cancel(
        "cancel my order, I placed it yesterday I think", _created_at(30),
    )
    mock_create.assert_awaited_once()  # proceeds to existing human-approval staging
    assert "NOT ELIGIBLE" not in result["action_context"]


def test_order_inside_window_still_requires_existing_human_approval_staging():
    """Safety unchanged: eligible does not mean auto-approved - it still
    goes through the existing manual-review staging path."""
    result, mock_create = _run_cancel("cancel my order please", _created_at(30))
    mock_create.assert_awaited_once()
    assert "REQUEST SUBMITTED FOR MANUAL REVIEW" in result["action_context"]


def test_unparseable_policy_falls_back_to_existing_manual_review_behavior_unchanged():
    """Regression guard: when the window can't be determined, behavior is
    exactly what it was before this fix - blanket escalate, never guess."""
    result, mock_create = _run_cancel(
        "cancel my order", _created_at(30), policy_text="Please contact us about cancellations.",
    )
    mock_create.assert_awaited_once()
    assert "REQUEST SUBMITTED FOR MANUAL REVIEW" in result["action_context"]


# ── Isolation: policy text is fetched per-brand, never shared ─────────────

def test_policy_lookup_is_scoped_to_the_requesting_brand():
    integration = ReturnActionsIntegration()
    captured_brand_ids = []

    async def fake_policy_text(brand_id, policy_notes=None):
        captured_brand_ids.append(brand_id)
        return POLICY_2H

    intent = IntentResult(action_type="cancel", order_id="1234", raw_address=None, confidence=0.9)
    with patch.object(integration.actions, "check_return_eligibility",
                       new=AsyncMock(return_value=_eligible_unfulfilled_order(_created_at(30)))), \
         patch.object(integration.actions, "get_custom_policy_text", side_effect=fake_policy_text), \
         patch.object(integration, "_find_active_action", new=AsyncMock(return_value=None)), \
         patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True, "action_id": "a1"})):
        run(integration.handle_return_intent(
            query="cancel my order", customer_info={"name": "Jane", "email": "c@example.com"},
            existing_tool_results={}, tenant_id="tenant-A", brand_id="brand-A", intent_result=intent,
        ))

    assert captured_brand_ids == ["brand-A"]
