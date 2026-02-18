#!/bin/bash

echo "=========================================="
echo "Starting Customer Success Digital FTE"
echo "=========================================="

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if .env exists
if [ ! -f .env ]; then
    echo -e "${RED}ERROR: .env file not found!${NC}"
    echo "Please create .env file from .env.example and add your API keys"
    exit 1
fi

# Load environment variables
set -a
source .env
set +a

echo -e "${YELLOW}Step 1: Stopping any existing containers...${NC}"
docker-compose down -v

echo -e "${YELLOW}Step 2: Starting infrastructure services (PostgreSQL, Kafka)...${NC}"
docker-compose up -d postgres zookeeper kafka

echo -e "${YELLOW}Waiting for PostgreSQL to be ready...${NC}"
while ! docker exec customer-success-fte-postgres pg_isready -U postgres > /dev/null 2>&1; do
    echo -n "."
    sleep 1
done
echo -e "${GREEN}PostgreSQL is ready!${NC}"

echo -e "${YELLOW}Waiting for Kafka to be ready...${NC}"
sleep 15
echo -e "${GREEN}Kafka is ready!${NC}"

echo -e "${YELLOW}Step 3: Creating Kafka topics...${NC}"
sleep 5  # Give Kafka a bit more time to fully start
docker exec customer-success-fte-kafka kafka-topics --create --if-not-exists --topic fte.tickets.incoming --bootstrap-server localhost:9092 --partitions 3 --replication-factor 1
docker exec customer-success-fte-kafka kafka-topics --create --if-not-exists --topic fte.channels.email.inbound --bootstrap-server localhost:9092 --partitions 3 --replication-factor 1
docker exec customer-success-fte-kafka kafka-topics --create --if-not-exists --topic fte.channels.whatsapp.inbound --bootstrap-server localhost:9092 --partitions 3 --replication-factor 1
docker exec customer-success-fte-kafka kafka-topics --create --if-not-exists --topic fte.channels.webform.inbound --bootstrap-server localhost:9092 --partitions 3 --replication-factor 1
docker exec customer-success-fte-kafka kafka-topics --create --if-not-exists --topic fte.channels.email.outbound --bootstrap-server localhost:9092 --partitions 3 --replication-factor 1
docker exec customer-success-fte-kafka kafka-topics --create --if-not-exists --topic fte.channels.whatsapp.outbound --bootstrap-server localhost:9092 --partitions 3 --replication-factor 1
docker exec customer-success-fte-kafka kafka-topics --create --if-not-exists --topic fte.escalations --bootstrap-server localhost:9092 --partitions 3 --replication-factor 1
docker exec customer-success-fte-kafka kafka-topics --create --if-not-exists --topic fte.metrics --bootstrap-server localhost:9092 --partitions 3 --replication-factor 1
docker exec customer-success-fte-kafka kafka-topics --create --if-not-exists --topic fte.dlq --bootstrap-server localhost:9092 --partitions 3 --replication-factor 1
echo -e "${GREEN}Kafka topics created!${NC}"

echo -e "${YELLOW}Step 4: Starting application services (API, Worker, Web Form)...${NC}"
docker-compose up -d backend worker web-form

echo -e "${YELLOW}Waiting for services to be ready...${NC}"
sleep 10

# Check if services are running
if docker-compose ps | grep -q "Up"; then
    echo -e "${GREEN}Services are starting up!${NC}"
else
    echo -e "${RED}ERROR: Some services failed to start${NC}"
    docker-compose ps
    exit 1
fi

echo -e "${YELLOW}Step 5: Verifying service health...${NC}"

# Wait and check health
for i in {1..10}; do
    HEALTH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>/dev/null)
    if [ "$HEALTH_STATUS" -eq 200 ]; then
        echo -e "${GREEN}API service is healthy!${NC}"
        break
    else
        echo -n "."
        sleep 5
    fi
done

if [ "$HEALTH_STATUS" != "200" ]; then
    echo -e "${RED}WARNING: API service may not be ready yet${NC}"
fi

echo ""
echo "=========================================="
echo "SERVICES STARTED SUCCESSFULLY"
echo "=========================================="
echo "API: http://localhost:8000"
echo "Web Form: http://localhost:3000"
echo "PostgreSQL: localhost:5432"
echo "Kafka: localhost:9092"
echo ""
echo "To view logs: docker-compose logs -f"
echo "To stop: docker-compose down"
echo "=========================================="
