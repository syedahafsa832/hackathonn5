-- ai_conversations was defined in migration 006_supabase_auth_roles.sql but
-- was never actually created in production (confirmed by direct inspection
-- of the live database - zero rows in information_schema.tables for this
-- name, in any schema). Production code has depended on it ever since:
-- BrandMessageProcessor._log_conversation() (backend/src/workers/
-- brand_message_processor.py) writes one row per customer/AI message on
-- every email-channel reply, including this sprint's new tokens_used/
-- latency_ms usage instrumentation - all of it has been failing silently
-- (caught by an existing try/except) with no data ever persisted.
--
-- This migration re-creates exactly the schema migration 006 already
-- specified (IF NOT EXISTS everywhere, so it's a no-op if the table somehow
-- already exists by the time this runs) and adds the RLS/grant lockdown
-- pattern established since (mirrors ai_reply_events, migration 040) that
-- migration 006 predates - service_role only, no anon/authenticated access.
-- Purely additive: no existing table is altered, no data is at risk.

CREATE TABLE IF NOT EXISTS ai_conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    ticket_id UUID REFERENCES tickets(id) ON DELETE CASCADE,
    -- Conversation
    role VARCHAR(20) NOT NULL, -- user, assistant, system
    content TEXT NOT NULL,
    -- Metadata
    model VARCHAR(100),
    tokens_used INTEGER,
    latency_ms INTEGER,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ai_conv_brand ON ai_conversations(brand_id);
CREATE INDEX IF NOT EXISTS idx_ai_conv_ticket ON ai_conversations(ticket_id);

ALTER TABLE ai_conversations ENABLE ROW LEVEL SECURITY;
REVOKE SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON ai_conversations FROM anon;
REVOKE SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON ai_conversations FROM authenticated;
