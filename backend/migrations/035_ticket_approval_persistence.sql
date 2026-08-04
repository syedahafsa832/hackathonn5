-- Fixes a live bug: approve-ai and send-reply write these columns on every
-- approval/send, but they never existed on the live tickets table, so the
-- PATCH raised and the merchant saw "Failed to approve AI response" even
-- though the email had already gone out. This also blocks Reply Style's
-- "20 approved human replies" learning source, which depends on
-- human_approved/human_response actually persisting.
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS human_approved BOOLEAN DEFAULT false;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS human_approved_by UUID;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS human_approved_at TIMESTAMPTZ;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS response_sent BOOLEAN DEFAULT false;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS response_sent_at TIMESTAMPTZ;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS response_method TEXT;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS human_response TEXT;

CREATE INDEX IF NOT EXISTS idx_tickets_human_approved_at ON tickets(human_approved_at) WHERE human_approved_at IS NOT NULL;
