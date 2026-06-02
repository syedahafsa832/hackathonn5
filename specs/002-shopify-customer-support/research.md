# Research: Shopify Customer Support AI

## Research Tasks

### R1: Shopify Admin API Best Practices

**Decision**: Use Shopify Admin REST API with Python `httpx` async client

**Rationale**:
- REST API is simpler and more documented than GraphQL for our use case
- Async `httpx` integrates well with FastAPI's async model
- Direct HTTP calls give more control over error handling and retries

**Alternatives Considered**:
- `shopify_api` Python library: Synchronous, not maintained for newer API versions
- GraphQL Admin API: More complex, overkill for our simple operations (refund, cancel, address update)

**Key API Operations**:
| Action | Endpoint | Method |
|--------|----------|--------|
| Get Order | `/admin/api/2024-01/orders/{id}.json` | GET |
| Create Refund | `/admin/api/2024-01/orders/{id}/refunds.json` | POST |
| Cancel Order | `/admin/api/2024-01/orders/{id}/cancel.json` | POST |
| Update Shipping Address | `/admin/api/2024-01/orders/{id}/shipping_address.json` | PUT |

**Error Handling**:
- 401 Unauthorized: Token invalid or expired → alert user
- 404 Not Found: Order doesn't exist → flag ticket for review
- 422 Unprocessable: Invalid request body → log error details
- 429 Too Many Requests: Rate limited → retry with exponential backoff
- 5xx Server Error: Shopify issue → retry with backoff, max 3 attempts

---

### R2: Supabase Multi-Tenant Patterns with pgvector

**Decision**: Use Row Level Security (RLS) with `tenant_id` column on all tables + pgvector for embeddings

**Rationale**:
- RLS provides database-level tenant isolation, preventing any query from leaking across tenants
- Adding `tenant_id` to all tables is simple and consistent
- pgvector on Supabase allows tenant-scoped vector similarity search for RAG
- Supabase Auth JWT contains custom claims including tenant_id

**Alternatives Considered**:
- Separate schema per tenant: Complex migrations, harder to manage
- Application-level filtering only: Risk of forgetting filter in some queries
- Separate database per tenant: Expensive, hard to scale

**RLS Pattern**:
```sql
-- Enable RLS on all tables
ALTER TABLE tickets ENABLE ROW LEVEL SECURITY;

-- Create policy that filters by tenant_id
CREATE POLICY "tenant_isolation" ON tickets
  FOR ALL USING (tenant_id = current_setting('app.current_tenant_id')::uuid);
```

**pgvector Search**:
```sql
-- Similarity search scoped to tenant
SELECT title, content, 1 - (embedding <=> $1) as similarity
FROM knowledge_base
WHERE tenant_id = $2
ORDER BY embedding <=> $1
LIMIT 5;
```

---

### R3: Mistral AI Integration for Intent Detection

**Decision**: Use Mistral AI via OpenAI-compatible API with structured output for intent classification

**Rationale**:
- Mistral supports OpenAI-compatible API, simplifying integration
- `mistral-large-latest` model handles complex reasoning and intent detection well
- Can use JSON mode for structured intent output

**Alternatives Considered**:
- OpenAI GPT-4: More expensive, not in current tech stack
- Local model (Llama): Would need GPU infrastructure, less reliable

**Intent Detection Prompt Structure**:
```
System: You are an AI assistant for a Shopify e-commerce brand.
Analyze the customer message and respond with:
1. intent: one of [question, refund_request, cancel_request, address_change_request, other]
2. confidence: float 0-1
3. sentiment: float -1 to 1
4. suggested_response: draft reply text (if intent is question)
5. suggested_action: {action_type, order_id, details} (if intent requires action)
6. needs_approval: boolean (true for any action, false for simple replies)

Customer message: {message}
Order context: {order_data if available}
Knowledge base results: {rag_results}
```

---

### R4: Email Handling for Incoming Ticket Processing

**Decision**: Phase 1 uses IMAP polling with async `aiomailbox`; Phase 2 can add webhook-based ingestion

**Rationale**:
- IMAP polling is simple and works with any email provider
- Async polling at 30-second intervals provides near real-time processing
- Existing `email_poller.py` in the codebase already implements this pattern

**Alternatives Considered**:
- Gmail Pub/Sub webhooks: Requires Google Cloud setup, only works for Gmail
- SendGrid Inbound Parse: Requires DNS changes, only works for SendGrid
- Microsoft Graph webhooks: Only for Outlook/Exchange

**Email Processing Flow**:
1. Poll inbox every 30 seconds via IMAP
2. Fetch new unread messages
3. Parse: extract from, subject, body, attachments
4. Check for order number in subject/body (regex pattern)
5. Create ticket in database
6. Trigger AI processing pipeline
7. Mark email as read/processed

**SMTP Sending (for replies)**:
- Use `aiosmtplib` for async email sending
- Include proper In-Reply-To and References headers for threading
- Add professional signature with ticket ID reference

---

## Summary of Key Decisions

| Area | Decision | Risk | Mitigation |
|------|----------|------|------------|
| Shopify API | REST with async httpx | API rate limits | Exponential backoff |
| Multi-tenant | RLS with tenant_id | Performance with many tenants | Proper indexing |
| AI | Mistral via OpenAI-compat API | Model availability | Fallback to simple rules |
| Email | IMAP polling | Delay in processing | 30s poll interval |
| Embeddings | pgvector on Supabase | Similarity accuracy | Tune embedding model |