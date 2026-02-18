-- PostgreSQL schema for Customer Success AI Agent
-- Database: customer_success_db

-- Enable pgvector extension for vector similarity search
CREATE EXTENSION IF NOT EXISTS vector;

-- Table: customers
-- Stores unified customer records with personal information and account details
CREATE TABLE IF NOT EXISTS customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) NOT NULL UNIQUE,
    phone VARCHAR(50) DEFAULT NULL,
    name VARCHAR(255) NOT NULL,
    company VARCHAR(255) DEFAULT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

-- Index for fast customer lookup by email
CREATE INDEX idx_customers_email ON customers(email);

-- Index for WhatsApp customer lookup by phone
CREATE INDEX idx_customers_phone ON customers(phone) WHERE phone IS NOT NULL;

-- Table: customer_identifiers
-- Maps various identifiers (emails, phone numbers) to customer records for cross-channel recognition
CREATE TABLE IF NOT EXISTS customer_identifiers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    identifier_type VARCHAR(50) NOT NULL, -- 'email', 'phone', 'external_id'
    identifier_value VARCHAR(255) NOT NULL,
    is_primary BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Enforced uniqueness on (identifier_type, identifier_value) to prevent duplicates
    CONSTRAINT unique_identifier_per_type UNIQUE (identifier_type, identifier_value)
);

-- Index for identifier lookup
CREATE INDEX idx_customer_identifiers_type_value ON customer_identifiers(identifier_type, identifier_value);
CREATE INDEX idx_customer_identifiers_customer_id ON customer_identifiers(customer_id);

-- Table: conversations
-- Tracks multi-channel conversations linking related interactions across different communication channels
CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID NOT NULL REFERENCES customers(id),
    initial_channel VARCHAR(50) NOT NULL, -- 'email', 'whatsapp', 'web_form'
    status VARCHAR(50) NOT NULL DEFAULT 'open', -- 'open', 'closed', 'escalated', 'pending'
    sentiment_score NUMERIC(3,2), -- Between -1.0 and 1.0
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

-- Indexes for conversation queries
CREATE INDEX idx_conversations_customer_id ON conversations(customer_id);
CREATE INDEX idx_conversations_status ON conversations(status);
CREATE INDEX idx_conversations_customer_status ON conversations(customer_id, status);

-- Table: messages
-- Stores all individual messages with metadata about channel, timestamp, and content
CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    channel VARCHAR(50) NOT NULL, -- 'email', 'whatsapp', 'web_form'
    direction VARCHAR(50) NOT NULL, -- 'inbound', 'outbound'
    sender_identifier VARCHAR(255), -- Email or phone of sender
    content TEXT NOT NULL,
    delivery_status VARCHAR(50) DEFAULT 'pending', -- 'sent', 'delivered', 'failed', 'pending'
    sentiment_score NUMERIC(3,2), -- Between -1.0 and 1.0
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

-- Indexes for message queries
CREATE INDEX idx_messages_conversation_id ON messages(conversation_id);
CREATE INDEX idx_messages_conversation_created ON messages(conversation_id, created_at);
CREATE INDEX idx_messages_channel_direction ON messages(channel, direction);
CREATE INDEX idx_messages_sentiment_range ON messages(sentiment_score) WHERE sentiment_score IS NOT NULL;

-- Table: tickets
-- Manages support ticket lifecycle with status, priority, category, and resolution tracking
CREATE TABLE IF NOT EXISTS tickets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID NOT NULL REFERENCES customers(id),
    conversation_id UUID REFERENCES conversations(id), -- May be null for standalone tickets
    source_channel VARCHAR(50) NOT NULL CHECK (source_channel IN ('whatsapp', 'web_form')), -- 'whatsapp', 'web_form'
    category VARCHAR(100) NOT NULL, -- Category of support request
    priority VARCHAR(50) NOT NULL, -- 'low', 'medium', 'high', 'critical'
    status VARCHAR(50) NOT NULL DEFAULT 'open', -- 'open', 'in_progress', 'escalated', 'resolved', 'closed'
    subject VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    assigned_agent VARCHAR(255) DEFAULT NULL, -- Name of assigned human agent (if escalated)
    resolution_notes TEXT DEFAULT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    resolved_at TIMESTAMP WITH TIME ZONE DEFAULT NULL,
    escalation_reason VARCHAR(100) DEFAULT NULL -- Reason for escalation (if escalated)
);

-- Indexes for ticket queries
CREATE INDEX idx_tickets_customer_id ON tickets(customer_id);
CREATE INDEX idx_tickets_conversation_id ON tickets(conversation_id) WHERE conversation_id IS NOT NULL;
CREATE INDEX idx_tickets_status ON tickets(status);
CREATE INDEX idx_tickets_priority ON tickets(priority);
CREATE INDEX idx_tickets_category ON tickets(category);
CREATE INDEX idx_tickets_customer_status ON tickets(customer_id, status);
CREATE INDEX idx_tickets_category_priority ON tickets(category, priority);

-- Table: knowledge_base
-- Contains searchable product documentation with vector embeddings for similarity search
CREATE TABLE IF NOT EXISTS knowledge_base (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    category VARCHAR(100),
    tags TEXT[], -- Array of tags for filtering
    version INTEGER DEFAULT 1,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    embedding vector(384) -- Vector embedding for similarity search (using pgvector)
);

-- Indexes for knowledge base queries
CREATE INDEX idx_knowledge_base_embedding ON knowledge_base USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX idx_knowledge_base_category ON knowledge_base(category) WHERE is_active = TRUE;
CREATE INDEX idx_knowledge_base_active ON knowledge_base(is_active);

-- Table: channel_configs
-- Store configuration parameters for different communication channels
CREATE TABLE IF NOT EXISTS channel_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    channel_type VARCHAR(50) NOT NULL, -- 'email', 'whatsapp', 'web_form'
    config_key VARCHAR(100) NOT NULL, -- Configuration parameter name
    config_value TEXT NOT NULL, -- Configuration parameter value
    is_sensitive BOOLEAN DEFAULT FALSE, -- Whether config contains sensitive data
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Index for channel config lookup
CREATE INDEX idx_channel_configs_type_key ON channel_configs(channel_type, config_key);

-- Table: agent_metrics
-- Store operational metrics and performance data for the AI agent
CREATE TABLE IF NOT EXISTS agent_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    metric_type VARCHAR(100) NOT NULL, -- Type of metric (response_time, accuracy, etc.)
    metric_value NUMERIC, -- Value of the metric
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    channel VARCHAR(50) DEFAULT 'overall', -- Channel-specific metrics: 'email', 'whatsapp', 'web_form', 'overall'
    metadata JSONB DEFAULT '{}' -- Additional context for the metric
);

-- Indexes for metrics queries
CREATE INDEX idx_agent_metrics_type ON agent_metrics(metric_type);
CREATE INDEX idx_agent_metrics_channel ON agent_metrics(channel);
CREATE INDEX idx_agent_metrics_timestamp ON agent_metrics(timestamp);
CREATE INDEX idx_agent_metrics_type_channel ON agent_metrics(metric_type, channel);

-- Function to update the updated_at column
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Triggers to automatically update the updated_at column
CREATE TRIGGER update_customers_updated_at BEFORE UPDATE ON customers FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_conversations_updated_at BEFORE UPDATE ON conversations FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_tickets_updated_at BEFORE UPDATE ON tickets FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_knowledge_base_updated_at BEFORE UPDATE ON knowledge_base FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_channel_configs_updated_at BEFORE UPDATE ON channel_configs FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Insert initial channel configurations
INSERT INTO channel_configs (channel_type, config_key, config_value, is_sensitive)
VALUES
    ('whatsapp', 'enabled', 'true', FALSE),
    ('whatsapp', 'config', '{"format":"casual","emoji":true}', FALSE),
    ('whatsapp', 'max_response_length', '1600', FALSE),
    ('web_form', 'enabled', 'true', FALSE),
    ('web_form', 'config', '{"format":"semi-formal"}', FALSE),
    ('web_form', 'max_response_length', '1000', FALSE)
ON CONFLICT DO NOTHING;

-- Insert initial knowledge base articles (example)
INSERT INTO knowledge_base (title, content, category, tags, is_active)
VALUES
    ('Getting Started', 'Welcome to our service! This article will guide you through the initial setup process...', 'onboarding', ARRAY['setup', 'getting-started', 'tutorial'], TRUE),
    ('Account Management', 'Learn how to manage your account settings, update your profile, and control your privacy settings...', 'account', ARRAY['account', 'settings', 'profile'], TRUE),
    ('Troubleshooting Common Issues', 'Find solutions to common problems you might encounter while using our service...', 'support', ARRAY['troubleshooting', 'faq', 'common-issues'], TRUE)
ON CONFLICT DO NOTHING;