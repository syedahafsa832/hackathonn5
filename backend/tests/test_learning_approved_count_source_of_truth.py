"""
"Learning shows 0 approved replies despite Review Luna's Work having many
Approved items" - two compounding bugs in reply_style_service.py's
_approved_reply_texts(), the sole source of truth
count_eligible_approved_replies() (Settings "Learning" section) and
get_training_readiness() (Training page) both read:

1. Queried tickets by brand_id, but tickets' real brand FK is store_id -
   brand_id is only a secondary alias present on some rows (same fact
   already established for tickets.py's own ownership checks). Confirmed
   live: every ticket for a real brand had brand_id=None. Matched zero
   rows for any real brand.
2. Only read human_response/ai_reply for the reply text, never ai_draft/
   ai_response - review_ai_reply() and list_review_queue() (tickets.py)
   both already treat all four as valid sources of "the Luna reply that
   was reviewed". Tickets whose reply lives in ai_draft (a real, common
   shape - confirmed live) were matched by the query but then silently
   dropped by `if text:` finding nothing.

Both are now fixed to match the exact scoping/fallback conventions
tickets.py's own Review Luna's Work endpoints already use - one
authoritative definition, not a second one.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services import reply_style_service as svc  # noqa: E402

BRAND_ID = "brand-1"


def _select(tickets):
    def fn(table, params=None):
        if table != "tickets":
            return []
        # Mirrors the real Postgres filter this query sends - only rows
        # matching store_id + the human_approved/human_response OR +
        # human_rejected != true are ever returned.
        wanted_store = (params or {}).get("store_id", "").removeprefix("eq.")
        return [
            t for t in tickets
            if t.get("store_id") == wanted_store
            and (t.get("human_approved") or t.get("human_response")) and not t.get("human_rejected")
        ]
    return fn


# ── 1. An approved review counts ─────────────────────────────────────────

def test_approved_reply_with_text_in_ai_draft_counts():
    """The exact reported bug: a ticket approved via Review Luna's Work
    whose reply lives in ai_draft (not ai_reply) must count."""
    tickets = [{"human_approved": True, "ai_reply": None, "ai_draft": "Real Luna reply", "store_id": BRAND_ID}]
    with patch("src.services.reply_style_service.supabase_select", side_effect=_select(tickets)):
        assert svc.count_eligible_approved_replies(BRAND_ID) == 1


def test_approved_reply_with_text_in_ai_response_counts():
    tickets = [{"human_approved": True, "ai_reply": None, "ai_draft": None, "ai_response": "Real Luna reply", "store_id": BRAND_ID}]
    with patch("src.services.reply_style_service.supabase_select", side_effect=_select(tickets)):
        assert svc.count_eligible_approved_replies(BRAND_ID) == 1


def test_approved_reply_with_no_text_anywhere_does_not_count():
    tickets = [{"human_approved": True, "ai_reply": None, "ai_draft": None, "ai_response": None, "store_id": BRAND_ID}]
    with patch("src.services.reply_style_service.supabase_select", side_effect=_select(tickets)):
        assert svc.count_eligible_approved_replies(BRAND_ID) == 0


# ── 2. Edited-and-approved counts (existing semantics, unaffected) ──────────

def test_edit_approve_reply_counts():
    tickets = [{"human_approved": True, "human_response": "edited text", "ai_reply": "original", "store_id": BRAND_ID}]
    with patch("src.services.reply_style_service.supabase_select", side_effect=_select(tickets)):
        assert svc.count_eligible_approved_replies(BRAND_ID) == 1


# ── 3/4. Rejected / needs-review never count ─────────────────────────────

def test_rejected_reply_does_not_count():
    tickets = [{"human_rejected": True, "ai_draft": "x", "store_id": BRAND_ID}]
    with patch("src.services.reply_style_service.supabase_select", side_effect=_select(tickets)):
        assert svc.count_eligible_approved_replies(BRAND_ID) == 0


def test_needs_review_reply_does_not_count():
    tickets = [{"ai_draft": "not yet reviewed", "store_id": BRAND_ID}]
    with patch("src.services.reply_style_service.supabase_select", side_effect=_select(tickets)):
        assert svc.count_eligible_approved_replies(BRAND_ID) == 0


# ── 5. A normal generated draft (untouched by a human) does not count ───────

def test_plain_ai_draft_never_reviewed_does_not_count():
    tickets = [{"human_approved": False, "human_response": None, "human_rejected": False, "ai_draft": "auto-generated", "store_id": BRAND_ID}]
    with patch("src.services.reply_style_service.supabase_select", side_effect=_select(tickets)):
        assert svc.count_eligible_approved_replies(BRAND_ID) == 0


# ── 7/8. Tenant/brand isolation ──────────────────────────────────────────

def test_count_is_scoped_to_the_requested_brand_via_store_id():
    captured = {}

    def fake_select(table, params=None):
        captured["params"] = params
        return []

    with patch("src.services.reply_style_service.supabase_select", side_effect=fake_select):
        svc.count_eligible_approved_replies(BRAND_ID)

    assert captured["params"]["store_id"] == f"eq.{BRAND_ID}"
    assert "brand_id" not in captured["params"]


def test_another_brands_approved_replies_never_leak_into_this_count():
    tickets = [
        {"human_approved": True, "ai_draft": "brand-1's reply", "store_id": BRAND_ID},
        {"human_approved": True, "ai_draft": "brand-2's reply", "store_id": "brand-2"},
    ]
    with patch("src.services.reply_style_service.supabase_select", side_effect=_select(tickets)):
        assert svc.count_eligible_approved_replies(BRAND_ID) == 1


# ── 9/10. Readiness threshold (unchanged: 20, not a cap) ────────────────────

def test_twenty_qualifying_approvals_meet_the_existing_readiness_threshold():
    tickets = [{"human_approved": True, "ai_draft": f"reply {i}", "store_id": BRAND_ID} for i in range(20)]
    with patch("src.services.reply_style_service.supabase_select", side_effect=_select(tickets)):
        count = svc.count_eligible_approved_replies(BRAND_ID)
    assert count == 20
    assert count >= svc.MIN_APPROVED_REPLIES_TO_LEARN


def test_fewer_than_twenty_shows_the_real_count_and_is_not_ready():
    tickets = [{"human_approved": True, "ai_draft": f"reply {i}", "store_id": BRAND_ID} for i in range(7)]
    with patch("src.services.reply_style_service.supabase_select", side_effect=_select(tickets)):
        count = svc.count_eligible_approved_replies(BRAND_ID)
    assert count == 7
    assert count < svc.MIN_APPROVED_REPLIES_TO_LEARN
