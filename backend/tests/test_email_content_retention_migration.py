"""Static regression checks for the 90-day email-content retention migration."""
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "migrations" / "066_email_content_retention.sql"


def test_retention_is_limited_to_completed_email_tickets():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "t.channel = 'email'" in sql
    assert "'resolved'" in sql
    assert "'auto_resolved'" in sql
    assert "'closed'" in sql
    assert "auto_resolved_review" in sql
    # Review-queue tickets must be excluded from the actual completion list.
    completion_block = sql.split("AND lower(COALESCE(t.status, '')) IN (", 1)[1].split(")", 1)[0]
    assert "auto_resolved_review" not in completion_block


def test_retention_never_purges_cases_with_unfinished_actions_or_sends():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "public.actions AS a" in sql
    assert "public.brand_actions AS ba" in sql
    assert "public.email_automation_pending AS ep" in sql
    assert "public.send_tasks AS st" in sql
    assert "NOT EXISTS" in sql


def test_retention_scrubs_related_email_content_and_keeps_minimal_audit():
    sql = MIGRATION.read_text(encoding="utf-8")
    for required in (
        "public.actions",
        "public.brand_actions",
        "public.action_logs",
        "public.financial_action_audit_log",
        "public.ai_reply_events",
        "public.chat_feedback",
        "public.email_automation_pending",
        "public.email_filter_log",
        "public.email_quarantine",
        "details = NULL",
        "error_message = NULL",
        "customer_identifier = NULL",
    ):
        assert required in sql


def test_retention_runs_on_a_schedule_without_purging_during_migration():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE EXTENSION IF NOT EXISTS pg_cron" in sql
    assert "'tresolv-email-content-retention'" in sql
    assert "'0 3 * * *'" in sql
    # The function is called by the cron command only, not by the migration
    # itself. A deployment must not immediately delete any existing tickets.
    scheduled_calls = sql.count("SELECT public.purge_expired_email_support_content();")
    assert scheduled_calls == 1  # scheduled command only
