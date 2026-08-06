"""
Quarantine re-insertion bug: Gmail's `after:` search operator is date-level,
not time-level, so the same message keeps reappearing in every poll for the
rest of that calendar day. _create_quarantine_record() had no dedup check
and didn't even store gmail_message_id, so a single low-confidence email
produced a fresh quarantine row on every ~15s poll cycle all day — confirmed
in production as one email generating 458 duplicate rows (plus similar
clusters for other senders, 801 duplicate rows total).
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.email_guardian_service import EmailGuardianService  # noqa: E402


def test_duplicate_gmail_message_id_returns_existing_record_without_reinserting():
    service = EmailGuardianService()
    email = {"id": "19fcfe5e8aa19b71", "sender_email": "test@example.com", "subject": "hi", "body": "hiiii"}

    def fake_select(table, params=None):
        assert table == "email_quarantine"
        assert params["gmail_message_id"] == "eq.19fcfe5e8aa19b71"
        return [{"id": "existing-quarantine-id"}]

    with patch("src.services.email_guardian_service.supabase_select", side_effect=fake_select), \
         patch("src.services.email_guardian_service.supabase_insert") as mock_insert:
        qid = service._create_quarantine_record("brand-1", email, "customer_support", 0.5)

    assert qid == "existing-quarantine-id"
    mock_insert.assert_not_called()


def test_new_gmail_message_id_inserts_and_stores_it():
    service = EmailGuardianService()
    email = {"id": "new-message-id", "sender_email": "test@example.com", "subject": "hi", "body": "hiiii"}
    inserted = []

    def fake_select(table, params=None):
        return []  # no existing quarantine record for this message

    def fake_insert(table, data):
        inserted.append(data)
        return {"id": "new-quarantine-id"}

    with patch("src.services.email_guardian_service.supabase_select", side_effect=fake_select), \
         patch("src.services.email_guardian_service.supabase_insert", side_effect=fake_insert):
        qid = service._create_quarantine_record("brand-1", email, "customer_support", 0.5)

    assert qid == "new-quarantine-id"
    assert len(inserted) == 1
    assert inserted[0]["gmail_message_id"] == "new-message-id"


def test_missing_gmail_message_id_still_inserts_without_crashing():
    """Legacy/malformed email dicts without an 'id' key shouldn't break
    quarantine — dedup is simply skipped for that one record."""
    service = EmailGuardianService()
    email = {"sender_email": "test@example.com", "subject": "hi", "body": "hiiii"}
    inserted = []

    def fake_insert(table, data):
        inserted.append(data)
        return {"id": "new-quarantine-id"}

    with patch("src.services.email_guardian_service.supabase_select") as mock_select, \
         patch("src.services.email_guardian_service.supabase_insert", side_effect=fake_insert):
        qid = service._create_quarantine_record("brand-1", email, "customer_support", 0.5)

    mock_select.assert_not_called()  # no id to dedup on — skips straight to insert
    assert qid == "new-quarantine-id"
    assert inserted[0]["gmail_message_id"] is None
