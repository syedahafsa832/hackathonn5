-- 017: Stability fixes — gmail_connected, missing columns, backfills

-- Fix gmail_connected for brands that have tokens but flag is wrong
-- NOTE: column is gmail_token (not gmail_refresh_token)
UPDATE brands
SET gmail_connected = true, is_active = true
WHERE gmail_token IS NOT NULL
AND gmail_email IS NOT NULL
AND (gmail_connected = false OR gmail_connected IS NULL);

-- Add brand_id to actions so v2_actions endpoint can filter by brand (idempotent)
ALTER TABLE actions ADD COLUMN IF NOT EXISTS brand_id UUID;

-- Add missing columns to tickets (idempotent)
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS detected_order_number VARCHAR(20);
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS body TEXT;

-- Add missing columns to actions (idempotent)
ALTER TABLE actions ADD COLUMN IF NOT EXISTS order_number VARCHAR(20);
ALTER TABLE actions ADD COLUMN IF NOT EXISTS customer_email VARCHAR(255);
ALTER TABLE actions ADD COLUMN IF NOT EXISTS ai_reasoning TEXT;
ALTER TABLE actions ADD COLUMN IF NOT EXISTS executed_at TIMESTAMPTZ;
ALTER TABLE actions ADD COLUMN IF NOT EXISTS error_message TEXT;
ALTER TABLE actions ADD COLUMN IF NOT EXISTS shopify_response TEXT;

-- Backfill customer_email on actions from tickets
UPDATE actions a
SET customer_email = t.customer_email
FROM tickets t
WHERE a.ticket_id = t.id AND a.customer_email IS NULL;
