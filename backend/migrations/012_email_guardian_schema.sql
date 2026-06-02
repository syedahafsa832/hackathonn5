BEGIN;

-- 1. Extend email_filter_log with AI classification audit columns
ALTER TABLE email_filter_log
    ADD COLUMN IF NOT EXISTS ai_classification TEXT,
    ADD COLUMN IF NOT EXISTS ai_confidence     FLOAT;

-- 2. Add guardian settings to system_settings
ALTER TABLE system_settings
    ADD COLUMN IF NOT EXISTS support_only_mode    BOOLEAN DEFAULT true,
    ADD COLUMN IF NOT EXISTS confidence_threshold FLOAT   DEFAULT 0.75,
    ADD COLUMN IF NOT EXISTS auto_reply_enabled   BOOLEAN DEFAULT true;

-- 3. Quarantine queue
CREATE TABLE IF NOT EXISTS email_quarantine (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id          UUID        NOT NULL,
    sender_email      TEXT        NOT NULL,
    subject           TEXT,
    body_preview      TEXT,
    thread_id         TEXT,
    ai_classification TEXT,
    ai_confidence     FLOAT,
    status            TEXT        NOT NULL DEFAULT 'pending'
                                  CHECK (status IN ('pending','promoted','discarded','expired')),
    actioned_by       TEXT,
    actioned_at       TIMESTAMPTZ,
    expires_at        TIMESTAMPTZ NOT NULL DEFAULT (now() + INTERVAL '7 days'),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_email_quarantine_brand_status
    ON email_quarantine (brand_id, status, created_at DESC);

COMMIT;
