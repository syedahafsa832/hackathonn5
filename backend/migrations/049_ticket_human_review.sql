-- "Review Luna's Work" needs a Rejected outcome for a ticket's AI reply,
-- which had no representation before this: human_approved/human_response
-- (migration 035) can express Approved/Edited but there was no column for
-- "a human looked at this reply and rejected it". Added as its own explicit
-- fields (mirroring the human_approved_by/at shape) rather than overloading
-- human_approved=false, which is indistinguishable from "not reviewed yet".
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS human_rejected BOOLEAN DEFAULT false;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS human_rejected_by UUID;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS human_rejected_at TIMESTAMPTZ;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS human_rejected_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_tickets_human_rejected_at ON tickets(human_rejected_at) WHERE human_rejected_at IS NOT NULL;
