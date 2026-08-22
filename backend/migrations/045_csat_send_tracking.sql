-- Formalizes csat_sent (previously live-only, guarded only by a bare
-- `except: pass` in email_poller.py) and adds csat_sent_at, needed for the
-- per-customer 30-day CSAT cooldown. resolved_at already exists
-- (migrations/016_features.sql) and needs no change - it's just finally
-- populated by the automated paths as part of this change.
--
-- Not applied to production by this change - see handoff notes.

ALTER TABLE tickets ADD COLUMN IF NOT EXISTS csat_sent BOOLEAN DEFAULT false;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS csat_sent_at TIMESTAMP WITH TIME ZONE;

CREATE INDEX IF NOT EXISTS idx_tickets_csat_sent_at ON tickets(csat_sent_at);
CREATE INDEX IF NOT EXISTS idx_tickets_resolved_at ON tickets(resolved_at);
