# Research: AI Operations Cockpit Frontend

## Decisions Made

### Architecture Pattern: Event-Driven Single Source of Truth

**Decision**: All UI state derived from `/api/events` with no independent page-level data fetching.

**Rationale**: Matches Constitution VIII (Frontend Architecture) principles - event-driven UI only, single source of truth, workflow-first design. Prevents duplicate data, ensures real-time consistency.

**Alternatives Considered**:
- Page-level API fetching (rejected - violates single source of truth)
- Central Redux store with separate API calls (rejected - adds complexity without benefit)
- WebSocket-only updates (rejected - polling as fallback needed for reliability)

### Real-Time Update Strategy: WebSocket with Polling Fallback

**Decision**: Use WebSocket for primary real-time updates, with polling (5-second interval) as fallback.

**Rationale**: WebSocket provides instant updates. Polling fallback ensures reliability when WebSocket disconnects. Matches Constitution VI (Performance) - real-time first.

**Alternatives Considered**:
- WebSocket only (rejected - connection drops break UX)
- Long-polling only (rejected - less efficient than WebSocket)
- Server-Sent Events (rejected - less widely supported than WebSocket)

### Event Display: Contextual with Expansion

**Decision**: Show basic event info (channel, customer, timestamp) with expandable details for full lifecycle data.

**Rationale**: User clarification response - contextual display with lifecycle metadata. Balances information density with clarity.

### Failed Actions: Separate Lane/Tab

**Decision**: Failed actions shown in separate "Failures" lane for visibility.

**Rationale**: User clarification response. Ensures failures are prominently visible and actionable.

### Action Grouping: Order-Level

**Decision**: Group pending actions by order in Action Center.

**Rationale**: Simplifies approval workflow - one approval handles entire order context.

---

## Best Practices Applied

- React 18 with hooks for event state management
- Tailwind CSS for styling (existing in codebase)
- Framer Motion for transitions (existing in codebase)
- Global EventContext for single source of truth
- Custom hooks for event stream subscription