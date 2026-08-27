"""
Root cause: generate_learned_profile()'s eligibility gate treated "any
uploaded example present" as license to skip MIN_APPROVED_REPLIES_TO_LEARN
entirely (reply_style_service.py, pre-fix). Adding a single Uploaded Example
with 0 approved replies therefore generated and persisted a full
reply_style_profile. The GET /reply-style response then surfaced that
profile as `learned_profile`, and the Settings page showed "A learned
writing style is ready from your approved replies. You can switch to it
anytime." plus a working "Switch to Learned Style" button — directly next
to a "0 of 20 approved replies" counter computed from the *same* response's
`eligible_for_learning` field, which correctly said False the whole time.
The frontend simply never consulted `eligible_for_learning`; it gated the
banner on `learned_profile` truthiness alone.

Fix (two layers, since a stale profile can already exist in the DB from
before this fix, or via force=True):
1. reply_style_service.generate_learned_profile / regenerate_if_due: always
   gate on real approved-reply volume (count_eligible_approved_replies),
   never bypassed by uploaded examples.
2. reply_style_service.switch_to_learned: also re-checks eligibility, so the
   API rejects switching even if a `reply_style_profile` row already exists.
3. dashboard/src/pages/Settings.jsx: the "ready to switch" banner now
   requires `data.eligible_for_learning` in addition to `data.learned_profile`.

These tests cover the service-level contract (1) and (2), which the
frontend fix (3) depends on for a fresh brand. They also exercise the "stale
profile from before the fix" case, since that's exactly the state a brand
that already hit this bug is left in.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services import reply_style_service as svc  # noqa: E402

BRAND_A = "brand-A"
BRAND_B = "brand-B"


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _strip_eq(value):
    return value[3:] if value and value.startswith("eq.") else value


def _fake_select(brands=None, tickets_by_brand=None, examples_by_brand=None):
    brands = brands or {}
    tickets_by_brand = tickets_by_brand or {}
    examples_by_brand = examples_by_brand or {}

    def fn(table, params=None):
        params = params or {}
        if table == "brands":
            bid = _strip_eq(params.get("id") or "")
            return [brands[bid]] if bid in brands else []
        if table == "tickets":
            bid = _strip_eq(params.get("brand_id") or "")
            return tickets_by_brand.get(bid, [])
        if table == "reply_style_examples":
            bid = _strip_eq(params.get("brand_id") or "")
            return examples_by_brand.get(bid, [])
        return []
    return fn


def test_exact_reported_bug_is_fixed_generate_blocked_at_zero_approved_with_one_example():
    """0 approved replies + 1 uploaded example must NOT produce a profile."""
    brand = {"id": BRAND_A, "reply_style_use_uploaded_only": False, "reply_style_profile": None}
    examples = {BRAND_A: [{"id": "ex-1", "content": "Customer: hiii\n\nLuna's reply: helloooo whatsup honey?"}]}

    with patch("src.services.reply_style_service.supabase_select",
               side_effect=_fake_select(brands={BRAND_A: brand}, examples_by_brand=examples)), \
         patch("src.services.reply_style_service.supabase_update") as mock_update:
        result = run(svc.generate_learned_profile(BRAND_A))

    assert result["success"] is False
    assert "20" in result["error"]
    mock_update.assert_not_called()


def test_switch_to_learned_rejected_when_stale_profile_exists_but_not_eligible():
    """Simulates a brand already left with a stale profile from before the
    fix (0 approved replies, but reply_style_profile is populated). Switching
    must still be rejected — a profile row existing is not sufficient."""
    brand = {
        "id": BRAND_A,
        "reply_style_profile": {"tone": "casual (from a single stale example)"},
    }

    with patch("src.services.reply_style_service.supabase_select",
               side_effect=_fake_select(brands={BRAND_A: brand}, tickets_by_brand={BRAND_A: []})), \
         patch("src.services.reply_style_service.supabase_update") as mock_update:
        result = svc.switch_to_learned(BRAND_A)

    assert result["success"] is False
    assert "20" in result["error"]
    mock_update.assert_not_called()


def test_switch_to_learned_allowed_once_actually_eligible():
    tickets = [{"human_approved": True, "ai_reply": f"reply {i}", "updated_at": "2026-01-01"} for i in range(20)]
    brand = {"id": BRAND_A, "reply_style_profile": {"tone": "warm"}}

    with patch("src.services.reply_style_service.supabase_select",
               side_effect=_fake_select(brands={BRAND_A: brand}, tickets_by_brand={BRAND_A: tickets})), \
         patch("src.services.reply_style_service.supabase_update") as mock_update:
        result = svc.switch_to_learned(BRAND_A)

    assert result["success"] is True
    mock_update.assert_called_once()


def test_eligibility_is_per_brand_not_leaked_across_tenants():
    """Brand A is eligible (20 approved replies); Brand B (different
    tenant/brand) has 0 approved replies and only an uploaded example.
    Brand B's eligibility and generate_learned_profile result must be
    entirely unaffected by Brand A's data."""
    tickets_a = [{"human_approved": True, "ai_reply": f"reply {i}", "updated_at": "2026-01-01"} for i in range(20)]
    brands = {
        BRAND_A: {"id": BRAND_A, "reply_style_use_uploaded_only": False, "reply_style_profile": None},
        BRAND_B: {"id": BRAND_B, "reply_style_use_uploaded_only": False, "reply_style_profile": None},
    }
    examples_b = {BRAND_B: [{"id": "ex-b", "content": "Customer: hi\n\nLuna's reply: hello!"}]}

    select_fn = _fake_select(brands=brands, tickets_by_brand={BRAND_A: tickets_a}, examples_by_brand=examples_b)

    with patch("src.services.reply_style_service.supabase_select", side_effect=select_fn):
        assert svc.count_eligible_approved_replies(BRAND_A) == 20
        assert svc.count_eligible_approved_replies(BRAND_B) == 0

    with patch("src.services.reply_style_service.supabase_select", side_effect=select_fn), \
         patch("src.services.reply_style_service.supabase_update"):
        result_b = run(svc.generate_learned_profile(BRAND_B))

    assert result_b["success"] is False
    assert "20" in result_b["error"]
