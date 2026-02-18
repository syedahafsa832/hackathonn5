#!/bin/bash

echo "=========================================="
echo "COMPLETE TESTING WORKFLOW"
echo "=========================================="

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Step 1: Start all services
echo -e "${YELLOW}Step 1: Starting all services...${NC}"
./start_all.sh
if [ $? -ne 0 ]; then
    echo -e "${RED}Failed to start services${NC}"
    exit 1
fi
echo ""

# Step 2: Wait for services to be ready
echo -e "${YELLOW}Step 2: Waiting for services to be ready...${NC}"
sleep 30

# Step 3: Seed database
echo -e "${YELLOW}Step 3: Seeding database with test data...${NC}"
./seed_data.sh
if [ $? -ne 0 ]; then
    echo -e "${RED}Failed to seed database${NC}"
    exit 1
fi
echo ""

# Step 4: Run tests
echo -e "${YELLOW}Step 4: Running all tests...${NC}"
./run_tests.sh
TEST_RESULT=$?
echo ""

# Step 5: Generate report
echo -e "${YELLOW}Step 5: Generating test report...${NC}"
echo ""
echo "=========================================="
echo "DATABASE STATUS REPORT"
echo "=========================================="

if command -v docker &> /dev/null; then
    # Count customers
    CUSTOMER_COUNT=$(docker exec customer-success-fte-postgres psql -U postgres -d fte_db -t -c "SELECT COUNT(*) FROM customers;" 2>/dev/null | tr -d ' ')
    echo "Total Customers: ${CUSTOMER_COUNT:-0}"

    # Count conversations
    CONV_COUNT=$(docker exec customer-success-fte-postgres psql -U postgres -d fte_db -t -c "SELECT COUNT(*) FROM conversations;" 2>/dev/null | tr -d ' ')
    echo "Total Conversations: ${CONV_COUNT:-0}"

    # Count messages
    MSG_COUNT=$(docker exec customer-success-fte-postgres psql -U postgres -d fte_db -t -c "SELECT COUNT(*) FROM messages;" 2>/dev/null | tr -d ' ')
    echo "Total Messages: ${MSG_COUNT:-0}"

    # Count tickets
    TICKET_COUNT=$(docker exec customer-success-fte-postgres psql -U postgres -d fte_db -t -c "SELECT COUNT(*) FROM tickets;" 2>/dev/null | tr -d ' ')
    echo "Total Tickets: ${TICKET_COUNT:-0}"

    # Count knowledge base articles
    KB_COUNT=$(docker exec customer-success-fte-postgres psql -U postgres -d fte_db -t -c "SELECT COUNT(*) FROM knowledge_base;" 2>/dev/null | tr -d ' ')
    echo "Knowledge Base Articles: ${KB_COUNT:-0}"

    # Messages by channel
    echo ""
    echo "Messages by Channel:"
    if [ ! -z "$MSG_COUNT" ] && [ "$MSG_COUNT" -gt 0 ]; then
        docker exec customer-success-fte-postgres psql -U postgres -d fte_db -c "SELECT channel, COUNT(*) as count FROM messages GROUP BY channel;" 2>/dev/null || echo "  Unable to fetch"
    else
        echo "  No messages found"
    fi

    # Tickets by status
    echo ""
    echo "Tickets by Status:"
    if [ ! -z "$TICKET_COUNT" ] && [ "$TICKET_COUNT" -gt 0 ]; then
        docker exec customer-success-fte-postgres psql -U postgres -d fte_db -c "SELECT status, COUNT(*) as count FROM tickets GROUP BY status;" 2>/dev/null || echo "  Unable to fetch"
    else
        echo "  No tickets found"
    fi
else
    echo "Docker not available, skipping database report"
fi

echo "=========================================="
echo ""

# Step 6: Display service logs
echo -e "${YELLOW}Step 6: Recent service logs...${NC}"
if command -v docker-compose &> /dev/null; then
    echo "API Logs (last 10 lines):"
    docker-compose logs --tail=10 backend 2>/dev/null || echo "  Unable to fetch logs"
    echo ""
    echo "Worker Logs (last 10 lines):"
    docker-compose logs --tail=10 worker 2>/dev/null || echo "  Unable to fetch logs"
    echo ""
else
    echo "Docker-compose not available"
    echo ""
fi

# Final summary
echo "=========================================="
echo "TESTING WORKFLOW COMPLETE"
echo "=========================================="

if [ $TEST_RESULT -eq 0 ]; then
    echo -e "${GREEN}✅ ALL TESTS PASSED${NC}"
    exit 0
else
    echo -e "${RED}❌ SOME TESTS FAILED${NC}"
    exit 1
fi
