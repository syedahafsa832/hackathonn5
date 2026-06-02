# Implementation Plan: AI Operations Cockpit Frontend

**Branch**: `003-ai-ops-cockpit` | **Date**: 2026-04-13 | **Spec**: `specs/003-ai-ops-cockpit/spec.md`

**Input**: Feature specification from `/specs/003-ai-ops-cockpit/spec.md`

## Summary

Build a unified AI Operations Cockpit frontend for Shopify support automation. The system transforms from fragmented dashboards into a real-time operations engine where users visualize: incoming customer events, AI decision-making, action proposals, execution results, and full lifecycle audit trails. All UI state derives from a unified event stream—no independent page-level data fetching.

## Technical Context

**Language/Version**: JavaScript (React 18) | **Primary Dependencies**: Tailwind CSS, Framer Motion, WebSocket client | **Storage**: N/A (frontend only) | **Testing**: Jest, React Testing Library | **Target Platform**: Web browser | **Project Type**: Single-page React application | **Performance Goals**: UI updates within 3 seconds, real-time event sync | **Constraints**: All state derived from /api/events, no independent API calls per page | **Scale/Scope**: 100+ events/hour, multi-tenant

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| VIII. Frontend Architecture - Event-Driven UI | ✅ PASS | All UI state from /api/events |
| VIII. Frontend Architecture - Single Source of Truth | ✅ PASS | No duplicate APIs, unified event stream |
| VIII. Frontend Architecture - Workflow-First | ✅ PASS | Operations Feed → Action Center → Execution Timeline |
| VIII. Frontend Architecture - Real-Time First | ✅ PASS | WebSocket/polling for instant updates |
| IX. Product Philosophy - No SaaS Dashboard | ✅ PASS | Operations cockpit, not admin panel |
| I. Code Quality | ✅ PASS | React best practices, clean component architecture |
| V. User Experience | ✅ PASS | One-click approvals, clear status indicators |

## Project Structure

### Documentation (this feature)

```text
specs/003-ai-ops-cockpit/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md             # Phase 2 output (/sp.tasks)
```

### Source Code (repository root)

```text
web-form/src/
├── components/
│   ├── EventStream/        # Real-time event display
│   ├── ActionCenter/        # Approval workflow
│   ├── ExecutionTimeline/  # Lifecycle visualization
│   └── common/             # Shared components
├── hooks/
│   ├── useEventStream.js   # WebSocket/polling for events
│   └── useEventById.js     # Single event fetching
├── context/
│   └── EventContext.js     # Global event state
├── services/
│   └── eventClient.js     # Single API client for /api/events
├── pages/
│   ├── OperationsFeed.jsx  # Main screen
│   ├── ActionCenter.jsx   # Approval queue
│   ├── ExecutionTimeline.jsx
│   └── Settings.jsx
└── App.js                  # Routes + event provider
```

**Structure Decision**: React SPA in `web-form/src/` - existing codebase reused, event-driven architecture with global event context as single source of truth.

## Complexity Tracking

> No complexity violations - follows constitution principles exactly.

---

## Phase 1: Design & Contracts

### Core Data Model (from /api/events)

**Unified Event Object**:
```typescript
interface UnifiedEvent {
  id: string;
  type: 'email_received' | 'ai_decision' | 'action_created' | 'action_approved' | 'execution_completed';
  timestamp: string;
  customer: {
    email: string;
    name?: string;
  };
  metadata: {
    channel: string;
    subject?: string;
    message_preview?: string;
    intent?: string;
    sentiment?: string;
    confidence?: number;
    decision?: string;
    action_type?: string;
    order_id?: string;
    risk_level?: string;
    execution_status?: string;
    error_details?: string;
  };
  lifecycle: {
    parent_event_id?: string;
    child_events: string[];
  };
}
```

### UI Structure

| Screen | Purpose | Data Source |
|--------|---------|-------------|
| Operations Feed | Real-time event stream, chronological | /api/events (WebSocket/polling) |
| Action Center | Pending approvals, order-level grouping | Filter: type=action_created, status=pending |
| Execution Timeline | Lifecycle per case, end-to-end trace | Filter: parent_event_id or lifecycle lookup |
| Settings | Minimal configuration | /api/v1/settings |

### API Contract

**GET /api/events**
- Query params: `?type=&status=&since=&limit=`
- Returns: `UnifiedEvent[]`

**GET /api/events/{id}**
- Returns: Single event with full lifecycle

**POST /api/actions/{id}/approve**
**POST /api/actions/{id}/reject**
- Approval workflow already exists in backend

### System Behavior

- All UI state derived from event stream
- No independent page-level data fetching
- UI updates triggered by event changes only (WebSocket or polling)
- Failed actions in separate "Failures" tab/lane

---

## Generated Artifacts

| File | Status |
|------|--------|
| `specs/003-ai-ops-cockpit/plan.md` | ✅ Created |
| `specs/003-ai-ops-cockpit/research.md` | ✅ (User provided architecture details) |
| `specs/003-ai-ops-cockpit/data-model.md` | ✅ (Included above) |
| `specs/003-ai-ops-cockpit/quickstart.md` | ⏳ Next |
| `specs/003-ai-ops-cockpit/contracts/` | ⏳ Next |

## Next Steps

Run `/sp.tasks` to generate task breakdown for implementation.