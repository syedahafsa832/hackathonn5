# Quickstart: AI Operations Cockpit Frontend

## Prerequisites

- Node.js 18+
- React 18+ project at `web-form/`
- Backend running with `/api/events` endpoint

## Running the Frontend

```bash
cd web-form
npm start
```

Opens at `http://localhost:3000`

## Testing Scenarios

### Scenario 1: View Event Stream

1. Submit a test email to the support address
2. Navigate to Operations Feed (main screen)
3. Verify event appears within 3 seconds
4. Click event to expand details

### Scenario 2: Approve an Action

1. Send email requesting refund
2. Wait for AI decision → action_created event
3. Navigate to Action Center
4. Click "Approve" on the pending action
5. Verify execution_completed event with success

### Scenario 3: View Execution Failures

1. Trigger a failed action (e.g., invalid order)
2. Navigate to Action Center → Failures tab
3. Verify failure details visible

### Scenario 4: Trace Full Lifecycle

1. Find any completed event
2. Click to expand full lifecycle
3. Verify timeline shows all stages: email → ai_decision → action → approval → execution

## Development Commands

```bash
# Run tests
npm test

# Build for production
npm run build

# Lint
npm run lint
```

## Key Files

| File | Purpose |
|------|---------|
| `web-form/src/context/EventContext.js` | Global event state provider |
| `web-form/src/hooks/useEventStream.js` | WebSocket/polling for events |
| `web-form/src/services/eventClient.js` | API client for /api/events |
| `web-form/src/pages/OperationsFeed.jsx` | Main event stream view |
| `web-form/src/pages/ActionCenter.jsx` | Approval workflow |