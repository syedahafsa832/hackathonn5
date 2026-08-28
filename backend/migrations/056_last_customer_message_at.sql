-- Conversations ordering was sorting by tickets.updated_at, which changes on
-- ANY write to the row (AI draft generation, status changes, internal
-- action updates, and even unrelated bookkeeping - update_tickets_updated_at,
-- see schema.sql, resets it on every UPDATE regardless of which column
-- changed). That made old conversations resurface at the top of the
-- Conversations list whenever anything internal touched them, with no new
-- customer message involved.
--
-- last_customer_message_at is written ONLY by message_processor.py at the
-- moment a genuine inbound customer message is persisted (STAGE 1.5 thread
-- continuation, STAGE 1.8 new ticket) - never by AI processing, draft
-- creation/edits, status changes, or any other ticket update. Conversations
-- ordering uses this column first, falling back to updated_at only for rows
-- where it's null.
--
-- Backfill limitation (documented, not fabricated): this system has never
-- captured Gmail's actual internalDate/received timestamp historically -
-- every existing message's own "received_at" field is a processing-time
-- timestamp (when this backend handled it), not when Gmail actually
-- received it. The backfill below uses the most recent inbound message's
-- own received_at where the messages array has one, otherwise falls back to
-- the ticket's created_at (itself tied to the arrival of the first customer
-- message by the existing "create ticket immediately" design) - both are
-- real, already-recorded values, never a fabricated timestamp.
--
-- NOT applied to production by this change - run via the project's usual
-- migration-apply step.

ALTER TABLE tickets ADD COLUMN IF NOT EXISTS last_customer_message_at TIMESTAMPTZ;

UPDATE tickets
SET last_customer_message_at = COALESCE(
  (
    SELECT (elem->>'received_at')::timestamptz
    FROM jsonb_array_elements(COALESCE(messages, '[]'::jsonb)) AS elem
    WHERE elem->>'direction' = 'inbound' AND elem->>'received_at' IS NOT NULL
    ORDER BY (elem->>'received_at')::timestamptz DESC
    LIMIT 1
  ),
  created_at
)
WHERE last_customer_message_at IS NULL;

COMMENT ON COLUMN tickets.last_customer_message_at IS
  'Timestamp of the most recent genuine inbound customer message for this '
  'conversation. Set only by message_processor.py when persisting an inbound '
  'message - never by AI processing, draft creation, status changes, or any '
  'other ticket update. Used as the primary Conversations ordering key '
  '(falls back to updated_at when null).';
