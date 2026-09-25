-- Migration 062: email_filter_log dedup by (brand_id, gmail_message_id)
--
-- Root cause: Gmail's `after:` search is date-level, not time-level, so a
-- message keeps reappearing in every ~15s poll cycle for the rest of that
-- calendar day. Messages that become a ticket are already deduped (see
-- tickets.processed_gmail_message_ids, migration 060), but messages that get
-- blocked or quarantined never do - email_filter_log had no column to even
-- check against, so every poll wrote a fresh audit row for the same message.
-- One brand alone: 1,022,843 rows across only 791 distinct threads.
--
-- This migration only adds a nullable column + a lookup index. It does not
-- touch tickets, ai_conversations, customers, or email_quarantine, and does
-- not alter any existing email_filter_log row or constraint.

BEGIN;

ALTER TABLE email_filter_log
    ADD COLUMN IF NOT EXISTS gmail_message_id TEXT;

-- Partial: only new rows carry this id, so there's no point indexing the
-- ~1.6M historical NULLs (that space is reclaimed anyway once 065's cleanup
-- prunes them).
CREATE INDEX IF NOT EXISTS idx_email_filter_log_brand_gmail_msg
    ON email_filter_log (brand_id, gmail_message_id)
    WHERE gmail_message_id IS NOT NULL;

COMMIT;
