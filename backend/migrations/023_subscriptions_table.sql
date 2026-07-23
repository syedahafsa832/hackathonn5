-- Subscription architecture scaffolding for future Stripe integration.
-- No Stripe calls happen yet; plan_service.py does not read/write this table
-- yet either — tenants.plan remains the source of truth for entitlements
-- until Stripe is actually wired in. This migration only prepares storage so
-- that step is additive later instead of another schema change.

BEGIN;

CREATE TABLE IF NOT EXISTS subscriptions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,

    stripe_customer_id      TEXT,
    stripe_subscription_id  TEXT,

    plan                    TEXT NOT NULL DEFAULT 'free',
    status                  TEXT NOT NULL DEFAULT 'inactive'
                                CHECK (status IN ('trialing', 'active', 'past_due', 'canceled', 'inactive')),

    current_period_start   TIMESTAMPTZ,
    current_period_end     TIMESTAMPTZ,

    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One subscription row per tenant (a tenant can only have one active plan).
CREATE UNIQUE INDEX IF NOT EXISTS idx_subscriptions_tenant_id ON subscriptions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_stripe_customer_id ON subscriptions(stripe_customer_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_stripe_subscription_id ON subscriptions(stripe_subscription_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);

COMMIT;
