-- P0 fix: Gmail thread-continuation replies were never recorded as
-- processed anywhere email_poller.py's dedup check could see. That check
-- only matched tickets.gmail_message_id, which is set ONCE at ticket
-- creation (message_processor.py's STAGE 1.8) to the very first message's
-- id. A same-thread reply appended later (STAGE 1.5) never got its own
-- gmail_message_id recorded against the ticket, so on every subsequent
-- poll cycle within the same day (Gmail's `after:` search is date-granular,
-- not incremental) the exact same reply was re-fetched, passed the dedup
-- check again, and was reprocessed from scratch — re-running the AI and
-- re-sending a duplicate reply/action roughly every 15s for the rest of
-- that day.
--
-- Fix: track every processed inbound gmail_message_id per ticket (the
-- creating message plus every thread-continuation reply), not just the
-- first one, so the dedup check can catch a reply too.
--
-- NOT applied to production by this change - run via the project's usual
-- migration-apply step.

ALTER TABLE tickets ADD COLUMN IF NOT EXISTS processed_gmail_message_ids TEXT[] NOT NULL DEFAULT '{}';

UPDATE tickets
SET processed_gmail_message_ids = ARRAY[gmail_message_id]
WHERE gmail_message_id IS NOT NULL
  AND processed_gmail_message_ids = '{}';

CREATE INDEX IF NOT EXISTS idx_tickets_processed_gmail_message_ids
  ON tickets USING GIN (processed_gmail_message_ids);

COMMENT ON COLUMN tickets.processed_gmail_message_ids IS
  'Every inbound Gmail message id already folded into this ticket (the '
  'ticket-creating message plus any thread-continuation replies). Checked '
  'by email_poller.py before reprocessing a message - the legacy '
  'gmail_message_id column alone only ever reflected the ticket-creating '
  'message, not later replies in the same thread.';
