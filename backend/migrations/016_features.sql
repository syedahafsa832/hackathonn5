-- Feature additions: sentiment, first_response_at, canned_responses
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS customer_sentiment VARCHAR(20) DEFAULT 'neutral';
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS first_response_at TIMESTAMPTZ;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS tags_array TEXT[];

CREATE TABLE IF NOT EXISTS canned_responses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    title VARCHAR(100) NOT NULL,
    trigger_keywords TEXT NOT NULL,
    response_text TEXT NOT NULL,
    use_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_canned_responses_tenant ON canned_responses(tenant_id);
