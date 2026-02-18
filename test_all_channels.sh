#!/bin/bash

# Customer Success AI Agent - Test All Channels Script
# This script runs tests for all three channels (WhatsApp, Web Form, Email)

set -e  # Exit on any error

echo "🧪 Testing Customer Success AI Agent - All Channels..."

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_step() {
    echo -e "${BLUE}[TEST]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[PASS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[SKIP]${NC} $1"
}

print_error() {
    echo -e "${RED}[FAIL]${NC} $1"
}

# Function to test WhatsApp channel
test_whatsapp() {
    print_step "Testing WhatsApp Channel..."

    # Check if WhatsApp webhook endpoint is accessible
    if curl -f -X POST http://localhost:8000/webhooks/whatsapp \
        -H "Content-Type: application/json" \
        -d '{"entry": [{"changes": [{"value": {"messages": [{"id": "test_msg", "from": "whatsapp:+1234567890", "text": {"body": "Test message"}}], "contacts": [{"profile": {"name": "Test User"}, "wa_id": "+1234567890"}]}}]}]}' \
        --max-time 10 >/dev/null 2>&1; then
        print_success "WhatsApp webhook endpoint is responsive"
    else
        print_error "WhatsApp webhook endpoint test failed - service may not be running"
    fi
}

# Function to test Web Form channel
test_web_form() {
    print_step "Testing Web Form Channel..."

    # Test web form submission endpoint
    if curl -f -X POST http://localhost:8000/support/submit \
        -H "Content-Type: application/json" \
        -d '{
            "name": "Test User",
            "email": "test@example.com",
            "subject": "Test Subject",
            "category": "technical",
            "priority": "medium",
            "message": "This is a test message for web form channel.",
            "company": "Test Company"
        }' \
        --max-time 10 >/dev/null 2>&1; then
        print_success "Web form submission endpoint is responsive"
    else
        print_error "Web form submission endpoint test failed - service may not be running"
    fi

    # Test ticket status endpoint
    if curl -f http://localhost:8000/support/ticket/test-ticket-id \
        --max-time 10 >/dev/null 2>&1; then
        print_success "Ticket status endpoint is responsive"
    else
        print_error "Ticket status endpoint test failed - service may not be running"
    fi
}

# Function to test Email channel
test_email() {
    print_step "Testing Email Channel..."

    # Test email simulator endpoint
    if curl -f -X POST http://localhost:8000/email/simulate \
        -H "Content-Type: application/json" \
        -d '{
            "from_email": "customer@example.com",
            "to_emails": ["support@techcorp.com"],
            "subject": "Test Email Subject",
            "body": "This is a test email body for the email channel simulator."
        }' \
        --max-time 10 >/dev/null 2>&1; then
        print_success "Email simulator endpoint is responsive"
    else
        print_error "Email simulator endpoint test failed - service may not be running"
    fi
}

# Function to run comprehensive test
run_comprehensive_test() {
    print_step "Running comprehensive multi-channel test..."

    # Submit requests to all channels simultaneously using background jobs
    echo "Submitting requests to all channels..."

    # Web form request
    (
        sleep 1  # Slight delay to avoid race conditions
        curl -f -X POST http://localhost:8000/support/submit \
            -H "Content-Type: application/json" \
            -d '{
                "name": "Multi-Channel Test",
                "email": "multitest@example.com",
                "subject": "Multi-Channel Test",
                "category": "general",
                "priority": "medium",
                "message": "Testing multi-channel functionality."
            }' \
            --max-time 10
    ) &

    # Email request
    (
        sleep 2  # Slight delay to avoid race conditions
        curl -f -X POST http://localhost:8000/email/simulate \
            -H "Content-Type: application/json" \
            -d '{
                "from_email": "multitest@example.com",
                "to_emails": ["support@techcorp.com"],
                "subject": "Multi-Channel Email Test",
                "body": "Testing multi-channel email functionality."
            }' \
            --max-time 10
    ) &

    # Wait for all background jobs to complete
    wait

    print_success "Comprehensive multi-channel test completed"
}

# Function to run unit tests
run_unit_tests() {
    print_step "Running unit tests for all channels..."

    # Check if pytest is available
    if ! command -v pytest &> /dev/null; then
        print_warning "pytest not found, installing..."
        pip install pytest pytest-asyncio
    fi

    # Run specific channel tests
    if [ -d "production/tests" ]; then
        cd production/tests

        # Run channel-specific tests
        if [ -f "test_channels.py" ]; then
            print_step "Running channel integration tests..."
            if pytest test_channels.py -v; then
                print_success "Channel integration tests passed"
            else
                print_error "Channel integration tests failed"
            fi
        fi

        # Run email channel tests
        if [ -f "test_email_channel.py" ]; then
            print_step "Running email channel tests..."
            if pytest test_email_channel.py -v; then
                print_success "Email channel tests passed"
            else
                print_error "Email channel tests failed"
            fi
        fi

        # Run multichannel tests
        if [ -f "test_multichannel_e2e.py" ]; then
            print_step "Running multichannel end-to-end tests..."
            if pytest test_multichannel_e2e.py -v; then
                print_success "Multichannel end-to-end tests passed"
            else
                print_error "Multichannel end-to-end tests failed"
            fi
        fi

        cd ../..
    else
        print_warning "No production tests directory found, skipping unit tests"
    fi
}

# Function to run health checks
run_health_checks() {
    print_step "Running system health checks..."

    # Check if required services are running
    if docker-compose ps | grep -q "Up"; then
        print_success "Docker services are running"
    else
        print_error "Docker services are not running - please start them first"
    fi

    # Check Kafka connectivity
    if docker-compose exec kafka kafka-topics.sh --list --bootstrap-server localhost:9092 >/dev/null 2>&1; then
        print_success "Kafka connectivity OK"
    else
        print_error "Kafka connectivity failed"
    fi

    # Check database connectivity
    if docker-compose exec postgres pg_isready >/dev/null 2>&1; then
        print_success "PostgreSQL connectivity OK"
    else
        print_error "PostgreSQL connectivity failed"
    fi

    # Check API availability
    if curl -f http://localhost:8000/health --max-time 5 >/dev/null 2>&1; then
        print_success "API health check passed"
    else
        print_error "API health check failed"
    fi
}

# Main execution
echo "🚀 Starting comprehensive channel testing..."
echo ""

# Run health checks first
run_health_checks

echo ""
# Test individual channels
test_web_form
test_email
test_whatsapp  # This may fail if WhatsApp is not configured with real credentials

echo ""
# Run comprehensive test
run_comprehensive_test

echo ""
# Run unit tests
run_unit_tests

echo ""
echo "==========================================="
echo "📊 CHANNEL TESTING SUMMARY"
echo "==========================================="

echo ""
echo "📋 Test Results:"
echo "  - Web Form Channel: $(curl -f http://localhost:8000/support/submit -H 'Content-Type: application/json' -d '{\"name\":\"Test\",\"email\":\"t@t.com\",\"subject\":\"Test\",\"category\":\"technical\",\"priority\":\"medium\",\"message\":\"Test\"}' --max-time 5 >/dev/null 2>&1 && echo '✓ PASS' || echo '✗ FAIL')"
echo "  - Email Channel: $(curl -f http://localhost:8000/email/simulate -H 'Content-Type: application/json' -d '{\"from_email\":\"t@test.com\",\"to_emails\":[\"s@t.com\"],\"subject\":\"Test\",\"body\":\"Test\"}' --max-time 5 >/dev/null 2>&1 && echo '✓ PASS' || echo '✗ FAIL')"
echo "  - Health Check: $(curl -f http://localhost:8000/health --max-time 5 >/dev/null 2>&1 && echo '✓ PASS' || echo '✗ FAIL')"

echo ""
echo "🔧 Available Endpoints:"
echo "  - Web Form: POST http://localhost:8000/support/submit"
echo "  - Email Simulator: POST http://localhost:8000/email/simulate"
echo "  - WhatsApp Webhook: POST http://localhost:8000/webhooks/whatsapp"
echo "  - Ticket Status: GET http://localhost:8000/support/ticket/{id}"

echo ""
echo "💡 Tips:"
echo "  - Ensure all services are running before testing"
echo "  - Check logs with: docker-compose logs"
echo "  - For WhatsApp testing, real credentials are needed for actual functionality"
echo "  - Email channel uses simulator for hackathon (production uses Gmail API)"

echo ""
echo "✅ Channel testing completed!"