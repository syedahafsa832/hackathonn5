# Customer Success Digital FTE - Startup and Testing Guide

This guide provides step-by-step instructions to start, test, and verify the Customer Success Digital FTE project.

## Prerequisites

- Docker and Docker Compose
- Python 3.11+ (for local testing)
- Git

## Quick Start (Recommended)

### 1. Clone and Setup

```bash
git clone <repository-url>
cd customer-success-digital-fte
cp .env.example .env
# Edit .env file with your API keys and credentials
```

### 2. Start All Services

```bash
./test_all.sh
```

This single command will:

- Start all required services (PostgreSQL, Kafka, API, Worker, Web Form)
- Seed the database with test data
- Run all tests
- Generate a status report

## Individual Commands (For Development)

### Start Services Only

```bash
./start_all.sh
```

This will:

- Stop any existing containers
- Start PostgreSQL with pgvector
- Start Kafka and Zookeeper
- Create required Kafka topics
- Start API, Worker, and Web Form services

### Seed Database with Test Data

```bash
./seed_data.sh
```

This will populate the database with:

- Sample knowledge base articles
- Sample customers
- Sample conversations and messages
- Sample tickets
- Channel configurations

### Run Tests Only

```bash
./run_tests.sh
```

This will run:

- Health checks
- Database connectivity tests
- API endpoint tests
- Kafka topic verification
- Form submission tests
- Unit tests (if pytest is available)

## Manual Testing Commands

### Health Check

```bash
curl http://localhost:8000/health
```

### Submit Test Support Request

```bash
curl -X POST http://localhost:8000/support/submit \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test User",
    "email": "test@example.com",
    "subject": "Test Issue",
    "category": "technical",
    "priority": "medium",
    "message": "This is a test message to verify the system works correctly."
  }'
```

### Check Database Contents

```bash
# Connect to database
docker exec -it customer-success-fte-postgres psql -U postgres -d fte_db

# Check customers table
SELECT * FROM customers LIMIT 5;

# Check messages table
SELECT * FROM messages LIMIT 5;

# Exit
\q
```

### Check Kafka Topics

```bash
docker exec customer-success-fte-kafka kafka-topics --list --bootstrap-server localhost:9092
```

### View Service Logs

```bash
# View all logs
docker-compose logs

# View specific service logs
docker-compose logs -f backend    # API logs
docker-compose logs -f worker     # Worker logs
docker-compose logs -f postgres   # Database logs
docker-compose logs -f kafka      # Kafka logs
```

## Troubleshooting

### Common Issues

1. **Services won't start**: Make sure Docker is running and you have sufficient privileges
2. **Database connection errors**: Verify PostgreSQL is healthy with `docker-compose ps`
3. **API not responding**: Check if Kafka is running and API has proper environment variables
4. **Worker not processing messages**: Verify Kafka topics exist and worker has proper permissions

### Restart Specific Service

```bash
docker-compose restart backend
docker-compose restart worker
```

### Clean Restart

```bash
# Stop all services and remove volumes
docker-compose down -v

# Start fresh
./start_all.sh
```

### Environment Variables

Make sure your `.env` file contains all required variables:

```bash
# OpenAI API key (required)
GROK_API_KEY=your-openai-api-key

# Database (usually default values work)
POSTGRES_HOST=localhost
POSTGRES_DB=fte_db
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres

# Kafka (usually default works)
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
```

## Setup

The system is configured for two channels: Meta WhatsApp and Web Form.

### Meta WhatsApp Setup

1. Configure your Meta Business credentials in `.env`.
2. Ensure `META_WHATSAPP_VERIFY_TOKEN` matches your webhook settings.
3. Start the system and point your webhook to `https://<your-domain>/webhooks/whatsapp`.

### Web Form Setup

1. The web form is available on the frontend.
2. Ensure the backend is reachable from the frontend.

## Services Overview

- **API Service**: Runs on `http://localhost:8000`
- **Web Form**: Runs on `http://localhost:3000`
- **PostgreSQL**: Available on `localhost:5432`
- **Kafka**: Available on `localhost:9092`
- **Worker**: Processes messages from Kafka queues

## Verification Steps

After running `./test_all.sh`, you should see:

1. ✅ All services running (backend, worker, postgres, kafka, web-form)
2. ✅ Health check returning "healthy"
3. ✅ Database tables created with test data
4. ✅ Kafka topics created
5. ✅ All tests passing
6. ✅ Sample data in database tables

## Production Deployment Notes

For production deployment:

1. Use proper secrets management (not environment files)
2. Configure proper resource limits
3. Set up monitoring and alerting
4. Configure SSL certificates for HTTPS
5. Set up backup and recovery procedures
6. Review security configurations

## Support

If you encounter issues:

1. Check service logs: `docker-compose logs -f`
2. Verify environment variables in `.env`
3. Ensure all prerequisites are met
4. Check available system resources (RAM, disk space)
5. Review firewall/network settings if running remotely
