-- Manual-activation upgrade requests (items 3 + 7): a customer submits the
-- "Request Upgrade" form, it lands here as 'pending', and the platform admin
-- activates it by hand from /admin after confirming payment out-of-band
-- (bank transfer) — no Stripe/real checkout involved.

CREATE TABLE IF NOT EXISTS upgrade_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    brand TEXT,
    requested_plan TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'activated', 'rejected')),
    activated_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_upgrade_requests_status ON upgrade_requests (status);
CREATE INDEX IF NOT EXISTS idx_upgrade_requests_tenant ON upgrade_requests (tenant_id);

-- Same lockdown pattern as every other table in this project (028): only
-- the backend's service_role should ever touch this directly.
ALTER TABLE upgrade_requests ENABLE ROW LEVEL SECURITY;
REVOKE SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON upgrade_requests FROM anon;
REVOKE SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON upgrade_requests FROM authenticated;
