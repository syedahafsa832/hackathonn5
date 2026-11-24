-- Migration 064: Free inbound email forwarding
-- Keeps the existing Gmail polling mode as the default for all current brands.
-- Forwarding-mode brands receive inbound mail through Cloudflare Email Routing
-- and continue to use their connected Gmail account only for outbound replies.

ALTER TABLE brands
  ADD COLUMN IF NOT EXISTS email_ingestion_mode TEXT NOT NULL DEFAULT 'gmail_polling',
  ADD COLUMN IF NOT EXISTS inbound_email_token TEXT,
  ADD COLUMN IF NOT EXISTS inbound_email_enabled BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS gmail_scope_mode TEXT NOT NULL DEFAULT 'full';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'brands_email_ingestion_mode_check'
  ) THEN
    ALTER TABLE brands
      ADD CONSTRAINT brands_email_ingestion_mode_check
      CHECK (email_ingestion_mode IN ('gmail_polling', 'forwarding'));
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'brands_gmail_scope_mode_check'
  ) THEN
    ALTER TABLE brands
      ADD CONSTRAINT brands_gmail_scope_mode_check
      CHECK (gmail_scope_mode IN ('full', 'send_only'));
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_brands_inbound_email_token
  ON brands(inbound_email_token)
  WHERE inbound_email_token IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_brands_forwarding_enabled
  ON brands(inbound_email_enabled, email_ingestion_mode)
  WHERE inbound_email_enabled = true;
