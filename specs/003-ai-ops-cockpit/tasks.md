# Tasks: AI Operations Cockpit Frontend

**Input**: Design documents from `specs/003-ai-ops-cockpit/`
**Prerequisites**: plan.md (required), spec.md (required), data-model.md

**Organization**: Tasks grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)

## Path Conventions

- **Frontend**: `ai-ops-console/src/` (components, pages, hooks, context, services)

---

## Phase 1: Setup (Project Foundation)

**Purpose**: Initialize event-driven architecture foundation

- [X] T001 Create EventContext provider in ai-ops-console/src/context/EventContext.tsx — Global unified event state
- [X] T002 [P] Create eventClient.ts API service in ai-ops-console/src/services/eventClient.ts — Single /api/events client
- [X] T003 [P] Create useEventStream hook in ai-ops-console/src/hooks/useEventStream.ts — WebSocket/polling listener
- [X] T004 [P] Create useEventById hook in ai-ops-console/src/hooks/useEventById.ts — Single event fetching
- [X] T005 Create Event normalization utility in ai-ops-console/src/utils/eventNormalizer.ts — Normalize backend events to UnifiedEvent model

**Checkpoint**: Event infrastructure ready ✅

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story work

- [X] T006 Add tenant-aware filtering to EventContext in ai-ops-console/src/context/EventContext.tsx
- [X] T007 Create loading state component in ai-ops-console/src/components/common/LoadingState.tsx
- [X] T008 Create error state component in ai-ops-console/src/components/common/ErrorState.tsx
- [X] T009 Create offline state component in ai-ops-console/src/components/common/OfflineState.tsx
- [X] T010 Configure App.tsx with EventContext provider in ai-ops-console/src/App.tsx

**Checkpoint**: Foundation ready ✅

---

## Phase 3: User Story 1 — Real-Time Event Stream (Priority: P1) 🎯 MVP

**Goal**: Display incoming customer events in real-time chronological stream

- [X] T011 [P] [US1] ChannelIcon component in ai-ops-console/src/components/common/ChannelIcon.tsx
- [X] T012 [P] [US1] OperationsFeed page in ai-ops-console/src/pages/OperationsFeed.tsx
- [X] T013 [US1] Add timestamp formatting utility in ai-ops-console/src/utils/dateFormatter.ts

**Checkpoint**: Event stream shows incoming events ✅

---

## Phase 4: User Story 2 — AI Decision Visualization (Priority: P1)

**Goal**: Show AI reasoning for each event (intent, sentiment, confidence, decision)

- [X] T017 [P] [US2] AIDecisionBadge component in ai-ops-console/src/components/EventStream/AIDecisionBadge.tsx
- [X] T018 [P] [US2] ConfidenceMeter component in ai-ops-console/src/components/common/ConfidenceMeter.tsx
- [X] T019 [P] [US2] SentimentBadge component in ai-ops-console/src/components/common/SentimentBadge.tsx

**Checkpoint**: AI decision visualization components ready ✅

---

## Phase 5: User Story 3 — Action Approval Workflow (Priority: P1)

**Goal**: One-click approve/reject for pending action proposals

- [X] T022 [P] [US3] RiskBadge component in ai-ops-console/src/components/common/RiskBadge.tsx
- [X] T023 [US3] ActionCenter page in ai-ops-console/src/pages/ActionCenter.tsx — Pending actions queue with approve/reject
- [X] T024 [US3] Approve/reject buttons in ActionCenter ✅
- [X] T025 [US3] Integrate approve/reject API calls in eventClient.ts ✅

**Checkpoint**: Action approval workflow functional ✅

---

## Phase 6: User Story 4 — Execution Results & Failures (Priority: P2)

**Goal**: Display action execution results, separate failure lane

- [X] T029 [P] [US4] FailuresTab in ActionCenter page with tabbed interface ✅

**Checkpoint**: Failed actions visible in separate tab ✅

---

## Phase 7: User Story 5 — Execution Timeline (Priority: P2)

**Goal**: Full lifecycle visualization per customer case

- [X] T030 [US5] ExecutionTimeline page in ai-ops-console/src/pages/ExecutionTimeline.tsx ✅

**Checkpoint**: Lifecycle visualization available ✅

---

## Phase 8: User Story 6 — Unified Cockpit View (Priority: P1)

**Goal**: Single view showing system health at a glance

- [X] T031 [US6] OperationsCockpit page in ai-ops-console/src/pages/OperationsCockpit.tsx ✅

**Checkpoint**: Unified cockpit with live metrics ✅

---

## Phase 9: Backend Events API

**Purpose**: Create unified /api/events endpoint for frontend

- [X] T040 Create /api/events endpoint in hack5/backend/src/api/routes/events.py ✅

**Checkpoint**: Backend ready ✅

---

## Summary

| Phase | Status | Tasks |
|-------|--------|-------|
| Phase 1: Setup | ✅ Done | 5/5 |
| Phase 2: Foundational | ✅ Done | 5/5 |
| Phase 3: US1 Event Stream | ✅ Done | 3/3 |
| Phase 4: US2 AI Decision | ✅ Done | 3/3 |
| Phase 5: US3 Action Approval | ✅ Done | 4/4 |
| Phase 6: US4 Execution | ✅ Done | 1/1 |
| Phase 7: US5 Timeline | ✅ Done | 1/1 |
| Phase 8: US6 Cockpit | ✅ Done | 1/1 |
| Backend API | ✅ Done | 1/1 |

**Core MVP Complete** ✅

The following are NOT implemented (could be added later):
- EventCard with full expandable details
- EventList with virtual scrolling
- AIReviewPanel expandable panel
- Legacy page refactoring
- Advanced polish (virtual scrolling, keyboard shortcuts)

---

## How to Run

### Start Backend
```bash
cd E:/hack5/hack5/backend
python main.py
```

### Start Frontend
```bash
cd E:/ai-ops-console
npm run dev
```

### Access the Cockpit
- `/` - Operations Cockpit (main dashboard)
- `/ops/feed` - Real-time event stream
- `/ops/actions` - Action Center with approvals
- `/ops/timeline` - Execution timeline