-- Subscription/trial/usage tracking. Additive only — no new tables, reuses `tenants`.
-- `plan` already exists (migration 021); trial + daily-usage columns added here.

ALTER TABLE tenants ADD COLUMN IF NOT EXISTS trial_start_at TIMESTAMPTZ;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS trial_end_at   TIMESTAMPTZ;

-- Lazy daily usage counters: usage_date is the day these counts apply to. Any
-- read/write path that finds usage_date != today resets counts to 0 for the new
-- day first — no cron job, no separate usage table, no expensive COUNT queries.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS usage_date                  DATE DEFAULT CURRENT_DATE;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS usage_tickets_today         INT  DEFAULT 0;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS usage_ai_replies_today      INT  DEFAULT 0;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS usage_emails_today          INT  DEFAULT 0;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS usage_shopify_actions_today INT  DEFAULT 0;

-- Backfill: existing tenants without trial dates (registered before this change)
-- keep their current `plan` untouched — only new registrations get plan='trial'.
