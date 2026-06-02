-- 020: Production hardening — gmail_message_id dedup, messages default enforcement

-- Dedup column: store the Gmail message ID so the poller can skip re-processed messages
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS gmail_message_id TEXT;

CREATE INDEX IF NOT EXISTS idx_tickets_gmail_message_id
    ON tickets(gmail_message_id)
    WHERE gmail_message_id IS NOT NULL;

-- Ensure messages JSONB column always defaults to [] (safe re-application of 008 default)
ALTER TABLE tickets ALTER COLUMN messages SET DEFAULT '[]'::jsonb;

-- Backfill: replace any NULL messages rows with [] so JSON.parse never fails
UPDATE tickets SET messages = '[]'::jsonb WHERE messages IS NULL;
