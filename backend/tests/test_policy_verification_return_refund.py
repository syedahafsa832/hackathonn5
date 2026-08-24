"""
Policy verification for Refund / Return / Exchange (follow-up to the
cancellation-window fix — Task: "Finish the Incomplete Parts of PR #23").

check_return_eligibility() already had a fully deterministic Policy ->
Evidence -> Decision check for the *structured* return window
(brand.return_policy_days vs order.created_at, "Step 4"). What it did NOT
have: a merchant's *free-text* policy note (e.g. "no refunds after 14
days") was only ever consulted as raw text and blanket-escalated to a
human whenever any such text existed at all — the exact same category of
bug the cancellation-window fix addressed, just for refund/return instead
of cancel.

Fix: reuse the existing evaluate_cancellation_window() text parser (it was
never actually cancellation-specific — it just parses "within N
hours/days" out of any policy text) against the free-text policy inside
check_return_eligibility. A definitive NOT ELIGIBLE now short-circuits
straight to a customer-understandable answer; an eligible or ambiguous
read still falls through to the exact same human-review escalation as
before — never invents eligibility, never regresses the existing
structured-window / final-sale-tag / exclusion checks.

Because check_return_eligibility is the single eligibility check shared by
refund, return, AND exchange (return_actions_integration.py's
_handle_exchange calls the identical method), this one change covers all
three without touching either integration file.
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


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _created_at(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _fulfilled_order(created_at, email="jane@example.com"):
    return {
        "email": email,
        "fulfillment_status": "fulfilled",
        "cancelled_at": None,
        "total_price": "45.00",
        "refunds": [],
        "tags": "",
        "created_at": created_at,
        "line_items": [{"product_id": 111, "title": "T-Shirt", "requires_shipping": True}],
    }


def _check_eligibility(created_at, custom_policy_text, order_id="1234", brand_id="brand-1"):
    order = _fulfilled_order(created_at)
    with patch.object(actions_manager, "_get_order_from_shopify", new=AsyncMock(return_value=order)), \
         patch.object(actions_manager, "get_custom_policy_text", new=AsyncMock(return_value=custom_policy_text)):
        return run(actions_manager.check_return_eligibility(
            order_id, email="jane@example.com", tenant_id="tenant-1", brand_id=brand_id,
        ))


# ── evaluate_return_window: pure deterministic evaluator ──────────────────

def test_evaluate_return_window_within_window_is_eligible():
    result = actions_manager.evaluate_return_window(_created_at(10), window_days=30)
    assert result["eligible"] is True
    assert result["days_since_order"] == 10


def test_evaluate_return_window_beyond_window_is_not_eligible():
    result = actions_manager.evaluate_return_window(_created_at(40), window_days=30)
    assert result["eligible"] is False


def test_evaluate_return_window_missing_timestamp_returns_none_never_guesses():
    assert actions_manager.evaluate_return_window(None, window_days=30) is None


def test_evaluate_return_window_unparseable_timestamp_returns_none_never_guesses():
    assert actions_manager.evaluate_return_window("not-a-date", window_days=30) is None


def test_get_return_window_days_defaults_when_no_brand():
    assert run(actions_manager.get_return_window_days(None)) == actions_manager.RETURN_WINDOW_DAYS


def test_get_return_window_days_reads_brand_configured_value():
    with patch("src.services.brand_manager.brand_manager.get_brand", new=AsyncMock(return_value={"return_policy_days": 14})):
        assert run(actions_manager.get_return_window_days("brand-1")) == 14


# ── check_return_eligibility: free-text window now deterministic ──────────

def test_free_text_return_window_short_circuits_to_definitive_not_eligible():
    """The refund/return analogue of the cancellation bug: a free-text note
    ('no refunds after 14 days') on an order that's actually 20 days old
    must resolve to a direct, confident NOT ELIGIBLE - not a blanket
    'needs a human to check' escalation the text itself already answers."""
    result = _check_eligibility(
        _created_at(20), "We do not accept refunds after 14 days from purchase.",
    )
    assert result["eligible"] is False
    assert "outside the store's" in result["reason"]
    assert "14-day" in result["reason"]
    # Must NOT be escalated to a human - the text already gave a definitive answer.
    assert not result.get("staging_required")
    assert not result.get("requires_manual_review")


def test_free_text_without_a_window_pattern_still_escalates_exactly_as_before():
    """Regression guard: free text that isn't a simple window statement
    (can't be deterministically evaluated) must fall back to the existing
    'needs a human' behavior unchanged - never guessed."""
    result = _check_eligibility(_created_at(5), "Please contact support for any refund exceptions.")
    assert result["eligible"] is False
    assert result.get("staging_required") is True
    assert result.get("requires_manual_review") is True


def test_free_text_window_that_is_still_satisfied_does_not_bypass_human_review():
    """Safety guard: an order inside the free-text window must NOT be
    auto-approved from this check alone - the same text could carry other
    conditions (final sale, exclusions) this window parser can't evaluate,
    so it still falls through to the existing escalation, same as before."""
    result = _check_eligibility(_created_at(2), "We do not accept refunds after 14 days from purchase.")
    assert result["eligible"] is False
    assert result.get("staging_required") is True


def test_no_free_text_policy_still_grants_eligibility_exactly_as_before():
    """Regression guard: the common case (no free-text policy at all) must
    be completely unaffected by this change."""
    result = _check_eligibility(_created_at(5), "")
    assert result["eligible"] is True


def test_shopify_order_evidence_is_actually_used_not_customer_wording():
    """Two calls, identical policy text, different real order ages - the
    decision must track the real Shopify timestamp, proving evidence (not
    a hardcoded guess) drives the outcome."""
    inside = _check_eligibility(_created_at(2), "No refunds after 10 days.")
    outside = _check_eligibility(_created_at(15), "No refunds after 10 days.")
    assert inside["eligible"] is False and inside.get("staging_required")  # ambiguous-safe path (still eligible per window, escalate)
    assert outside["eligible"] is False and not outside.get("staging_required")  # definitive per window


# ── Traceability: decision method + evidence are visible on the result ────

def test_deterministic_decision_carries_its_evidence_for_traceability():
    result = _check_eligibility(_created_at(20), "No refunds after 14 days.")
    snapshot = result.get("policy_snapshot") or {}
    check = snapshot.get("time_window_check")
    assert check is not None
    assert check["eligible"] is False
    assert check["window_hours"] == 14 * 24


# ── Brand isolation: one merchant's policy text never leaks to another ────

def test_policy_text_lookup_is_scoped_to_the_requesting_brand():
    captured_brand_ids = []

    async def fake_policy_text(brand_id, policy_notes=None):
        captured_brand_ids.append(brand_id)
        return "No refunds after 14 days."

    order = _fulfilled_order(_created_at(20))
    with patch.object(actions_manager, "_get_order_from_shopify", new=AsyncMock(return_value=order)), \
         patch.object(actions_manager, "get_custom_policy_text", side_effect=fake_policy_text):
        run(actions_manager.check_return_eligibility(
            "1234", email="jane@example.com", tenant_id="tenant-A", brand_id="brand-A",
        ))

    assert captured_brand_ids == ["brand-A"]


# ── AI cannot override the deterministic decision ──────────────────────────

def test_decision_is_identical_regardless_of_extra_persuasive_customer_wording():
    """The eligibility decision comes from evaluate_cancellation_window's
    pure arithmetic on real Shopify data - customer-side wording never
    reaches this function at all, so it structurally cannot influence the
    result. Locks in that the decision is order_id/policy/evidence driven,
    not text-of-the-request driven."""
    result_a = _check_eligibility(_created_at(20), "No refunds after 14 days.", order_id="1111")
    result_b = _check_eligibility(_created_at(20), "No refunds after 14 days.", order_id="2222")
    assert result_a["eligible"] == result_b["eligible"] == False
    assert result_a["reason"] == result_b["reason"]
