-- PendingAction Table for Human-in-the-Loop Approval Queue
-- Run this SQL in your Supabase SQL Editor to create the table

CREATE TABLE IF NOT EXISTS pending_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id TEXT NOT NULL,
    customer_email TEXT NOT NULL,
    customer_name TEXT,
    action_type TEXT NOT NULL CHECK (action_type IN ('Refund', 'Exchange')),
    ai_reasoning TEXT,
    risk_score TEXT NOT NULL CHECK (risk_score IN ('Low', 'Medium', 'High')),
    status TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN ('Pending', 'Approved', 'Rejected', 'Executed')),
    suggested_variant_id TEXT,
    original_payload JSONB,
    order_data JSONB,
    exchange_suggestion JSONB,
    rejection_note TEXT,
    approved_by TEXT,
    approved_at TIMESTAMPTZ,
    executed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for faster lookups
CREATE INDEX IF NOT EXISTS idx_pending_actions_status ON pending_actions(status);
CREATE INDEX IF NOT EXISTS idx_pending_actions_customer_email ON pending_actions(customer_email);
CREATE INDEX IF NOT EXISTS idx_pending_actions_created_at ON pending_actions(created_at DESC);

-- Enable RLS
ALTER TABLE pending_actions ENABLE ROW LEVEL SECURITY;

-- Allow service role full access
CREATE POLICY "Service role full access" ON pending_actions
    FOR ALL USING (true) WITH CHECK (true);
