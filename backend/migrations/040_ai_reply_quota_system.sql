-- Centralized AI-reply usage quota. Replaces the trial plan's unused daily
-- ai_replies_per_day cap (500/day, never the effective bottleneck since
-- tickets_per_day was unlimited) with a real lifetime cap enforced across
-- every customer-facing channel (Gmail, Chat Widget, future channels).

-- Lifetime (never-reset) counter — the fast O(1) check every AI generation
-- gates on before calling the model. Only meaningful while plan == 'trial';
-- stays 0 for every other plan today (see plan_service.py PLAN_LIMITS).
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS ai_replies_used_total INTEGER NOT NULL DEFAULT 0;

-- Append-only event ledger: the source of truth for analytics and abuse
-- investigation (which channel/conversation/customer each reply went to).
-- Mirrors financial_action_audit_log's (migration 027) RLS/grant convention
-- exactly — service_role-only, no UPDATE/DELETE/TRUNCATE from anyone,
-- including service_role itself.
CREATE TABLE IF NOT EXISTS ai_reply_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    brand_id UUID,
    -- 'gmail' | 'chat_widget' | future channels. No CHECK constraint so a
    -- new channel never needs its own migration to become loggable.
    channel TEXT NOT NULL,
    ticket_id UUID,
    -- Customer email, or the chat widget session_id when no email is known.
    customer_identifier TEXT,
    model_used TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ai_reply_events_tenant ON ai_reply_events (tenant_id);
CREATE INDEX IF NOT EXISTS idx_ai_reply_events_created_at ON ai_reply_events (created_at);

REVOKE UPDATE, DELETE, TRUNCATE ON ai_reply_events FROM service_role;
REVOKE UPDATE, DELETE, TRUNCATE ON ai_reply_events FROM authenticated;
REVOKE UPDATE, DELETE, TRUNCATE ON ai_reply_events FROM anon;

ALTER TABLE ai_reply_events ENABLE ROW LEVEL SECURITY;
REVOKE SELECT, INSERT ON ai_reply_events FROM anon;
REVOKE SELECT, INSERT ON ai_reply_events FROM authenticated;
