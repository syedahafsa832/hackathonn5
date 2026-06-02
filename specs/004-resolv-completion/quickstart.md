# Quickstart: Resolv MVP Completion

**Branch**: `004-resolv-completion` | **Date**: 2026-05-13

## Prerequisites

- Docker + Docker Compose running
- Both projects can run simultaneously (hack5 on ports 8001, 6380; meershakeel on 8000, 6379)
- Node 18+ for running the admin UI locally

## Start the Backend

```bash
# From E:\hack5\hack5
docker compose up -d
```

API available at `http://localhost:8001`. Health check: `GET http://localhost:8001/health`

## Start the Admin UI (Dev Mode)

```bash
cd ai-ops-console
npm run dev
# Opens at http://localhost:5173
```

Env file at `ai-ops-console/.env` — `VITE_API_BASE_URL=http://localhost:8001`

## Verify Auth Works (Item A)

```bash
# Login and get token
curl -X POST http://localhost:8001/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"your@email.com","password":"yourpassword"}'

# Use token on any protected route
curl http://localhost:8001/api/v1/actions/stats \
  -H "Authorization: Bearer <token>"
```

Expected: 200 response with action stats (not 401).

## Verify Dashboard Stats (Item B)

1. Submit a test ticket via `POST http://localhost:8001/support/submit`
2. Log in to `http://localhost:5173/login`
3. Dashboard should show Active Conversations ≥ 1

## Verify Ticket Fields (Item C)

```bash
curl http://localhost:8001/api/tickets
```

Expected: Each ticket object includes `channel` (non-null) and `customer_email`.

## Verify Worker Is Stable (Item D)

```bash
docker compose logs email_poller --follow
```

Expected: Continuous log output with no process exit. Errors are logged and polling continues.

## Verify Email Send (Item E)

1. Log in, open a ticket with an AI draft
2. Click "Approve & Send"
3. Expected: Toast "Email sent successfully" and ticket status → "resolved"

## Verify Knowledge Base UI (Item F)

1. Log in → Settings → Knowledge Base tab
2. Enter a title and paste some text → Upload
3. Source appears in list with Delete button

## Verify Onboarding (Item G)

1. Register a new account (no brands connected)
2. Navigate to `/dashboard`
3. Expected: 3-step onboarding wizard appears, not empty dashboard

## Verify Email Threading (Item H)

1. Submit ticket from customer email address
2. Approve and send AI reply
3. Reply to that email from the customer address
4. Check admin console: reply appears inside original ticket, not as new ticket

## Verify Browser Notifications (Item I)

1. Log in to admin console, allow notification permission when prompted
2. Submit a ticket from another browser tab / curl
3. Expected: OS notification appears within 10 seconds

## Implementation Order

Strictly follow this order — each item unblocks the next:

```
1. Fix auth middleware          (backend/src/api/middleware/auth_middleware.py)
2. Fix background worker        (backend/src/channels/email_poller.py)
3. Fix ticket list API          (backend/src/api/routes/tickets.py)
4. Fix dashboard stats          (ai-ops-console/src/api/services.js)
5. Fix email send               (backend/src/api/routes/v2_tickets.py)
6. Build knowledge base UI      (ai-ops-console/src/pages/Settings.jsx)
7. Build onboarding wizard      (ai-ops-console/src/pages/Dashboard.jsx)
8. Fix email threading          (backend/src/channels/email_poller.py)
9. Add browser notifications    (ai-ops-console/src/pages/Dashboard.jsx)
```
