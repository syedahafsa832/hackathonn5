-- Custom Email Automation — merchant-configured confirmation emails for
-- support actions (cancellation, refund, exchange, address change).
--
-- email_automations: one merchant-authored template per brand+trigger.
-- trigger is intentionally restricted (via app-layer validation, mirrored
-- here as a CHECK) to the four action types actions_service.py can
-- actually report success/failure for — see email_automation_service.py.
--
-- email_automation_pending: a queued render awaiting merchant approval
-- when an automation has requires_approval=true. Never auto-sent; only
-- created from inside the existing post-execution-success hook, so it can
-- never represent a failed or still-pending action.
--
-- NOT applied to production by this change — create only, same convention
-- as prior migrations in this repo (e.g. 048_refund_autopilot.sql). Apply
-- explicitly via the project's usual migration-apply step.

CREATE TABLE IF NOT EXISTS email_automations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    name VARCHAR(120) NOT NULL,
    trigger VARCHAR(30) NOT NULL CHECK (trigger IN ('cancel_order', 'refund', 'exchange', 'change_address')),
    subject VARCHAR(255) NOT NULL,
    body TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT false,
    requires_approval BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- One template per brand+trigger keeps "which automation fires" unambiguous
-- (no ordering/priority logic needed) — editing means updating the existing
-- row, not creating a competing one.
CREATE UNIQUE INDEX IF NOT EXISTS idx_email_automations_brand_trigger
    ON email_automations(brand_id, trigger);

CREATE TABLE IF NOT EXISTS email_automation_pending (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    automation_id UUID NOT NULL REFERENCES email_automations(id) ON DELETE CASCADE,
    brand_id UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    action_id UUID REFERENCES actions(id) ON DELETE SET NULL,
    ticket_id UUID REFERENCES tickets(id) ON DELETE SET NULL,
    to_email VARCHAR(255) NOT NULL,
    subject VARCHAR(255) NOT NULL,
    body TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'dismissed')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_email_automations_brand ON email_automations(brand_id);
CREATE INDEX IF NOT EXISTS idx_email_automation_pending_brand ON email_automation_pending(brand_id);
CREATE INDEX IF NOT EXISTS idx_email_automation_pending_status ON email_automation_pending(brand_id, status);

-- RLS lockdown — same pattern as every other tenant-scoped table in this
-- repo (e.g. 043_chat_feedback.sql): the backend (service role) is the
-- only reader/writer, tenant scoping is enforced in application code via
-- _get_owned_brand(), never by anon/authenticated Postgres roles.
ALTER TABLE email_automations ENABLE ROW LEVEL SECURITY;
REVOKE SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON email_automations FROM anon;
REVOKE SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON email_automations FROM authenticated;

ALTER TABLE email_automation_pending ENABLE ROW LEVEL SECURITY;
REVOKE SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON email_automation_pending FROM anon;
REVOKE SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON email_automation_pending FROM authenticated;
