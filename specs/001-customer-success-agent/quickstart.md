# Quickstart Guide: Customer Success AI Agent

**Date**: 2026-02-03
**Feature**: Customer Success AI Agent (Digital FTE)
**Branch**: 001-customer-success-agent

## Overview

This guide provides step-by-step instructions to set up and run the Customer Success AI Agent locally. The system consists of a backend service (handling AI agent and channel integrations) and a frontend web form.

## Prerequisites

- Python 3.11+
- Node.js 18+ and npm/yarn
- PostgreSQL 16+ with pgvector extension
- Docker and Docker Compose (recommended for local development)
- OpenAI API key
- Gmail API credentials (for email channel)
- Twilio account credentials (for WhatsApp channel)

## Local Development Setup

### 1. Clone and Navigate to Repository

```bash
git clone <repository-url>
cd <repository-name>
```

### 2. Set Up PostgreSQL with pgvector

#### Option A: Using Docker (Recommended)

```bash
# Start PostgreSQL with pgvector using docker-compose
docker-compose up -d postgres

# Verify the database is running
docker-compose logs postgres
```

#### Option B: Local Installation

1. Install PostgreSQL 16+
2. Install pgvector extension:
   ```bash
   # On Ubuntu/Debian
   sudo apt install postgresql-16-pgvector

   # On macOS with Homebrew
   brew install pgvector

   # On Windows, download from the pgvector releases page
   ```
3. Enable pgvector in your PostgreSQL installation:
   ```sql
   -- Connect to your database
   CREATE EXTENSION IF NOT EXISTS vector;
   ```

### 3. Backend Setup

#### Create and Activate Virtual Environment

```bash
cd backend
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

#### Install Dependencies

```bash
pip install -r requirements.txt
```

#### Configure Environment Variables

Create a `.env` file in the `backend` directory:

```env
# Database Configuration
DATABASE_URL=postgresql://username:password@localhost:5432/fte_db

# OpenAI Configuration
GROK_API_KEY=your_GROK_API_KEY_here

# Gmail API Configuration
GMAIL_CLIENT_ID=your_gmail_client_id
GMAIL_CLIENT_SECRET=your_gmail_client_secret
GMAIL_REFRESH_TOKEN=your_gmail_refresh_token

# Twilio Configuration
TWILIO_ACCOUNT_SID=your_twilio_account_sid
TWILIO_AUTH_TOKEN=your_twilio_auth_token
TWILIO_WHATSAPP_NUMBER=your_twilio_whatsapp_number

# Kafka Configuration
KAFKA_BOOTSTRAP_SERVERS=localhost:9092

# Application Configuration
SECRET_KEY=your_secret_key_for_security
DEBUG=True
```

#### Run Database Migrations

```bash
# Initialize the database tables
python -m src.services.database --init-db
```

#### Start the Backend Server

```bash
# From the backend directory
uvicorn src.api.main:app --reload --port 8000
```

### 4. Frontend Setup

#### Install Dependencies

```bash
cd web-form  # Navigate to the web-form directory
npm install
# or yarn install
```

#### Configure Environment Variables

Create a `.env` file in the `web-form` directory:

```env
REACT_APP_API_BASE_URL=http://localhost:8000
REACT_APP_ENVIRONMENT=development
```

#### Start the Frontend Development Server

```bash
npm start
# or yarn start
```

The web form will be available at `http://localhost:3000`.

### 5. Complete Setup with Docker Compose (Recommended)

Alternatively, run everything with a single command:

```bash
# From the repository root
docker-compose up --build
```

This will start:
- PostgreSQL with pgvector
- Backend service on port 8000
- Frontend on port 3000
- Kafka for event streaming

## Initial Configuration

### 1. Populate Knowledge Base

After starting the services, populate the knowledge base with product documentation:

```bash
# From backend directory
python -m src.services.knowledge_base --load-docs /path/to/docs
```

### 2. Set Up Webhooks

For production deployment, configure webhooks:

#### Gmail Webhook
- Configure Google Cloud Pub/Sub to forward emails to your `/webhooks/gmail` endpoint
- Set up OAuth2 credentials for Gmail API access

#### WhatsApp Webhook
- Point your Twilio WhatsApp number to your `/webhooks/whatsapp` endpoint
- Configure webhook validation using your Twilio auth token

## Testing the Setup

### 1. API Health Check

Verify the backend is running:
```bash
curl http://localhost:8000/health
```

### 2. Submit Test Ticket via Web Form

1. Navigate to `http://localhost:3000`
2. Fill out the support form with test data
3. Submit and verify you receive a ticket ID

### 3. Test API Endpoints

```bash
# Create a test ticket
curl -X POST http://localhost:8000/support/submit \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test User",
    "email": "test@example.com",
    "subject": "Test Inquiry",
    "category": "technical",
    "priority": "medium",
    "message": "This is a test inquiry."
  }'

# Check ticket status
curl http://localhost:8000/support/ticket/{ticket_id}
```

## Running Tests

### Backend Tests

```bash
# Run all tests
pytest

# Run unit tests
pytest tests/unit/

# Run integration tests
pytest tests/integration/

# Run E2E tests
pytest tests/e2e/
```

### Frontend Tests

```bash
# Run frontend tests
npm test
# or yarn test
```

## Development Workflow

### Backend Development

1. Make changes to backend code
2. The server will auto-reload with `--reload` flag
3. Run tests: `pytest`
4. Format code: `black .` and `flake8 .`

### Frontend Development

1. Make changes to React components
2. The development server will auto-refresh
3. Run tests: `npm test`
4. Format code: `npm run format`

## Troubleshooting

### Common Issues

1. **Database Connection Error**
   - Ensure PostgreSQL is running
   - Verify `DATABASE_URL` in your `.env` file
   - Check that pgvector extension is installed

2. **OpenAI API Error**
   - Verify `GROK_API_KEY` is set correctly
   - Check API key has proper permissions

3. **Port Already in Use**
   - Change ports in `.env` files and docker-compose.yml
   - Kill processes using the ports: `lsof -ti:8000 | xargs kill -9`

4. **Frontend Cannot Connect to Backend**
   - Verify backend is running on the expected port
   - Check `REACT_APP_API_BASE_URL` in frontend `.env`

### Resetting Local Environment

```bash
# Stop all services
docker-compose down

# Remove volumes (will delete all data)
docker-compose down -v

# Restart fresh
docker-compose up --build
```

## Next Steps

1. Configure your production environment
2. Set up proper SSL certificates
3. Configure monitoring and logging
4. Set up CI/CD pipeline
5. Add your product documentation to the knowledge base
