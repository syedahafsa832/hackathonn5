-- 018: Missing tables and column name fixes

-- ============================================
-- 1. CONVERSATION OVERRIDES (Human Takeover)
-- Missing from all migrations; code references it in admin.py takeover/release
-- and message_processor.py _check_thread_override
-- ============================================
CREATE TABLE IF NOT EXISTS conversation_overrides (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL,
    overridden_by TEXT,
    override_type TEXT DEFAULT 'human_takeover',
    active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_overrides_active ON conversation_overrides(active);
CREATE INDEX IF NOT EXISTS idx_overrides_convo ON conversation_overrides(conversation_id);

-- ============================================
-- 2. FIX tickets.tags column name mismatch
-- Migration 016 added column as tags_array but all code reads/writes tags
-- ============================================
DO $$
BEGIN
    -- Rename tags_array → tags if tags_array exists and tags does not
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'tickets' AND column_name = 'tags_array'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'tickets' AND column_name = 'tags'
    ) THEN
        ALTER TABLE tickets RENAME COLUMN tags_array TO tags;
    END IF;
END $$;

-- Ensure tags column exists (handles fresh DBs where 016 was never run)
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS tags TEXT[];

-- ============================================
-- 3. SEND TASKS TABLE (outbound email queue)
-- Referenced by /api/v1/settings/gmail/queue-status endpoint
-- CREATE TABLE is a no-op if the table exists; ADD COLUMN IF NOT EXISTS
-- ensures all columns are present even on pre-existing tables.
-- ============================================
CREATE TABLE IF NOT EXISTS send_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    to_email TEXT NOT NULL,
    status TEXT DEFAULT 'queued',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE send_tasks ADD COLUMN IF NOT EXISTS tenant_id UUID;
ALTER TABLE send_tasks ADD COLUMN IF NOT EXISTS brand_id UUID;
ALTER TABLE send_tasks ADD COLUMN IF NOT EXISTS ticket_id UUID;
ALTER TABLE send_tasks ADD COLUMN IF NOT EXISTS subject TEXT;
ALTER TABLE send_tasks ADD COLUMN IF NOT EXISTS body TEXT;
ALTER TABLE send_tasks ADD COLUMN IF NOT EXISTS error_message TEXT;
ALTER TABLE send_tasks ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ;
ALTER TABLE send_tasks ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_send_tasks_status ON send_tasks(status);
CREATE INDEX IF NOT EXISTS idx_send_tasks_tenant ON send_tasks(tenant_id);
CREATE INDEX IF NOT EXISTS idx_send_tasks_sent_at ON send_tasks(sent_at);

-- ============================================
-- 4. SEND LOG TABLE (per-email send history for rate limiting)
-- Referenced by /api/v1/settings/gmail/queue-status endpoint
-- ============================================
CREATE TABLE IF NOT EXISTS send_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status TEXT DEFAULT 'sent',
    sent_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE send_log ADD COLUMN IF NOT EXISTS tenant_id UUID;
ALTER TABLE send_log ADD COLUMN IF NOT EXISTS brand_id UUID;
ALTER TABLE send_log ADD COLUMN IF NOT EXISTS ticket_id UUID;
ALTER TABLE send_log ADD COLUMN IF NOT EXISTS to_email TEXT;
ALTER TABLE send_log ADD COLUMN IF NOT EXISTS subject TEXT;

CREATE INDEX IF NOT EXISTS idx_send_log_sent_at ON send_log(sent_at);
CREATE INDEX IF NOT EXISTS idx_send_log_tenant ON send_log(tenant_id);

-- ============================================
-- 5. Remaining missing columns on tickets
-- (gmail_thread_id, channel, messages, email_category, sender_type covered by 008+011)
-- ============================================
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS gmail_message_id TEXT;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS unread_count INTEGER DEFAULT 0;

-- ============================================
-- 6. FIX action_logs column mismatch
-- Code writes: timestamp, event, actor, tenant_id, error_code, error_message
-- DB has:      created_at, event_type, performed_by, brand_id NOT NULL, details
-- ============================================

-- action_logs table (from migration 004) already has:
-- tenant_id, action_id, event, actor, error_code, error_message, created_at
-- The only mismatch was code writing "timestamp" instead of relying on created_at DEFAULT.
-- That is fixed in code (actions_service.py, multi_brand_actions.py).
-- No schema change needed here.
