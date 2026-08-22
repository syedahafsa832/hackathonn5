-- chat_feedback existed live in Supabase with no migration file (only its
-- RLS lockdown was ever recorded, in 028_lockdown_anon_authenticated_access.sql).
-- v2_chat_widget.py's submit_feedback() only ever wrote session_id/rating/
-- created_at, so merchants had no tenant-scoped way to see their own
-- customers' feedback. This migration documents the live columns and adds
-- what Phase 2 needs to associate feedback with a brand/ticket and to carry
-- optional written comments.
--
-- Not applied to production by this change - see handoff notes.

CREATE TABLE IF NOT EXISTS chat_feedback (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id VARCHAR(255) NOT NULL,
    rating VARCHAR(20) NOT NULL, -- 'positive' | 'negative'
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE chat_feedback ADD COLUMN IF NOT EXISTS ticket_id UUID REFERENCES tickets(id) ON DELETE SET NULL;
ALTER TABLE chat_feedback ADD COLUMN IF NOT EXISTS brand_id UUID REFERENCES brands(id) ON DELETE CASCADE;
ALTER TABLE chat_feedback ADD COLUMN IF NOT EXISTS feedback_text TEXT;

CREATE INDEX IF NOT EXISTS idx_chat_feedback_brand ON chat_feedback(brand_id);
CREATE INDEX IF NOT EXISTS idx_chat_feedback_ticket ON chat_feedback(ticket_id);
CREATE INDEX IF NOT EXISTS idx_chat_feedback_created ON chat_feedback(created_at DESC);

-- RLS lockdown (idempotent - 028 already applies this same pattern to this
-- table; repeated here so this migration is self-sufficient if run standalone).
ALTER TABLE chat_feedback ENABLE ROW LEVEL SECURITY;
REVOKE SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON chat_feedback FROM anon;
REVOKE SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON chat_feedback FROM authenticated;
