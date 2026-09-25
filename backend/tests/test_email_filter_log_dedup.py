"""
email_filter_log write-amplification bug: both email_filter_service.log_decision()
and email_guardian_service.log_guardian_decision() inserted an audit row on EVERY
invocation, with zero memory of prior writes. Gmail's `after:` search is date-level,
not time-level, so a message that never becomes a ticket (i.e. anything blocked or
quarantined) keeps reappearing in every ~15s poll cycle for the rest of that
calendar day - and got a fresh, identical audit row every single time.

Live evidence: one brand alone accumulated 1,022,843 email_filter_log rows across
only 791 distinct threads (~1,300 duplicate rows per thread) - 444MB of a 500MB
database cap, almost entirely from this.

Fix (migration 062_email_filter_log_dedup.sql): added a nullable gmail_message_id
column, and both log functions now check for an existing (brand_id,
gmail_message_id) row before inserting - skipping the write entirely if one
already exists. Mirrors the exact pattern already used by
email_guardian_service._find_existing_decision() for the (separate)
re-classification bug.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.email_filter_service import EmailFilterService, FilterResult  # noqa: E402
from src.services.email_guardian_service import EmailGuardianService, GuardianResult  # noqa: E402


_FILTER_RESULT = FilterResult(decision="blocked", reason="blocked_domain", email_category="unknown", sender_type="automated")
_GUARDIAN_RESULT = GuardianResult(
    decision="quarantined", classification="customer_support", confidence=0.4,
    reason="low_confidence", quarantine_id="q-1", auto_reply_enabled=False,
)


# ── email_filter_service.log_decision ───────────────────────────────────────

def test_filter_log_decision_skips_insert_when_message_already_logged():
    svc = EmailFilterService()
    with patch("src.services.email_filter_service.supabase_select", return_value=[{"id": "row-1"}]) as mock_select, \
         patch("src.services.email_filter_service.supabase_insert") as mock_insert:
        svc.log_decision("brand-1", "someone@example.com", "thread-1", _FILTER_RESULT, gmail_message_id="msg-1")

    mock_select.assert_called_once_with("email_filter_log", {
        "brand_id": "eq.brand-1", "gmail_message_id": "eq.msg-1",
        "ai_classification": "is.null", "select": "id", "limit": "1",
    })
    mock_insert.assert_not_called()


def test_filter_log_decision_inserts_for_a_genuinely_new_message():
    svc = EmailFilterService()
    with patch("src.services.email_filter_service.supabase_select", return_value=[]), \
         patch("src.services.email_filter_service.supabase_insert") as mock_insert:
        svc.log_decision("brand-1", "someone@example.com", "thread-1", _FILTER_RESULT, gmail_message_id="msg-2")

    mock_insert.assert_called_once()
    assert mock_insert.call_args[0][1]["gmail_message_id"] == "msg-2"


def test_filter_log_decision_without_a_message_id_skips_the_dedup_lookup():
    """Callers that genuinely have no gmail_message_id (shouldn't happen from the
    poller, but keeps this function safe standalone) fall back to always inserting -
    never silently drops an audit row just because there's nothing to dedup on."""
    svc = EmailFilterService()
    with patch("src.services.email_filter_service.supabase_select") as mock_select, \
         patch("src.services.email_filter_service.supabase_insert") as mock_insert:
        svc.log_decision("brand-1", "someone@example.com", "thread-1", _FILTER_RESULT)

    mock_select.assert_not_called()
    mock_insert.assert_called_once()


# ── email_guardian_service.log_guardian_decision ────────────────────────────

def test_guardian_log_decision_skips_insert_when_message_already_logged():
    svc = EmailGuardianService()
    with patch("src.services.email_guardian_service.supabase_select", return_value=[{"id": "row-1"}]) as mock_select, \
         patch("src.services.email_guardian_service.supabase_insert") as mock_insert:
        svc.log_guardian_decision("brand-1", "someone@example.com", "thread-1", _GUARDIAN_RESULT, gmail_message_id="msg-1")

    mock_select.assert_called_once_with("email_filter_log", {
        "brand_id": "eq.brand-1", "gmail_message_id": "eq.msg-1",
        "ai_classification": "not.is.null", "select": "id", "limit": "1",
    })
    mock_insert.assert_not_called()


def test_guardian_log_decision_inserts_for_a_genuinely_new_message():
    svc = EmailGuardianService()
    with patch("src.services.email_guardian_service.supabase_select", return_value=[]), \
         patch("src.services.email_guardian_service.supabase_insert") as mock_insert:
        svc.log_guardian_decision("brand-1", "someone@example.com", "thread-1", _GUARDIAN_RESULT, gmail_message_id="msg-2")

    mock_insert.assert_called_once()
    assert mock_insert.call_args[0][1]["gmail_message_id"] == "msg-2"


# ── the actual guarantee: repeated polls of the same message never duplicate ─

def test_same_brand_and_message_id_cannot_produce_duplicate_rows_across_repeated_polls():
    """Simulates the real failure mode: the same (brand_id, gmail_message_id)
    goes through log_decision/log_guardian_decision multiple times, as it would
    across several ~15s poll cycles while Gmail keeps resurfacing it. A fake
    table backs supabase_select/supabase_insert so this proves the *combined*
    effect, not just one mocked call in isolation."""
    fake_table = []

    def fake_select(table, params):
        assert table == "email_filter_log"
        rows = [r for r in fake_table
                if r["brand_id"] == params["brand_id"].removeprefix("eq.")
                and r["gmail_message_id"] == params["gmail_message_id"].removeprefix("eq.")]
        classification_filter = params.get("ai_classification")
        if classification_filter == "is.null":
            rows = [r for r in rows if r.get("ai_classification") is None]
        elif classification_filter == "not.is.null":
            rows = [r for r in rows if r.get("ai_classification") is not None]
        return rows

    def fake_insert(table, data):
        assert table == "email_filter_log"
        fake_table.append(data)
        return data

    filter_svc = EmailFilterService()
    guardian_svc = EmailGuardianService()

    with patch("src.services.email_filter_service.supabase_select", side_effect=fake_select), \
         patch("src.services.email_filter_service.supabase_insert", side_effect=fake_insert), \
         patch("src.services.email_guardian_service.supabase_select", side_effect=fake_select), \
         patch("src.services.email_guardian_service.supabase_insert", side_effect=fake_insert):
        for _ in range(5):  # 5 simulated poll cycles surfacing the same message
            filter_svc.log_decision("brand-1", "someone@example.com", "thread-1", _FILTER_RESULT, gmail_message_id="msg-1")
            guardian_svc.log_guardian_decision("brand-1", "someone@example.com", "thread-1", _GUARDIAN_RESULT, gmail_message_id="msg-1")

    assert len(fake_table) == 2  # one from the filter layer, one from the guardian layer - never 10

    # A different message for the same brand still gets its own row (dedup is
    # scoped per-message, not a blanket "one row per brand ever").
    with patch("src.services.email_filter_service.supabase_select", side_effect=fake_select), \
         patch("src.services.email_filter_service.supabase_insert", side_effect=fake_insert):
        filter_svc.log_decision("brand-1", "someone@example.com", "thread-1", _FILTER_RESULT, gmail_message_id="msg-2")

    assert len(fake_table) == 3
