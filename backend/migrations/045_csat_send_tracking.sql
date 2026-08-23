-- Formalizes csat_sent (previously live-only, guarded only by a bare
-- `except: pass` in email_poller.py) and adds csat_sent_at, needed for the
-- per-customer 30-day CSAT cooldown.
--
-- CORRECTION: this migration originally assumed resolved_at already existed
-- (citing migrations/016_features.sql), which was wrong - grep confirms
-- resolved_at is only ever defined inside CREATE TABLE IF NOT EXISTS tickets
-- (...) blocks (migrations/003_saas_multi_tenant.sql,
-- migrations/006_supabase_auth_roles.sql), both of which silently no-op
-- against the already-existing production tickets table. It was never
-- actually added via ALTER TABLE, so running this migration as originally
-- written fails with "column resolved_at does not exist" on the final
-- CREATE INDEX. Fixed by adding it explicitly, the same IF NOT EXISTS way
-- csat_sent/csat_sent_at already are here.

ALTER TABLE tickets ADD COLUMN IF NOT EXISTS csat_sent BOOLEAN DEFAULT false;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS csat_sent_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMP WITH TIME ZONE;

CREATE INDEX IF NOT EXISTS idx_tickets_csat_sent_at ON tickets(csat_sent_at);
CREATE INDEX IF NOT EXISTS idx_tickets_resolved_at ON tickets(resolved_at);
