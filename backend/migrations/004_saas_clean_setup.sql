-- ============================================
-- CLEAN SAAS SETUP - Run this in Supabase SQL Editor
-- ============================================

-- Drop existing tables if they exist (to start fresh)
DROP TABLE IF EXISTS sessions CASCADE;
DROP TABLE IF EXISTS action_logs CASCADE;
DROP TABLE IF EXISTS actions CASCADE;
DROP TABLE IF EXISTS tenants CASCADE;

-- ============================================
-- 1. TENANTS TABLE
-- ============================================
CREATE TABLE tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    company_name VARCHAR(255),
    shopify_domain VARCHAR(255),
    shopify_access_token TEXT,
    shopify_api_version VARCHAR(20) DEFAULT '2024-01',
    shopify_connected BOOLEAN DEFAULT false,
    shopify_shop_name VARCHAR(255),
    shopify_plan VARCHAR(100),
    support_email VARCHAR(255),
    auto_approve_threshold DECIMAL(10, 2) DEFAULT 0,
    timezone VARCHAR(50) DEFAULT 'UTC',
    is_active BOOLEAN DEFAULT true,
    email_verified BOOLEAN DEFAULT false,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_login_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_tenants_email ON tenants(email);

-- ============================================
-- 2. SESSIONS TABLE (for refresh tokens)
-- ============================================
CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    refresh_token_hash VARCHAR(255) NOT NULL,
    user_agent TEXT,
    ip_address VARCHAR(50),
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_sessions_tenant ON sessions(tenant_id);
CREATE INDEX idx_sessions_token ON sessions(refresh_token_hash);

-- ============================================
-- 3. ACTIONS TABLE
-- ============================================
CREATE TABLE actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    action_type VARCHAR(50) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    order_id VARCHAR(100),
    order_number VARCHAR(50),
    order_total DECIMAL(10, 2),
    customer_email VARCHAR(255) NOT NULL,
    customer_name VARCHAR(255),
    confidence DECIMAL(3, 2),
    risk_level VARCHAR(20) DEFAULT 'medium',
    risk_factors JSONB DEFAULT '[]',
    ai_reasoning TEXT,
    original_message TEXT,
    extracted_data JSONB DEFAULT '{}',
    execution_result JSONB,
    error_message TEXT,
    approved_by VARCHAR(100),
    rejection_reason TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    approved_at TIMESTAMP WITH TIME ZONE,
    executed_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_actions_tenant ON actions(tenant_id);
CREATE INDEX idx_actions_status ON actions(status);
CREATE INDEX idx_actions_tenant_status ON actions(tenant_id, status);

-- ============================================
-- 4. ACTION_LOGS TABLE
-- ============================================
CREATE TABLE action_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    action_id UUID REFERENCES actions(id) ON DELETE CASCADE,
    event VARCHAR(50) NOT NULL,
    actor VARCHAR(100),
    details JSONB DEFAULT '{}',
    error_code VARCHAR(50),
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_action_logs_tenant ON action_logs(tenant_id);
CREATE INDEX idx_action_logs_action ON action_logs(action_id);

-- ============================================
-- 5. UPDATE TRIGGER
-- ============================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_tenants_updated_at
    BEFORE UPDATE ON tenants
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_actions_updated_at
    BEFORE UPDATE ON actions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- DONE! Tables created successfully.
-- ============================================
