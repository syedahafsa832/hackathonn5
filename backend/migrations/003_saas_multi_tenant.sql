-- ============================================
-- MULTI-TENANT SAAS MIGRATION
-- ============================================
-- This migration transforms the system into a proper multi-tenant SaaS product
-- Run this in Supabase SQL Editor

-- ============================================
-- 1. TENANTS TABLE (Core multi-tenant entity)
-- ============================================
CREATE TABLE IF NOT EXISTS tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Account Info
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    company_name VARCHAR(255),
    -- Shopify Connection (One store per tenant for MVP)
    shopify_domain VARCHAR(255),  -- e.g., "mystore.myshopify.com" or "mystore"
    shopify_access_token TEXT,    -- Encrypted
    shopify_api_version VARCHAR(20) DEFAULT '2024-01',
    shopify_connected BOOLEAN DEFAULT false,
    shopify_shop_name VARCHAR(255),  -- From Shopify API response
    shopify_plan VARCHAR(100),       -- From Shopify API response
    -- Settings
    support_email VARCHAR(255),
    auto_approve_threshold DECIMAL(10, 2) DEFAULT 0,  -- 0 = manual approval required
    timezone VARCHAR(50) DEFAULT 'UTC',
    -- Status
    is_active BOOLEAN DEFAULT true,
    email_verified BOOLEAN DEFAULT false,
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_login_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_tenants_email ON tenants(email);
CREATE INDEX IF NOT EXISTS idx_tenants_shopify_domain ON tenants(shopify_domain);
CREATE INDEX IF NOT EXISTS idx_tenants_active ON tenants(is_active);

-- ============================================
-- 2. ACTIONS TABLE (Simplified from brand_actions)
-- ============================================
CREATE TABLE IF NOT EXISTS actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    -- Action Details
    action_type VARCHAR(50) NOT NULL,  -- refund, cancel_order, change_address
    status VARCHAR(50) NOT NULL DEFAULT 'pending',  -- pending, approved, executed, rejected, failed
    -- Order Info
    order_id VARCHAR(100),
    order_number VARCHAR(50),
    order_total DECIMAL(10, 2),
    -- Customer Info
    customer_email VARCHAR(255) NOT NULL,
    customer_name VARCHAR(255),
    -- AI Detection
    confidence DECIMAL(3, 2),  -- 0.00 to 1.00
    risk_level VARCHAR(20) DEFAULT 'medium',  -- low, medium, high
    risk_factors JSONB DEFAULT '[]',
    ai_reasoning TEXT,
    -- Request Details
    original_message TEXT,
    extracted_data JSONB DEFAULT '{}',  -- amount, new_address, etc.
    -- Execution
    execution_result JSONB,
    error_message TEXT,
    -- Approval
    approved_by VARCHAR(100),
    rejection_reason TEXT,
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    approved_at TIMESTAMP WITH TIME ZONE,
    executed_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_actions_tenant ON actions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_actions_status ON actions(status);
CREATE INDEX IF NOT EXISTS idx_actions_type ON actions(action_type);
CREATE INDEX IF NOT EXISTS idx_actions_created ON actions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_actions_tenant_status ON actions(tenant_id, status);

-- ============================================
-- 3. ACTION_LOGS TABLE (Audit Trail)
-- ============================================
CREATE TABLE IF NOT EXISTS action_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    action_id UUID REFERENCES actions(id) ON DELETE CASCADE,
    -- Event Info
    event VARCHAR(50) NOT NULL,  -- created, approved, rejected, executed, failed, api_error
    actor VARCHAR(100),  -- email or 'system'
    -- Details
    details JSONB DEFAULT '{}',
    error_code VARCHAR(50),
    error_message TEXT,
    -- Timestamps
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_action_logs_tenant ON action_logs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_action_logs_action ON action_logs(action_id);
CREATE INDEX IF NOT EXISTS idx_action_logs_timestamp ON action_logs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_action_logs_event ON action_logs(event);

-- ============================================
-- 4. TICKETS TABLE (Support tickets with tenant isolation)
-- ============================================
-- Update existing tickets table or create new one
CREATE TABLE IF NOT EXISTS tickets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    -- Ticket Info
    subject VARCHAR(500),
    description TEXT,
    status VARCHAR(50) DEFAULT 'open',  -- open, pending, resolved, closed
    priority VARCHAR(20) DEFAULT 'normal',
    -- Customer Info
    customer_email VARCHAR(255),
    customer_name VARCHAR(255),
    -- Source
    source_channel VARCHAR(50) DEFAULT 'email',  -- email, webform, whatsapp
    source_message_id VARCHAR(255),
    -- Order Reference
    order_id VARCHAR(100),
    -- AI Analysis
    intent VARCHAR(100),
    sentiment VARCHAR(50),
    sentiment_score INTEGER,
    ai_response TEXT,
    ai_reasoning TEXT,
    -- Action Reference
    action_id UUID REFERENCES actions(id),
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_tickets_tenant ON tickets(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_customer ON tickets(customer_email);
CREATE INDEX IF NOT EXISTS idx_tickets_created ON tickets(created_at DESC);

-- ============================================
-- 5. SESSIONS TABLE (For JWT refresh tokens)
-- ============================================
CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    refresh_token_hash VARCHAR(255) NOT NULL,
    user_agent TEXT,
    ip_address VARCHAR(50),
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sessions_tenant ON sessions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(refresh_token_hash);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

-- ============================================
-- 6. AUTO-UPDATE TRIGGERS
-- ============================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

DROP TRIGGER IF EXISTS update_tenants_updated_at ON tenants;
CREATE TRIGGER update_tenants_updated_at
    BEFORE UPDATE ON tenants
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_actions_updated_at ON actions;
CREATE TRIGGER update_actions_updated_at
    BEFORE UPDATE ON actions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_tickets_updated_at ON tickets;
CREATE TRIGGER update_tickets_updated_at
    BEFORE UPDATE ON tickets
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- 7. ROW LEVEL SECURITY (Optional but recommended)
-- ============================================
-- Enable RLS on tenant-scoped tables
ALTER TABLE actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE action_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;

-- Note: RLS policies should be created based on your Supabase auth setup
-- For now, we enforce tenant isolation at the application level

-- ============================================
-- 8. CLEANUP OLD TABLES (Optional)
-- ============================================
-- Uncomment to drop old tables after migrating data
-- DROP TABLE IF EXISTS brands CASCADE;
-- DROP TABLE IF EXISTS brand_actions CASCADE;
-- DROP TABLE IF EXISTS brand_approvers CASCADE;

-- ============================================
-- 9. USEFUL VIEWS
-- ============================================
CREATE OR REPLACE VIEW pending_actions_view AS
SELECT
    a.id,
    a.tenant_id,
    a.action_type,
    a.status,
    a.order_id,
    a.order_number,
    a.order_total,
    a.customer_email,
    a.customer_name,
    a.confidence,
    a.risk_level,
    a.ai_reasoning,
    a.created_at,
    t.company_name,
    t.shopify_domain
FROM actions a
JOIN tenants t ON a.tenant_id = t.id
WHERE a.status = 'pending'
ORDER BY a.created_at DESC;

CREATE OR REPLACE VIEW action_history_view AS
SELECT
    a.id,
    a.tenant_id,
    a.action_type,
    a.status,
    a.order_id,
    a.order_number,
    a.order_total,
    a.customer_email,
    a.customer_name,
    a.approved_by,
    a.rejection_reason,
    a.execution_result,
    a.error_message,
    a.created_at,
    a.approved_at,
    a.executed_at
FROM actions a
WHERE a.status IN ('executed', 'rejected', 'failed')
ORDER BY COALESCE(a.executed_at, a.approved_at, a.created_at) DESC;
