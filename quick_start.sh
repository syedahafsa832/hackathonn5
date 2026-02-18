#!/bin/bash

# Customer Success AI Agent - Quick Start Script
# This script sets up and starts all services for the customer success agent system

set -e  # Exit on any error

echo "🚀 Starting Customer Success AI Agent Quick Setup..."

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if Docker is installed and running
print_step "Checking Docker installation..."
if ! command -v docker &> /dev/null; then
    print_error "Docker is not installed. Please install Docker first."
    exit 1
fi

if ! docker ps &> /dev/null; then
    print_error "Docker daemon is not running. Please start Docker and try again."
    exit 1
fi

print_success "Docker is installed and running."

# Check if docker-compose is installed
print_step "Checking Docker Compose installation..."
if ! command -v docker-compose &> /dev/null; then
    print_error "Docker Compose is not installed. Please install Docker Compose first."
    exit 1
fi

print_success "Docker Compose is installed."

# Check for .env file
print_step "Checking for .env file..."
if [ ! -f ".env" ]; then
    print_warning ".env file not found. Creating from .env.example..."
    if [ -f ".env.example" ]; then
        cp .env.example .env
        print_success ".env file created from .env.example. Please update it with your credentials."
    else
        print_error ".env.example file not found. Cannot create .env file."
        exit 1
    fi
else
    print_success ".env file exists."
fi

# Start all services
print_step "Starting all services with Docker Compose..."
docker-compose up -d

# Wait for services to be healthy
print_step "Waiting for services to start (this may take 1-2 minutes)..."
sleep 30

# Check service status
print_step "Checking service status..."
SERVICES=("postgres" "kafka" "zookeeper" "redis")
for service in "${SERVICES[@]}"; do
    if docker-compose ps | grep -q "$service" && docker-compose ps | grep -q "Up"; then
        print_success "$service is running"
    else
        print_error "$service is not running properly"
    fi
done

# Create Kafka topics
print_step "Creating Kafka topics..."
sleep 10  # Give Kafka a bit more time to fully start
docker-compose exec kafka kafka-topics.sh --create --topic fte.tickets.incoming --bootstrap-server localhost:9092 --if-not-exists
docker-compose exec kafka kafka-topics.sh --create --topic fte.whatsapp.incoming --bootstrap-server localhost:9092 --if-not-exists
docker-compose exec kafka kafka-topics.sh --create --topic fte.conversations.escalated --bootstrap-server localhost:9092 --if-not-exists
docker-compose exec kafka kafka-topics.sh --create --topic fte.metrics --bootstrap-server localhost:9092 --if-not-exists
docker-compose exec kafka kafka-topics.sh --create --topic fte.dlq --bootstrap-server localhost:9092 --if-not-exists
docker-compose exec kafka kafka-topics.sh --create --topic fte.whatsapp.outbound --bootstrap-server localhost:9092 --if-not-exists
docker-compose exec kafka kafka-topics.sh --create --topic fte.webform.outbound --bootstrap-server localhost:9092 --if-not-exists
docker-compose exec kafka kafka-topics.sh --create --topic fte.email.outbound --bootstrap-server localhost:9092 --if-not-exists

print_success "Kafka topics created."

# Check if database migrations need to be run
print_step "Setting up database schema..."
sleep 5
docker-compose exec api python -c "
import asyncio
import sys
import os
sys.path.insert(0, '/app')
from src.services.database import create_tables
import asyncio

async def setup_db():
    try:
        await create_tables()
        print('Database tables created successfully.')
    except Exception as e:
        print(f'Error creating tables: {e}')
        # This might fail if tables already exist, which is fine

asyncio.run(setup_db())
"

# Start backend services
print_step "Starting backend services..."
cd backend
pip install -r requirements.txt 2>/dev/null || echo "Installing backend dependencies failed, continuing..."
cd ..

# Start the API server in background
print_step "Starting API server..."
nohup python -m uvicorn backend.src.api.main:app --host 0.0.0.0 --port 8000 --reload > api.log 2>&1 &
API_PID=$!
echo $API_PID > api.pid

# Start the worker in background
print_step "Starting message processor worker..."
nohup python production/workers/message_processor.py > worker.log 2>&1 &
WORKER_PID=$!
echo $WORKER_PID > worker.pid

print_success "API server started with PID: $API_PID"
print_success "Worker started with PID: $WORKER_PID"

# Start frontend if available
if [ -d "web-form" ] && [ -f "web-form/package.json" ]; then
    print_step "Starting web form frontend..."
    cd web-form
    npm install 2>/dev/null || echo "Installing frontend dependencies failed, continuing..."
    nohup npm start > frontend.log 2>&1 &
    FRONTEND_PID=$!
    echo $FRONTEND_PID > frontend.pid
    cd ..
    print_success "Frontend started with PID: $FRONTEND_PID"
fi

# Run health checks
print_step "Running health checks..."
sleep 10

HEALTHY=true

# Check API health
if curl -f http://localhost:8000/health >/dev/null 2>&1; then
    print_success "API health check passed"
else
    print_error "API health check failed"
    HEALTHY=false
fi

# Check if Kafka is accessible
if docker-compose exec kafka kafka-topics.sh --list --bootstrap-server localhost:9092 >/dev/null 2>&1; then
    print_success "Kafka connectivity check passed"
else
    print_error "Kafka connectivity check failed"
    HEALTHY=false
fi

# Display status summary
echo ""
echo "==========================================="
echo "📊 SYSTEM STATUS SUMMARY"
echo "==========================================="

echo "📋 Services:"
docker-compose ps

echo ""
echo "🔗 Access Points:"
echo "  - API: http://localhost:8000"
echo "  - Health Check: http://localhost:8000/health"
echo "  - Support Form: http://localhost:3000 (if frontend started)"

echo ""
echo "🔧 Running Processes:"
if [ -f "api.pid" ]; then
    echo "  - API Server (PID: $(cat api.pid))"
fi
if [ -f "worker.pid" ]; then
    echo "  - Message Worker (PID: $(cat worker.pid))"
fi
if [ -f "frontend.pid" ]; then
    echo "  - Frontend (PID: $(cat frontend.pid))"
fi

echo ""
if [ "$HEALTHY" = true ]; then
    echo -e "${GREEN}🎉 ALL SERVICES ARE RUNNING SUCCESSFULLY!${NC}"
    echo ""
    echo "✨ Next Steps:"
    echo "  1. Visit http://localhost:8000/health to verify API is working"
    echo "  2. Submit a test ticket via the web form or API"
    echo "  3. Check the logs with 'docker-compose logs' for any issues"
    echo "  4. For WhatsApp integration, set up ngrok: 'ngrok http 8000'"
    echo "  5. Configure WhatsApp webhook in Meta Business Suite"
    echo ""
    echo "📝 Logs are available in:"
    echo "  - api.log (API server logs)"
    echo "  - worker.log (Message processor logs)"
    echo "  - frontend.log (Frontend logs)"
    echo "  - Docker Compose logs: 'docker-compose logs'"
else
    echo -e "${RED}❌ Some services are not healthy. Check logs for details.${NC}"
    echo "Check logs with: docker-compose logs"
    echo "Check API logs: cat api.log"
    echo "Check worker logs: cat worker.log"
fi

echo ""
echo "🛑 To stop all services:"
echo "  - Kill background processes: kill \$(cat api.pid) \$(cat worker.pid) \$(cat frontend.pid 2>/dev/null || echo '')"
echo "  - Stop Docker services: docker-compose down"
echo ""

echo "🚀 Customer Success AI Agent setup complete!"