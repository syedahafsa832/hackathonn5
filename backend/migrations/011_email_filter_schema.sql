-- Migration 011: Email filter schema
-- Adds email_filter_log table and extends system_settings and tickets
-- Feature: 005-email-filter-loop

BEGIN;

-- 1. New email filter log table (append-only)
CREATE TABLE IF NOT EXISTS email_filter_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id        UUID NOT NULL,
    sender_email    TEXT NOT NULL,
    thread_id       TEXT,
    decision        TEXT NOT NULL CHECK (decision IN ('allowed', 'blocked')),
    filter_reason   TEXT,
    email_category  TEXT,
    sender_type     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_email_filter_log_brand_created
    ON email_filter_log (brand_id, created_at DESC);

-- 2. Extend system_settings with filter configuration columns
ALTER TABLE system_settings
    ADD COLUMN IF NOT EXISTS blocked_domains          JSONB   DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS whitelisted_domains      JSONB   DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS max_auto_replies         INTEGER DEFAULT 2,
    ADD COLUMN IF NOT EXISTS promotion_filter_enabled BOOLEAN DEFAULT true,
    ADD COLUMN IF NOT EXISTS loop_protection_enabled  BOOLEAN DEFAULT true;

-- 3. Extend tickets with per-ticket classification and loop tracking columns
ALTER TABLE tickets
    ADD COLUMN IF NOT EXISTS email_category   TEXT,
    ADD COLUMN IF NOT EXISTS sender_type      TEXT,
    ADD COLUMN IF NOT EXISTS loop_risk        BOOLEAN DEFAULT false,
    ADD COLUMN IF NOT EXISTS auto_reply_count INTEGER DEFAULT 0;

COMMIT;
