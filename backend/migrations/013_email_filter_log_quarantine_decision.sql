-- Allow 'quarantined' as a valid decision value in email_filter_log
ALTER TABLE email_filter_log
    DROP CONSTRAINT IF EXISTS email_filter_log_decision_check;

ALTER TABLE email_filter_log
    ADD CONSTRAINT email_filter_log_decision_check
    CHECK (decision IN ('allowed', 'blocked', 'quarantined'));
