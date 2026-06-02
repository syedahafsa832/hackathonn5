-- Multi-Brand Actions Schema
-- Run this migration to add multi-brand support

-- ============================================
-- 1. BRANDS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS brands (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    shopify_shop_name VARCHAR(100) NOT NULL UNIQUE,
    shopify_access_token TEXT NOT NULL,  -- Encrypted
    shopify_api_version VARCHAR(20) DEFAULT '2024-01',
    support_email VARCHAR(255) NOT NULL,
    sender_name VARCHAR(100),
    email_signature TEXT,
    logo_url TEXT,
    primary_color VARCHAR(7) DEFAULT '#000000',
    return_policy_days INTEGER DEFAULT 30,
    auto_approve_threshold DECIMAL(10, 2) DEFAULT 50.00,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Index for quick lookups
CREATE INDEX IF NOT EXISTS idx_brands_shop_name ON brands(shopify_shop_name);
CREATE INDEX IF NOT EXISTS idx_brands_active ON brands(is_active);

-- ============================================
-- 2. BRAND ACTIONS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS brand_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id UUID REFERENCES brands(id),
    ticket_id UUID,  -- Optional reference to tickets table
    action_type VARCHAR(50) NOT NULL,  -- refund, cancel_order, change_address
    status VARCHAR(50) NOT NULL DEFAULT 'pending',  -- pending, approved, executed, rejected, failed
    order_id VARCHAR(100),
    customer_email VARCHAR(255) NOT NULL,
    customer_name VARCHAR(255),
    confidence_score DECIMAL(3, 2),
    risk_level VARCHAR(20),  -- low, medium, high
    risk_factors JSONB DEFAULT '[]',
    extracted_data JSONB DEFAULT '{}',
    ai_reasoning TEXT,
    original_message TEXT,
    execution_result JSONB,
    rejection_reason TEXT,
    approved_by VARCHAR(100),
    approved_at TIMESTAMP WITH TIME ZONE,
    executed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_brand_actions_brand ON brand_actions(brand_id);
CREATE INDEX IF NOT EXISTS idx_brand_actions_status ON brand_actions(status);
CREATE INDEX IF NOT EXISTS idx_brand_actions_type ON brand_actions(action_type);
CREATE INDEX IF NOT EXISTS idx_brand_actions_customer ON brand_actions(customer_email);
CREATE INDEX IF NOT EXISTS idx_brand_actions_created ON brand_actions(created_at DESC);

-- ============================================
-- 3. ACTION LOGS TABLE (Audit Trail)
-- ============================================
CREATE TABLE IF NOT EXISTS action_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action_id UUID REFERENCES brand_actions(id),
    event VARCHAR(50) NOT NULL,  -- created, approved, rejected, executed, failed
    actor VARCHAR(100),
    details JSONB DEFAULT '{}',
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_action_logs_action ON action_logs(action_id);
CREATE INDEX IF NOT EXISTS idx_action_logs_timestamp ON action_logs(timestamp DESC);

-- ============================================
-- 4. BRAND APPROVERS TABLE (Optional)
-- ============================================
CREATE TABLE IF NOT EXISTS brand_approvers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id UUID REFERENCES brands(id),
    user_email VARCHAR(255) NOT NULL,
    user_name VARCHAR(255),
    role VARCHAR(50) DEFAULT 'approver',  -- approver, admin, viewer
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_brand_approvers_brand ON brand_approvers(brand_id);
CREATE INDEX IF NOT EXISTS idx_brand_approvers_email ON brand_approvers(user_email);

-- ============================================
-- 5. UPDATE EXISTING TABLES (Add brand_id)
-- ============================================

-- Add brand_id to tickets if not exists
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS brand_id UUID REFERENCES brands(id);
CREATE INDEX IF NOT EXISTS idx_tickets_brand ON tickets(brand_id);

-- Add brand_id to orders if not exists
ALTER TABLE orders ADD COLUMN IF NOT EXISTS brand_id UUID REFERENCES brands(id);
CREATE INDEX IF NOT EXISTS idx_orders_brand ON orders(brand_id);

-- Add brand_id to products if not exists
ALTER TABLE products ADD COLUMN IF NOT EXISTS brand_id UUID REFERENCES brands(id);
CREATE INDEX IF NOT EXISTS idx_products_brand ON products(brand_id);

-- ============================================
-- 6. ROW LEVEL SECURITY (Optional - for multi-tenant)
-- ============================================

-- Enable RLS on brand_actions
ALTER TABLE brand_actions ENABLE ROW LEVEL SECURITY;

-- Policy: Users can only see actions for brands they have access to
-- (Uncomment and customize based on your auth setup)
-- CREATE POLICY brand_actions_policy ON brand_actions
--     FOR ALL
--     USING (brand_id IN (
--         SELECT brand_id FROM brand_approvers
--         WHERE user_email = current_user
--     ));

-- ============================================
-- 7. FUNCTIONS
-- ============================================

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Trigger for brands
DROP TRIGGER IF EXISTS update_brands_updated_at ON brands;
CREATE TRIGGER update_brands_updated_at
    BEFORE UPDATE ON brands
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Trigger for brand_actions
DROP TRIGGER IF EXISTS update_brand_actions_updated_at ON brand_actions;
CREATE TRIGGER update_brand_actions_updated_at
    BEFORE UPDATE ON brand_actions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- 8. SAMPLE DATA (For testing)
-- ============================================
-- Uncomment to insert sample brand

-- INSERT INTO brands (name, shopify_shop_name, shopify_access_token, support_email, sender_name, email_signature, logo_url, primary_color)
-- VALUES (
--     'Aurelio & Finch',
--     'aurelio-finch-dev',
--     'shpat_xxxxx',  -- Replace with actual token
--     'support@aureliofinch.com',
--     'Luna from Aurelio & Finch',
--     '— Luna\nAurelio & Finch',
--     'https://example.com/logo.png',
--     '#1a1a1a'
-- );
