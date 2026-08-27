"""
Learning counter semantics (Settings page "X approved replies / 20 needed
to unlock Learned Style" copy): 20 is a minimum to unlock, never a ceiling,
rejected replies must never count, and an edited reply must be counted
using the human's actual edited wording, not the AI's original draft.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services import reply_style_service as svc  # noqa: E402

BRAND_ID = "brand-1"


def _tickets_select(tickets):
    def fn(table, params=None):
        if table == "tickets":
            return tickets
        return []
    return fn


def test_counting_is_not_capped_at_the_20_unlock_threshold():
    """20 is the unlock minimum, not a maximum - the counter must keep
    rising past it."""
    tickets = [{"human_approved": True, "ai_reply": f"reply {i}"} for i in range(46)]
    with patch("src.services.reply_style_service.supabase_select", side_effect=_tickets_select(tickets)):
        count = svc.count_eligible_approved_replies(BRAND_ID)
    assert count == 46


def test_rejected_only_ticket_is_never_counted():
    """A ticket that was only rejected (human_rejected=True, no
    human_approved/human_response) must not contribute to the counter -
    rejected replies are never a positive style example."""
    tickets = [
        {"human_approved": True, "ai_reply": "counts"},
        {"human_rejected": True, "ai_reply": "must not count"},
    ]

    def fake_select(table, params=None):
        if table != "tickets":
            return []
        # Mirrors the real query's own filter: only rows matching
        # human_approved.is.true OR human_response.not.is.null are ever
        # returned - a rejected-only row has neither, so a correct filter
        # implementation would never hand it back here.
        return [t for t in tickets if t.get("human_approved") or t.get("human_response")]

    with patch("src.services.reply_style_service.supabase_select", side_effect=fake_select):
        texts = svc._approved_reply_texts(BRAND_ID, limit=1000)
        count = svc.count_eligible_approved_replies(BRAND_ID)

    assert texts == ["counts"]
    assert count == 1


def test_edited_and_approved_reply_is_learned_from_the_humans_own_wording():
    """Edit & Approve is 'especially valuable' per spec - it must feed the
    human's actual edited text into learning, never the AI's original draft
    the human corrected."""
    tickets = [{
        "human_approved": True,
        "human_response": "The human's corrected wording",
        "ai_reply": "Luna's original (overridden) draft",
    }]
    with patch("src.services.reply_style_service.supabase_select", side_effect=_tickets_select(tickets)):
        texts = svc._approved_reply_texts(BRAND_ID)

    assert texts == ["The human's corrected wording"]
    assert "overridden" not in texts[0]


def test_rejected_ticket_is_excluded_even_if_it_was_also_approved():
    """Hard invariant, not an incidental consequence of current UI flow: a
    ticket carrying human_rejected=True must never count, even in the edge
    case where human_approved is also set (e.g. approved via one flow, then
    marked rejected via Review Luna's Work)."""
    captured = {}

    def fake_select(table, params=None):
        captured["params"] = params
        if table != "tickets":
            return []
        return []  # the real query filter is what's under test here, not the data

    with patch("src.services.reply_style_service.supabase_select", side_effect=fake_select):
        svc._approved_reply_texts(BRAND_ID)

    assert captured["params"]["human_rejected"] == "not.is.true"


def test_get_active_style_fills_missing_learned_fields_from_the_selected_preset():
    """Learned Style = preset baseline + refinement, never a full
    replacement - a learned profile missing/blank fields must fall back to
    the merchant's own selected preset, not a generic hardcoded default."""
    brand = {
        "reply_style_mode": "learned",
        "reply_style_preset": "playful",
        "reply_style_profile": {
            "tone": "casual and upbeat",  # only this field was confidently learned
            "greeting_style": None,
            "closing_style": "",
            # every other STYLE_PROFILE_KEYS field entirely absent
        },
    }
    style = svc.get_active_style(brand)

    playful_preset = svc.get_preset("playful").as_style_dict()
    assert style["tone"] == "casual and upbeat"  # learned value wins
    for key in ("greeting_style", "closing_style", "emoji_usage", "sentence_length",
                "paragraph_style", "use_bullets", "use_customer_name"):
        assert style[key] == playful_preset[key]  # falls back to the SELECTED preset, not a generic default


def test_get_active_style_learned_mode_with_fully_populated_profile_ignores_preset_fields():
    """Regression guard: when the learned profile confidently sets every
    field, the preset must never leak through and override it."""
    brand = {
        "reply_style_mode": "learned",
        "reply_style_preset": "professional",
        "reply_style_profile": {k: f"learned-{k}" for k in svc.STYLE_PROFILE_KEYS},
    }
    style = svc.get_active_style(brand)
    for key in svc.STYLE_PROFILE_KEYS:
        assert style[key] == f"learned-{key}"
