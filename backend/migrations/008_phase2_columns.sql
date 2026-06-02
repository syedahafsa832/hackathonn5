-- Phase 2: Add email threading, channel, and email tracking columns to tickets
-- Run this in your Supabase SQL editor

ALTER TABLE tickets ADD COLUMN IF NOT EXISTS gmail_thread_id  VARCHAR(255);
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS detected_order_id VARCHAR(50);
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS channel           VARCHAR(50) DEFAULT 'email';
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS messages          JSONB DEFAULT '[]'::jsonb;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS email_sent        BOOLEAN DEFAULT false;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS email_sent_at     TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_tickets_gmail_thread_id  ON tickets(gmail_thread_id)  WHERE gmail_thread_id  IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tickets_detected_order_id ON tickets(detected_order_id) WHERE detected_order_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tickets_channel           ON tickets(channel);
