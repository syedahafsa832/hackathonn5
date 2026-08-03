-- Founding-price paid-plan slot tracking + monthly ticket/conversation usage.
-- Idempotent (safe to re-run).

-- Set once, the first time a tenant is activated onto a founding-priced
-- paid plan (starter/growth) — deliberately never cleared afterward, even
-- if the plan later lapses, so a claimed founding slot stays claimed. Scale
-- has no founding price, so activating Scale never sets this.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS founding_pricing_claimed_at TIMESTAMPTZ;

-- Monthly counterpart to the existing daily usage_tickets_today/usage_date
-- pair (migration 022) — used only by plans whose PLAN_LIMITS entry defines
-- tickets_per_month instead of tickets_per_day (currently: starter, capped
-- at 500 conversations/month per the landing page). usage_month stores the
-- first-of-month the counter covers; plan_service.py resets it lazily,
-- same pattern as the daily counter.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS usage_month TEXT;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS usage_tickets_this_month INTEGER NOT NULL DEFAULT 0;
