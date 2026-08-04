-- Reply Style: per-brand wording/tone personalization, separate from Identity
-- (agent_name, email_signature — already exist, untouched by this migration).
-- mode: 'preset' | 'learned' | 'disabled'. preset is a code-constant id
-- (see backend/src/agent/reply_style_presets.py), not a DB-stored preset.
-- profile/reasoning are only populated once mode = 'learned'.
ALTER TABLE brands ADD COLUMN IF NOT EXISTS reply_style_mode TEXT DEFAULT 'preset';
ALTER TABLE brands ADD COLUMN IF NOT EXISTS reply_style_preset TEXT DEFAULT 'warm_friendly';
ALTER TABLE brands ADD COLUMN IF NOT EXISTS reply_style_profile JSONB;
ALTER TABLE brands ADD COLUMN IF NOT EXISTS reply_style_reasoning TEXT;
ALTER TABLE brands ADD COLUMN IF NOT EXISTS reply_style_learn_automatically BOOLEAN DEFAULT true;
ALTER TABLE brands ADD COLUMN IF NOT EXISTS reply_style_use_uploaded_only BOOLEAN DEFAULT false;
ALTER TABLE brands ADD COLUMN IF NOT EXISTS reply_style_last_generated_at TIMESTAMPTZ;

-- Optional uploaded example replies (Step 7 — seed data for faster
-- personalization). Not part of onboarding; feeds the same profile
-- generation as approved replies when present.
CREATE TABLE IF NOT EXISTS reply_style_examples (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_reply_style_examples_brand_id ON reply_style_examples(brand_id);

-- Only the backend (service_role) touches this table, same convention as
-- financial_action_audit_log — service_role bypasses RLS by default.
ALTER TABLE reply_style_examples ENABLE ROW LEVEL SECURITY;
REVOKE SELECT, INSERT, UPDATE, DELETE ON reply_style_examples FROM anon;
REVOKE SELECT, INSERT, UPDATE, DELETE ON reply_style_examples FROM authenticated;
