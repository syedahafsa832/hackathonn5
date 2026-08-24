-- Real, backend-driven processing activity for a ticket ("Real-Time AI
-- Employees" feature) — e.g. "Finding order #1013", "Shopify order found",
-- "Checking cancellation policy". Never fabricated: rows are written only
-- from the existing on_progress emission points already built into
-- customer_success_agent.process_customer_query() and
-- return_actions_integration.handle_return_intent() (previously only wired
-- to the live chat widget's streaming response, never persisted or
-- connected to the email pipeline), plus a few coarse milestones
-- (message received, draft ready, sent/escalated) logged directly by
-- message_processor.py. No LLM-generated text is ever stored here — every
-- stage/label is one of the fixed strings already hardcoded at each real
-- call site.
CREATE TABLE IF NOT EXISTS ticket_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_id UUID NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    brand_id UUID REFERENCES brands(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    label TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'done',
    detail TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ticket_events_ticket ON ticket_events(ticket_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ticket_events_brand ON ticket_events(brand_id);

-- Only the backend (service_role) writes/reads this table directly — same
-- convention as ai_conversations/reply_style_examples. Merchants see it
-- only through the existing tenant-scoped ticket API, never a direct table grant.
ALTER TABLE ticket_events ENABLE ROW LEVEL SECURITY;
REVOKE SELECT, INSERT, UPDATE, DELETE ON ticket_events FROM anon;
REVOKE SELECT, INSERT, UPDATE, DELETE ON ticket_events FROM authenticated;
