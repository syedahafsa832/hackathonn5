"""Static regression checks for versioned email AI-processing consent."""
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "migrations" / "066_email_ai_processing_consent.sql"


def test_consent_migration_adds_version_and_timestamp_without_backfilling_consent():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "email_ai_processing_consent_at timestamptz" in sql
    assert "email_ai_processing_consent_version text" in sql
    # Existing merchants are paused; the migration must not invent consent.
    assert "UPDATE public.brands" in sql
    assert "email_ai_processing_consent_version IS DISTINCT FROM '2026-11-28-v1'" in sql
    assert "SET inbound_email_enabled = false" in sql
    assert "gmail_token = NULL" in sql
    assert "gmail_connected = false" in sql
    assert "gmail_email = NULL" in sql
    assert "SET email_ai_processing_consent_at" not in sql


def test_consent_is_checked_by_inbound_processor_before_ticket_and_ai_path():
    source = (BACKEND / "src/services/email_forwarding_service.py").read_text(encoding="utf-8")
    consent_guard = source.index("email_ai_processing_consent_required")
    ticket_lookup = source.index('"tickets"', source.index("async def process_forwarded_email"))
    assert consent_guard < ticket_lookup


def test_activation_requires_current_consent_and_consent_endpoint_records_it():
    source = (BACKEND / "src/api/routes/brand_gmail.py").read_text(encoding="utf-8")
    assert 'EMAIL_AI_PROCESSING_CONSENT_VERSION = "2026-11-28-v1"' in source
    assert 'email-forwarding/consent' in source
    assert 'email_ai_processing_consent_at' in source
    assert 'email_ai_processing_consent_version' in source
    assert 'Review and accept the email AI-processing consent' in source
