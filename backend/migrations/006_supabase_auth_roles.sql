-- ============================================
-- SUPABASE AUTH + ROLES + MULTI-TENANT UPGRADE
-- Run this in Supabase SQL Editor
-- ============================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================
-- 1. ORGANIZATIONS TABLE (Multi-tenant root)
-- ============================================
-- Organizations are the billing/subscription unit
CREATE TABLE IF NOT EXISTS organizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(100) UNIQUE NOT NULL,
    plan VARCHAR(50) DEFAULT 'free', -- free, starter, professional, enterprise
    plan_limits JSONB DEFAULT '{"brands": 1, "users": 2, "tickets_per_month": 100}'::jsonb,
    billing_email VARCHAR(255),
    stripe_customer_id VARCHAR(255),
    stripe_subscription_id VARCHAR(255),
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_organizations_slug ON organizations(slug);
CREATE INDEX idx_organizations_stripe ON organizations(stripe_customer_id);

-- ============================================
-- 2. USERS TABLE (Supabase Auth integration)
-- ============================================
-- Links Supabase Auth users to our application
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supabase_auth_id UUID UNIQUE, -- Links to auth.users(id)
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL,
    full_name VARCHAR(255),
    avatar_url TEXT,
    role VARCHAR(50) NOT NULL DEFAULT 'agent', -- admin, agent, read_only
    permissions JSONB DEFAULT '[]'::jsonb, -- Fine-grained permissions
    is_active BOOLEAN DEFAULT true,
    last_login_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(organization_id, email)
);

CREATE INDEX idx_users_supabase_auth ON users(supabase_auth_id);
CREATE INDEX idx_users_org ON users(organization_id);
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_role ON users(role);

-- ============================================
-- 3. BRANDS TABLE (Multiple brands per org)
-- ============================================
-- Each organization can have multiple Shopify brands
CREATE TABLE IF NOT EXISTS brands (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(100) NOT NULL,
    -- Shopify integration
    shopify_domain VARCHAR(255),
    shopify_access_token TEXT, -- Encrypted
    shopify_shop_name VARCHAR(255),
    shopify_connected BOOLEAN DEFAULT false,
    shopify_scopes TEXT[],
    -- Support configuration
    support_email VARCHAR(255),
    support_phone VARCHAR(50),
    timezone VARCHAR(100) DEFAULT 'UTC',
    -- AI configuration
    ai_enabled BOOLEAN DEFAULT true,
    ai_auto_respond BOOLEAN DEFAULT false,
    ai_confidence_threshold DECIMAL(3,2) DEFAULT 0.75,
    ai_escalation_keywords TEXT[] DEFAULT ARRAY['manager', 'lawyer', 'legal', 'sue'],
    -- Branding
    logo_url TEXT,
    primary_color VARCHAR(20),
    -- Status
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(organization_id, slug)
);

CREATE INDEX idx_brands_org ON brands(organization_id);
CREATE INDEX idx_brands_shopify ON brands(shopify_domain);
CREATE INDEX idx_brands_support_email ON brands(support_email);

-- ============================================
-- 4. KNOWLEDGE BASE SOURCES (Per-brand RAG)
-- ============================================
-- Track uploaded knowledge base documents per brand
DROP TABLE IF EXISTS knowledge_base_sources CASCADE;
CREATE TABLE knowledge_base_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    source_type VARCHAR(50) NOT NULL DEFAULT 'text', -- text, url, file, shopify_sync
    file_url TEXT,
    original_filename VARCHAR(255),
    status VARCHAR(50) NOT NULL DEFAULT 'processing', -- processing, completed, failed
    chunk_count INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    error_message TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_kb_sources_brand ON knowledge_base_sources(brand_id);
CREATE INDEX idx_kb_sources_status ON knowledge_base_sources(status);

-- ============================================
-- 5. RAG CHUNKS TABLE (Per-brand embeddings)
-- ============================================
-- Store embeddings with brand isolation
DROP TABLE IF EXISTS rag_chunks CASCADE;
CREATE TABLE rag_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    source_id UUID REFERENCES knowledge_base_sources(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    embedding vector(1024), -- Mistral embed dimension
    metadata JSONB DEFAULT '{}'::jsonb,
    source_name VARCHAR(255),
    chunk_index INTEGER DEFAULT 0,
    token_count INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_rag_chunks_brand ON rag_chunks(brand_id);
CREATE INDEX idx_rag_chunks_source ON rag_chunks(source_id);
CREATE INDEX idx_rag_chunks_embedding ON rag_chunks USING hnsw (embedding vector_cosine_ops);

-- ============================================
-- 6. TICKETS TABLE (Support tickets per brand)
-- ============================================
DROP TABLE IF EXISTS tickets CASCADE;
CREATE TABLE tickets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    -- Customer info
    customer_email VARCHAR(255) NOT NULL,
    customer_name VARCHAR(255),
    customer_phone VARCHAR(50),
    -- Ticket content
    subject VARCHAR(500),
    message TEXT NOT NULL,
    channel VARCHAR(50) NOT NULL DEFAULT 'email', -- email, web_form, whatsapp, chat
    -- Status & assignment
    status VARCHAR(50) NOT NULL DEFAULT 'open', -- open, pending, ai_responded, human_responded, resolved, escalated, closed
    priority VARCHAR(20) DEFAULT 'normal', -- low, normal, high, urgent
    assigned_to UUID REFERENCES users(id),
    -- AI analysis
    ai_response TEXT,
    ai_confidence DECIMAL(3,2),
    ai_sentiment VARCHAR(20), -- positive, neutral, negative
    ai_sentiment_score DECIMAL(3,2),
    ai_intent VARCHAR(100),
    ai_suggested_actions JSONB DEFAULT '[]'::jsonb,
    ai_reasoning TEXT,
    ai_responded_at TIMESTAMP WITH TIME ZONE,
    -- Human review
    human_approved BOOLEAN,
    human_approved_by UUID REFERENCES users(id),
    human_approved_at TIMESTAMP WITH TIME ZONE,
    human_response TEXT,
    -- Response tracking
    response_sent BOOLEAN DEFAULT false,
    response_sent_at TIMESTAMP WITH TIME ZONE,
    response_method VARCHAR(50), -- email, sms, whatsapp
    -- Shopify context
    order_id VARCHAR(100),
    order_number VARCHAR(100),
    order_total DECIMAL(10,2),
    -- Metadata
    source_message_id VARCHAR(255), -- For deduplication
    thread_id VARCHAR(255),
    tags TEXT[] DEFAULT '{}',
    metadata JSONB DEFAULT '{}'::jsonb,
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_tickets_brand ON tickets(brand_id);
CREATE INDEX idx_tickets_status ON tickets(status);
CREATE INDEX idx_tickets_customer ON tickets(customer_email);
CREATE INDEX idx_tickets_created ON tickets(created_at DESC);
CREATE INDEX idx_tickets_assigned ON tickets(assigned_to);
CREATE INDEX idx_tickets_source_msg ON tickets(source_message_id);

-- ============================================
-- 7. ACTIONS TABLE (Action queue per brand)
-- ============================================
DROP TABLE IF EXISTS actions CASCADE;
CREATE TABLE actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    ticket_id UUID REFERENCES tickets(id) ON DELETE SET NULL,
    -- Action details
    action_type VARCHAR(50) NOT NULL, -- refund, cancel_order, change_address, discount, exchange
    status VARCHAR(50) NOT NULL DEFAULT 'pending', -- pending, approved, executed, rejected, failed
    -- Customer info
    customer_email VARCHAR(255) NOT NULL,
    customer_name VARCHAR(255),
    -- Order info
    order_id VARCHAR(100),
    order_number VARCHAR(100),
    order_total DECIMAL(10,2),
    -- Action specifics
    amount DECIMAL(10,2),
    reason TEXT,
    extracted_data JSONB DEFAULT '{}'::jsonb,
    original_message TEXT,
    -- AI analysis
    ai_confidence DECIMAL(3,2),
    ai_reasoning TEXT,
    -- Risk assessment
    risk_level VARCHAR(20) DEFAULT 'low', -- low, medium, high
    risk_factors TEXT[] DEFAULT '{}',
    risk_score DECIMAL(3,2),
    -- Approval workflow
    requires_approval BOOLEAN DEFAULT true,
    approved_by UUID REFERENCES users(id),
    approved_at TIMESTAMP WITH TIME ZONE,
    rejection_reason TEXT,
    rejected_by UUID REFERENCES users(id),
    rejected_at TIMESTAMP WITH TIME ZONE,
    -- Execution
    executed_at TIMESTAMP WITH TIME ZONE,
    execution_result JSONB,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_actions_brand ON actions(brand_id);
CREATE INDEX idx_actions_status ON actions(status);
CREATE INDEX idx_actions_ticket ON actions(ticket_id);
CREATE INDEX idx_actions_created ON actions(created_at DESC);
CREATE INDEX idx_actions_type ON actions(action_type);

-- ============================================
-- 8. ACTION LOGS TABLE (Audit trail)
-- ============================================
DROP TABLE IF EXISTS action_logs CASCADE;
CREATE TABLE action_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action_id UUID NOT NULL REFERENCES actions(id) ON DELETE CASCADE,
    brand_id UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL, -- created, approved, rejected, executed, failed, retried
    performed_by UUID REFERENCES users(id),
    details JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_action_logs_action ON action_logs(action_id);
CREATE INDEX idx_action_logs_brand ON action_logs(brand_id);

-- ============================================
-- 9. AI CONVERSATIONS TABLE (Chat history)
-- ============================================
CREATE TABLE IF NOT EXISTS ai_conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    ticket_id UUID REFERENCES tickets(id) ON DELETE CASCADE,
    -- Conversation
    role VARCHAR(20) NOT NULL, -- user, assistant, system
    content TEXT NOT NULL,
    -- Metadata
    model VARCHAR(100),
    tokens_used INTEGER,
    latency_ms INTEGER,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_ai_conv_brand ON ai_conversations(brand_id);
CREATE INDEX idx_ai_conv_ticket ON ai_conversations(ticket_id);

-- ============================================
-- 10. ANALYTICS EVENTS TABLE (Usage tracking)
-- ============================================
CREATE TABLE IF NOT EXISTS analytics_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    brand_id UUID REFERENCES brands(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id),
    event_type VARCHAR(100) NOT NULL,
    event_data JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_analytics_org ON analytics_events(organization_id);
CREATE INDEX idx_analytics_brand ON analytics_events(brand_id);
CREATE INDEX idx_analytics_type ON analytics_events(event_type);
CREATE INDEX idx_analytics_created ON analytics_events(created_at);

-- ============================================
-- 11. INVITATIONS TABLE (Team invites)
-- ============================================
CREATE TABLE IF NOT EXISTS invitations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL DEFAULT 'agent',
    invited_by UUID NOT NULL REFERENCES users(id),
    token VARCHAR(255) UNIQUE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    accepted_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_invitations_org ON invitations(organization_id);
CREATE INDEX idx_invitations_token ON invitations(token);
CREATE INDEX idx_invitations_email ON invitations(email);

-- ============================================
-- 12. RPC FUNCTIONS
-- ============================================

-- Function to search brand-specific RAG chunks
CREATE OR REPLACE FUNCTION match_brand_rag_chunks(
    p_brand_id UUID,
    query_embedding vector(1024),
    match_threshold FLOAT,
    match_count INT
) RETURNS TABLE (
    id UUID,
    content TEXT,
    metadata JSONB,
    source_name VARCHAR,
    similarity FLOAT
) LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT
        rag_chunks.id,
        rag_chunks.content,
        rag_chunks.metadata,
        rag_chunks.source_name,
        1 - (rag_chunks.embedding <=> query_embedding) AS similarity
    FROM rag_chunks
    WHERE rag_chunks.brand_id = p_brand_id
        AND 1 - (rag_chunks.embedding <=> query_embedding) > match_threshold
    ORDER BY rag_chunks.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- Function to get organization stats
CREATE OR REPLACE FUNCTION get_organization_stats(p_org_id UUID)
RETURNS TABLE (
    total_brands BIGINT,
    total_users BIGINT,
    total_tickets BIGINT,
    tickets_this_month BIGINT,
    ai_responses BIGINT,
    human_responses BIGINT,
    pending_actions BIGINT
) LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT
        (SELECT COUNT(*) FROM brands WHERE organization_id = p_org_id AND is_active = true),
        (SELECT COUNT(*) FROM users WHERE organization_id = p_org_id AND is_active = true),
        (SELECT COUNT(*) FROM tickets t JOIN brands b ON t.brand_id = b.id WHERE b.organization_id = p_org_id),
        (SELECT COUNT(*) FROM tickets t JOIN brands b ON t.brand_id = b.id
         WHERE b.organization_id = p_org_id AND t.created_at >= date_trunc('month', CURRENT_DATE)),
        (SELECT COUNT(*) FROM tickets t JOIN brands b ON t.brand_id = b.id
         WHERE b.organization_id = p_org_id AND t.ai_response IS NOT NULL),
        (SELECT COUNT(*) FROM tickets t JOIN brands b ON t.brand_id = b.id
         WHERE b.organization_id = p_org_id AND t.human_response IS NOT NULL),
        (SELECT COUNT(*) FROM actions a JOIN brands b ON a.brand_id = b.id
         WHERE b.organization_id = p_org_id AND a.status = 'pending');
END;
$$;

-- Function to get brand stats
CREATE OR REPLACE FUNCTION get_brand_stats(p_brand_id UUID)
RETURNS TABLE (
    total_tickets BIGINT,
    open_tickets BIGINT,
    ai_responded BIGINT,
    human_responded BIGINT,
    resolved BIGINT,
    escalated BIGINT,
    pending_actions BIGINT,
    approved_actions BIGINT,
    rejected_actions BIGINT,
    avg_ai_confidence DECIMAL
) LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT
        (SELECT COUNT(*) FROM tickets WHERE brand_id = p_brand_id),
        (SELECT COUNT(*) FROM tickets WHERE brand_id = p_brand_id AND status = 'open'),
        (SELECT COUNT(*) FROM tickets WHERE brand_id = p_brand_id AND status = 'ai_responded'),
        (SELECT COUNT(*) FROM tickets WHERE brand_id = p_brand_id AND status = 'human_responded'),
        (SELECT COUNT(*) FROM tickets WHERE brand_id = p_brand_id AND status = 'resolved'),
        (SELECT COUNT(*) FROM tickets WHERE brand_id = p_brand_id AND status = 'escalated'),
        (SELECT COUNT(*) FROM actions WHERE brand_id = p_brand_id AND status = 'pending'),
        (SELECT COUNT(*) FROM actions WHERE brand_id = p_brand_id AND status = 'approved'),
        (SELECT COUNT(*) FROM actions WHERE brand_id = p_brand_id AND status = 'rejected'),
        (SELECT AVG(ai_confidence) FROM tickets WHERE brand_id = p_brand_id AND ai_confidence IS NOT NULL);
END;
$$;

-- ============================================
-- 13. TRIGGERS FOR UPDATED_AT
-- ============================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply triggers
DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN SELECT unnest(ARRAY['organizations', 'users', 'brands', 'knowledge_base_sources', 'tickets', 'actions']) LOOP
        EXECUTE format('
            DROP TRIGGER IF EXISTS update_%s_updated_at ON %s;
            CREATE TRIGGER update_%s_updated_at
                BEFORE UPDATE ON %s
                FOR EACH ROW
                EXECUTE FUNCTION update_updated_at_column();
        ', t, t, t, t);
    END LOOP;
END $$;

-- ============================================
-- 14. ROW LEVEL SECURITY (RLS)
-- ============================================

-- Enable RLS on all tables
ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE brands ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_base_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE action_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytics_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE invitations ENABLE ROW LEVEL SECURITY;

-- RLS Policies for organizations
CREATE POLICY "Users can view their own organization"
    ON organizations FOR SELECT
    USING (id IN (SELECT organization_id FROM users WHERE supabase_auth_id = auth.uid()));

-- RLS Policies for users
CREATE POLICY "Users can view users in their organization"
    ON users FOR SELECT
    USING (organization_id IN (SELECT organization_id FROM users WHERE supabase_auth_id = auth.uid()));

CREATE POLICY "Admins can manage users in their organization"
    ON users FOR ALL
    USING (
        organization_id IN (
            SELECT organization_id FROM users
            WHERE supabase_auth_id = auth.uid() AND role = 'admin'
        )
    );

-- RLS Policies for brands
CREATE POLICY "Users can view brands in their organization"
    ON brands FOR SELECT
    USING (organization_id IN (SELECT organization_id FROM users WHERE supabase_auth_id = auth.uid()));

CREATE POLICY "Admins can manage brands in their organization"
    ON brands FOR ALL
    USING (
        organization_id IN (
            SELECT organization_id FROM users
            WHERE supabase_auth_id = auth.uid() AND role = 'admin'
        )
    );

-- RLS Policies for tickets
CREATE POLICY "Users can view tickets for their org brands"
    ON tickets FOR SELECT
    USING (brand_id IN (
        SELECT b.id FROM brands b
        JOIN users u ON b.organization_id = u.organization_id
        WHERE u.supabase_auth_id = auth.uid()
    ));

CREATE POLICY "Agents and admins can manage tickets"
    ON tickets FOR ALL
    USING (brand_id IN (
        SELECT b.id FROM brands b
        JOIN users u ON b.organization_id = u.organization_id
        WHERE u.supabase_auth_id = auth.uid() AND u.role IN ('admin', 'agent')
    ));

-- RLS Policies for actions
CREATE POLICY "Users can view actions for their org brands"
    ON actions FOR SELECT
    USING (brand_id IN (
        SELECT b.id FROM brands b
        JOIN users u ON b.organization_id = u.organization_id
        WHERE u.supabase_auth_id = auth.uid()
    ));

CREATE POLICY "Agents and admins can manage actions"
    ON actions FOR ALL
    USING (brand_id IN (
        SELECT b.id FROM brands b
        JOIN users u ON b.organization_id = u.organization_id
        WHERE u.supabase_auth_id = auth.uid() AND u.role IN ('admin', 'agent')
    ));

-- RLS Policies for knowledge base
CREATE POLICY "Users can view KB for their org brands"
    ON knowledge_base_sources FOR SELECT
    USING (brand_id IN (
        SELECT b.id FROM brands b
        JOIN users u ON b.organization_id = u.organization_id
        WHERE u.supabase_auth_id = auth.uid()
    ));

CREATE POLICY "Admins can manage KB"
    ON knowledge_base_sources FOR ALL
    USING (brand_id IN (
        SELECT b.id FROM brands b
        JOIN users u ON b.organization_id = u.organization_id
        WHERE u.supabase_auth_id = auth.uid() AND u.role = 'admin'
    ));

-- RLS for rag_chunks follows knowledge_base_sources
CREATE POLICY "Users can view RAG chunks for their org brands"
    ON rag_chunks FOR SELECT
    USING (brand_id IN (
        SELECT b.id FROM brands b
        JOIN users u ON b.organization_id = u.organization_id
        WHERE u.supabase_auth_id = auth.uid()
    ));

-- ============================================
-- 15. SERVICE ROLE BYPASS
-- ============================================
-- Allow service role to bypass RLS for backend operations
-- This is handled by Supabase automatically for service_role key

-- ============================================
-- MIGRATION COMPLETE
-- ============================================
-- Next steps:
-- 1. Run this migration in Supabase SQL Editor
-- 2. Update backend auth service to use Supabase Auth
-- 3. Update frontend to use Supabase Auth client
-- 4. Migrate existing tenants to organizations/users/brands
