#!/bin/bash

echo "=========================================="
echo "SEEDING DATABASE WITH TEST DATA"
echo "=========================================="

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if services are running
echo -e "${YELLOW}Checking if services are running...${NC}"
if ! docker-compose ps | grep -q "Up"; then
    echo -e "${RED}ERROR: Services not running. Please run ./start_all.sh first${NC}"
    exit 1
fi

echo -e "${GREEN}Services are running!${NC}"
echo ""

# Seed knowledge base
echo -e "${YELLOW}Seeding knowledge base articles...${NC}"
docker exec -i customer-success-fte-postgres psql -U postgres -d fte_db << 'EOF'
-- Insert sample knowledge base articles
INSERT INTO knowledge_base (title, content, category, tags, is_active) VALUES
('Password Reset Guide', 'To reset your password: 1. Click "Forgot Password" on the login page. 2. Enter your email address. 3. Check your email for a reset link. 4. Click the link and create a new password. The link expires in 24 hours.', 'authentication', ARRAY['password', 'reset', 'login']),
('API Authentication', 'Our API uses OAuth2 for authentication. Generate an API key from Settings > Developer > API Keys. Include the key in the Authorization header: Authorization: Bearer YOUR_API_KEY', 'api', ARRAY['api', 'authentication', 'oauth', 'keys']),
('User Management', 'To add users: 1. Go to Settings > Users. 2. Click "Invite User". 3. Enter their email. 4. Select their role. 5. Click Send Invitation. Users will receive an email to complete registration.', 'users', ARRAY['users', 'management', 'roles', 'invitations']),
('Integrations', 'We support integrations with Slack, Microsoft Teams, Google Calendar, and GitHub. To set up: Go to Settings > Integrations, select your platform, and follow the OAuth flow.', 'integrations', ARRAY['integration', 'slack', 'microsoft', 'google', 'github']),
('Billing Information', 'View and manage your billing on the Billing page. You can update payment methods, view invoices, and manage subscriptions. For enterprise plans, contact sales.', 'billing', ARRAY['billing', 'payment', 'invoices', 'subscription']),
('Getting Started', 'Welcome to our platform! Start by completing your profile, then explore the dashboard. Check out our tutorials for step-by-step guides to common tasks.', 'onboarding', ARRAY['onboarding', 'getting started', 'tutorial', 'dashboard']);

-- Insert sample customers
INSERT INTO customers (email, phone, name, company, created_at, updated_at) VALUES
('john.doe@example.com', '+1234567890', 'John Doe', 'TechCorp', NOW(), NOW()),
('jane.smith@example.com', '+1987654321', 'Jane Smith', 'StartupXYZ', NOW(), NOW()),
('bob.wilson@example.com', '+1555123456', 'Bob Wilson', 'Enterprise Inc', NOW(), NOW()),
('alice.brown@example.com', '+1444987654', 'Alice Brown', 'Small Business Co', NOW(), NOW()),
('charlie.davis@example.com', '+13335557777', 'Charlie Davis', 'Consulting Ltd', NOW(), NOW());

-- Insert customer identifiers
INSERT INTO customer_identifiers (customer_id, identifier_type, identifier_value, is_primary)
SELECT id, 'email', email, true FROM customers;

INSERT INTO customer_identifiers (customer_id, identifier_type, identifier_value, is_primary)
SELECT id, 'phone', phone, true FROM customers WHERE phone IS NOT NULL;

-- Insert channel configs
INSERT INTO channel_configs (channel_type, config_key, config_value, is_sensitive) VALUES
('email', 'max_response_length', '2000', FALSE),
('whatsapp', 'max_response_length', '1600', FALSE),
('web_form', 'max_response_length', '1000', FALSE),
('email', 'response_format', 'formal', FALSE),
('whatsapp', 'response_format', 'concise', FALSE),
('web_form', 'response_format', 'semi_formal', FALSE);

-- Insert sample conversations
INSERT INTO conversations (customer_id, initial_channel, status, created_at, updated_at)
SELECT
    c.id,
    CASE
        WHEN RANDOM() < 0.33 THEN 'email'
        WHEN RANDOM() < 0.66 THEN 'whatsapp'
        ELSE 'web_form'
    END,
    CASE
        WHEN RANDOM() < 0.2 THEN 'closed'
        WHEN RANDOM() < 0.4 THEN 'in_progress'
        WHEN RANDOM() < 0.6 THEN 'escalated'
        ELSE 'open'
    END,
    NOW() - (RANDOM() * 30)::INTEGER * INTERVAL '1 day',
    NOW() - (RANDOM() * 30)::INTEGER * INTERVAL '1 day'
FROM customers c;

-- Insert sample tickets
INSERT INTO tickets (customer_id, conversation_id, source_channel, category, priority, status, subject, description, created_at, updated_at)
SELECT
    c.id,
    (SELECT id FROM conversations WHERE customer_id = c.id LIMIT 1),
    CASE
        WHEN RANDOM() < 0.33 THEN 'email'
        WHEN RANDOM() < 0.66 THEN 'whatsapp'
        ELSE 'web_form'
    END,
    CASE
        WHEN RANDOM() < 0.25 THEN 'technical'
        WHEN RANDOM() < 0.5 THEN 'billing'
        WHEN RANDOM() < 0.75 THEN 'general'
        ELSE 'sales'
    END,
    CASE
        WHEN RANDOM() < 0.3 THEN 'low'
        WHEN RANDOM() < 0.6 THEN 'medium'
        ELSE 'high'
    END,
    CASE
        WHEN RANDOM() < 0.2 THEN 'closed'
        WHEN RANDOM() < 0.4 THEN 'in_progress'
        WHEN RANDOM() < 0.6 THEN 'escalated'
        ELSE 'open'
    END,
    CASE
        WHEN RANDOM() < 0.25 THEN 'Password Reset Issue'
        WHEN RANDOM() < 0.5 THEN 'API Integration Question'
        WHEN RANDOM() < 0.75 THEN 'Billing Inquiry'
        ELSE 'Feature Request'
    END,
    CASE
        WHEN RANDOM() < 0.25 THEN 'Customer is having trouble resetting their password and needs assistance.'
        WHEN RANDOM() < 0.5 THEN 'Request for help with API integration and authentication.'
        WHEN RANDOM() < 0.75 THEN 'Question about billing and subscription options.'
        ELSE 'Request for new feature or enhancement to existing functionality.'
    END,
    NOW() - (RANDOM() * 30)::INTEGER * INTERVAL '1 day',
    NOW() - (RANDOM() * 30)::INTEGER * INTERVAL '1 day'
FROM customers c;

-- Insert sample messages
INSERT INTO messages (conversation_id, channel, direction, sender_identifier, content, delivery_status, created_at)
SELECT
    conv.id,
    conv.initial_channel,
    CASE WHEN RANDOM() < 0.5 THEN 'inbound' ELSE 'outbound' END,
    CASE
        WHEN conv.initial_channel = 'email' THEN c.email
        WHEN conv.initial_channel = 'whatsapp' THEN c.phone
        ELSE c.email
    END,
    CASE
        WHEN RANDOM() < 0.25 THEN 'Hello, I need help with resetting my password.'
        WHEN RANDOM() < 0.5 THEN 'I am having trouble integrating your API with our system.'
        WHEN RANDOM() < 0.75 THEN 'Can you provide information about your billing options?'
        ELSE 'I would like to request a new feature for the platform.'
    END,
    CASE
        WHEN RANDOM() < 0.1 THEN 'failed'
        WHEN RANDOM() < 0.3 THEN 'pending'
        ELSE 'delivered'
    END,
    NOW() - (RANDOM() * 30)::INTEGER * INTERVAL '1 day'
FROM conversations conv
JOIN customers c ON conv.customer_id = c.id;

-- Insert sample metrics
INSERT INTO agent_metrics (metric_type, metric_value, channel, metadata)
SELECT
    CASE
        WHEN RANDOM() < 0.33 THEN 'response_time'
        WHEN RANDOM() < 0.66 THEN 'accuracy'
        ELSE 'satisfaction'
    END,
    (RANDOM() * 5 + 1)::NUMERIC(3,2),
    CASE
        WHEN RANDOM() < 0.33 THEN 'email'
        WHEN RANDOM() < 0.66 THEN 'whatsapp'
        WHEN RANDOM() < 0.99 THEN 'web_form'
        ELSE 'overall'
    END,
    '{}';

EOF

if [ $? -eq 0 ]; then
    echo -e "${GREEN}Database seeded successfully!${NC}"
else
    echo -e "${RED}Error seeding database${NC}"
    exit 1
fi

# Verify data was inserted
echo -e "${YELLOW}Verifying seeded data...${NC}"

CUSTOMER_COUNT=$(docker exec customer-success-fte-postgres psql -U postgres -d fte_db -t -c "SELECT COUNT(*) FROM customers;" | tr -d ' ')
CONV_COUNT=$(docker exec customer-success-fte-postgres psql -U postgres -d fte_db -t -c "SELECT COUNT(*) FROM conversations;" | tr -d ' ')
MSG_COUNT=$(docker exec customer-success-fte-postgres psql -U postgres -d fte_db -t -c "SELECT COUNT(*) FROM messages;" | tr -d ' ')
TICKET_COUNT=$(docker exec customer-success-fte-postgres psql -U postgres -d fte_db -t -c "SELECT COUNT(*) FROM tickets;" | tr -d ' ')

echo -e "${GREEN}Data verification:${NC}"
echo -e "  • Customers: $CUSTOMER_COUNT"
echo -e "  • Conversations: $CONV_COUNT"
echo -e "  • Messages: $MSG_COUNT"
echo -e "  • Tickets: $TICKET_COUNT"

echo ""
echo "=========================================="
echo "DATABASE SEEDING COMPLETE"
echo "=========================================="
