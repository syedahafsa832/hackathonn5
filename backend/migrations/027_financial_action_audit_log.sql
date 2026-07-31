-- H2: append-only audit log for financial actions (refund / cancel-order
-- execution), and the backing store for idempotency-key replay so a
-- retried or duplicated request returns the original result instead of
-- executing the same Shopify action twice.

CREATE TABLE IF NOT EXISTS financial_action_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_id UUID NOT NULL,
    tenant_id UUID,
    brand_id UUID,
    action_type TEXT NOT NULL CHECK (action_type IN ('cancel_order', 'refund')),
    idempotency_key TEXT,
    user_id TEXT,
    user_email TEXT,
    ip_address TEXT,
    status TEXT NOT NULL CHECK (status IN ('success', 'failed')),
    result JSONB,
    error_detail TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per (ticket, action_type, idempotency_key) — the uniqueness this
-- index enforces IS the idempotency guarantee: a concurrent duplicate insert
-- for the same key fails at the database level, not just in application code.
CREATE UNIQUE INDEX IF NOT EXISTS idx_financial_audit_idempotency
    ON financial_action_audit_log (ticket_id, action_type, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_financial_audit_ticket ON financial_action_audit_log (ticket_id);
CREATE INDEX IF NOT EXISTS idx_financial_audit_tenant ON financial_action_audit_log (tenant_id);
CREATE INDEX IF NOT EXISTS idx_financial_audit_created_at ON financial_action_audit_log (created_at);

-- Append-only, enforced at the database level (not just "the app only calls
-- insert"): the backend connects as service_role, so this REVOKE genuinely
-- blocks UPDATE/DELETE even from the app itself, not just from end users.
-- TRUNCATE is a separate privilege from DELETE and would also let someone
-- wipe the table wholesale, so it's revoked alongside UPDATE/DELETE.
REVOKE UPDATE, DELETE, TRUNCATE ON financial_action_audit_log FROM service_role;
REVOKE UPDATE, DELETE, TRUNCATE ON financial_action_audit_log FROM authenticated;
REVOKE UPDATE, DELETE, TRUNCATE ON financial_action_audit_log FROM anon;

-- This table holds sensitive data (IPs, user emails tied to financial
-- actions) — only the backend (service_role) should ever touch it. Default
-- public-schema grants would otherwise let anon/authenticated read/write it
-- directly via PostgREST/GraphQL.
ALTER TABLE financial_action_audit_log ENABLE ROW LEVEL SECURITY;
REVOKE SELECT, INSERT ON financial_action_audit_log FROM anon;
REVOKE SELECT, INSERT ON financial_action_audit_log FROM authenticated;
