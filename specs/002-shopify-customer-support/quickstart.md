# Quickstart: Shopify Customer Support AI

## Prerequisites

- Docker and Docker Compose
- Python 3.11+ (for local development)
- Node.js 18+ (for frontend development)
- Supabase account (or local PostgreSQL)
- Shopify store with Admin API access
- Mistral AI API key

## Environment Setup

1. Clone the repository:
```bash
git clone <repo-url>
cd hack5
```

2. Copy environment file:
```bash
cp .env.example .env
```

3. Configure environment variables in `.env`:
```env
# Supabase
SUPABASE_URL=your-supabase-url
SUPABASE_KEY=your-supabase-anon-key

# Mistral AI
MISTRAL_API_KEY=your-mistral-api-key

# Shopify (per tenant, stored in database)
# Set up via Dashboard Settings

# Email (SMTP for sending replies)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASS=your-app-password
```

## Running the System

### Docker Compose (Recommended)

Start all services:
```bash
docker-compose up --build
```

Services:
- **API** (backend): http://localhost:8000
- **Web Form** (frontend): http://localhost:3001
- **PostgreSQL**: localhost:5432
- **Redis**: localhost:6379
- **Kafka**: localhost:9092
- **Email Poller**: Background worker for incoming emails

### Local Development

**Backend:**
```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

**Frontend:**
```bash
cd web-form
npm install
npm run dev
```

## Initial Setup

### 1. Create Tenant (Brand)

Using the Dashboard or API:
```bash
curl -X POST http://localhost:8000/api/v2/brands \
  -H "Authorization: Bearer <supabase-jwt>" \
  -H "Content-Type: application/json" \
  -d '{"name": "My Brand", "shopify_domain": "mystore.myshopify.com"}'
```

### 2. Connect Shopify

In Dashboard > Settings:
1. Enter Shopify store domain
2. Enter Admin API access token
3. System validates connection

### 3. Configure Email

In Dashboard > Settings:
1. Enter support email address
2. Enter SMTP credentials
3. System validates and stores encrypted

### 4. Add Knowledge Base Articles

In Dashboard > Knowledge Base:
1. Add FAQ articles
2. Add return policy
3. Add shipping info

The AI uses these articles to answer customer questions.

## Usage Flow

### Customer Submits Request

**Via Web Form:**
1. Customer visits support page
2. Fills form with email, order number (optional), subject, message
3. Receives ticket ID and status

**Via Email:**
1. Customer emails brand's support address
2. Email poller catches and processes
3. Ticket created automatically

### AI Processing

1. System extracts customer info and order number
2. AI detects intent (question, refund, cancel, address change)
3. AI analyzes sentiment
4. If simple question: generates auto-reply using RAG
5. If sensitive action: creates action proposal

### Human Approval (if action needed)

1. User views Action Queue in Dashboard
2. Reviews action details (order, operation, confidence, risk)
3. Clicks "Approve" or "Reject"
4. If approved, action executes in Shopify

### Customer Notified

1. If action executed, customer receives confirmation email
2. If rejected, customer notified of rejection

## Dashboard Pages

| Page | Description |
|------|-------------|
| Inbox | View all tickets, filter by status |
| Action Queue | Review and approve/reject pending actions |
| History | View audit log of all events |
| Settings | Configure Shopify, email, knowledge base |
| Knowledge Base | Add/edit FAQ articles |

## Testing

Run backend tests:
```bash
cd backend
pytest tests/
```

Run frontend tests:
```bash
cd web-form
npm test
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /api/v2/support/tickets` | Create ticket |
| `GET /api/v2/support/tickets` | List tickets |
| `GET /api/v2/support/tickets/{id}` | Get ticket |
| `GET /api/v2/support/actions` | List actions |
| `POST /api/v2/support/actions/{id}/approve` | Approve action |
| `POST /api/v2/support/actions/{id}/reject` | Reject action |
| `GET /api/v2/support/knowledge` | List knowledge |
| `POST /api/v2/support/knowledge` | Add article |
| `GET /api/v2/support/settings` | Get settings |
| `PUT /api/v2/support/settings` | Update settings |
| `GET /api/v2/support/history` | Get audit logs |

## Troubleshooting

**Email not being processed:**
- Check email poller logs
- Verify SMTP credentials in settings

**Shopify actions failing:**
- Check API token is valid
- Verify order exists
- Check action error message in Dashboard

**AI not responding:**
- Verify Mistral API key
- Check knowledge base has content
- Check logs for errors

**Tenant data visible to other tenants:**
- Verify RLS policies are enabled
- Check Supabase JWT contains tenant_id
- Review auth middleware configuration