# Implementation Plan: Email Filtering & Loop Prevention

**Branch**: `005-email-filter-loop` | **Date**: 2026-05-15 | **Spec**: specs/005-email-filter-loop/spec.md  
**Input**: Feature specification from `/specs/005-email-filter-loop/spec.md`

## Summary

Add a synchronous email filter service that runs inside the existing email poller before ticket creation or AI processing. The service evaluates every incoming Gmail message against six filter layers (Gmail category labels, sender-pattern matching, blocked-domain checks, auto-reply headers, promotional-content keywords, and per-thread reply-count limits) and discards non-support emails before they reach the pipeline. Loop prevention tracks `auto_reply_count` per thread and stops AI replies once a configurable threshold is reached. All filter decisions are logged to a new append-only `email_filter_log` table. Brand owners configure filter rules via new Settings API endpoints; the dashboard gains a Filtered Emails widget backed by aggregated log queries.

---

## Technical Context

**Language/Version**: Python 3.11 (backend), React 18 (ai-ops-console)  
**Primary Dependencies**: FastAPI, python-jose (JWT), supabase REST client helpers (`supabase_select`, `supabase_insert`, `supabase_update`) — all already in `requirements.txt`; no new packages  
**Storage**: PostgreSQL via Supabase — new table `email_filter_log`, extended `system_settings` and `tickets` (see `data-model.md`)  
**Testing**: pytest (unit), manual end-to-end via Gmail test inbox  
**Target Platform**: Linux server (Docker container — `backend` and `email_poller` services)  
**Project Type**: Web application — FastAPI backend + React admin UI  
**Performance Goals**: Filter evaluation < 5 ms per email (pure heuristic, no network calls); filter log insert < 100 ms; dashboard widget query < 500 ms  
**Constraints**: No new Python packages. No new npm packages. Filtering must be synchronous in the poller — no message queues or background jobs. All endpoints use v1 JWT auth (`get_current_tenant`). Must not break existing ticket creation or AI reply flow for emails that pass the filter.  
**Scale/Scope**: Single email channel (Gmail). Per-brand/tenant filter config. Filter log retained 30 days.

---

## Constitution Check

| Gate | Status | Notes |
|------|--------|-------|
| **X. Auth Integrity** — v1 JWT only | ✅ PASS | All new endpoints use `Depends(get_current_tenant)` |
| **XI. Dependency Minimalism** — no new packages | ✅ PASS | `re`, `json`, `logging` (stdlib); Gmail labels already in API response |
| **XI. API-First** — backend before UI | ✅ PASS | Filter settings endpoints in Milestone 1; UI in Milestone 2 |
| **XI. No mock data** | ✅ PASS | All UI components will query real `/api/v1/settings/email-filter` and `/api/v1/filter-logs` endpoints |
| **XII. Feature Stability** — no breaking changes | ✅ PASS | Filter inserted before ticket creation, no modifications to existing ticket/message-processor logic except `auto_reply_count` increment |
| **II. Multi-Tenant Security** | ✅ PASS | All filter log queries scoped to `brand_id` derived from tenant JWT |
| **IV. AI Behavior Standards** | ✅ PASS | Loop prevention reinforces confidence gating — loop-risk threads cannot trigger AI replies |
| **VIII. Error States** | ✅ PASS | Filter service returns explicit `FilterResult`; any exception falls back to `allowed` to avoid blocking real customer emails |

**Post-design re-check**: No violations. Filter service is a pure function with no external dependencies added.

---

## Project Structure

### Documentation (this feature)

```text
specs/005-email-filter-loop/
├── plan.md              ← This file
├── research.md          ← Phase 0 output (complete)
├── data-model.md        ← Phase 1 output (complete)
├── quickstart.md        ← Phase 1 output (complete)
├── contracts/
│   └── email-filter-openapi.yaml  ← Phase 1 output (complete)
└── tasks.md             ← Phase 2 output (/sp.tasks — not yet generated)
```

### Source Code (new files)

```text
backend/
├── migrations/
│   └── 011_email_filter_schema.sql    [NEW] DB migration
└── src/
    ├── services/
    │   └── email_filter_service.py    [NEW] Core filter + loop prevention logic
    └── api/
        └── routes/
            └── v2_email_filter.py     [NEW] GET/PATCH /api/v1/settings/email-filter
                                             GET /api/v1/filter-logs
```

### Source Code (modified files)

```text
backend/
└── src/
    ├── channels/
    │   └── email_poller.py            [MODIFY] Call filter service before ticket creation
    ├── workers/
    │   └── message_processor.py       [MODIFY] Increment auto_reply_count after AI send
    └── main.py                        [MODIFY] Register v2_email_filter router

ai-ops-console/
└── src/
    ├── pages/
    │   ├── Settings.jsx               [MODIFY] Add Email Filter tab with settings form
    │   └── Dashboard.jsx (or index)   [MODIFY] Add Filtered Emails widget
    └── services/
        └── apiClient.js               [MODIFY] Add filter settings + logs API calls
```

---

## Complexity Tracking

No constitution violations. No complexity justification required.

---

## Implementation Strategy

### Milestone 1 — Safety-Critical Core (P1 user stories)

All P1 requirements must ship together. A partial filter that blocks some emails but not others is worse than no filter (inconsistent safety boundary).

**Order of implementation**:

1. **Migration** (`011_email_filter_schema.sql`) — Run first; all other code depends on the new columns.
2. **`email_filter_service.py`** — Pure Python service; no FastAPI dependency; can be unit-tested in isolation.
   - `FilterResult` dataclass: `{ decision: "allowed"|"blocked", reason: str|None, email_category: str, sender_type: str }`
   - `EmailFilterService.evaluate(message, settings) -> FilterResult`
   - Six filter layers in priority order:
     1. Whitelist domain bypass (if sender domain in `whitelisted_domains` → skip layers 2–5)
     2. Blocked domain check (sender domain in `blocked_domains` or built-in list → `blocked_domain`)
     3. Sender prefix check (`noreply@`, `newsletter@`, etc. → `blocked_sender_pattern`)
     4. Gmail category label check (`CATEGORY_PROMOTIONS`, `CATEGORY_SOCIAL`, `CATEGORY_UPDATES` → `gmail_category`)
     5. Auto-reply header check (`Auto-Submitted`, `Precedence: bulk/list`, `X-Autoreply`, `X-Autorespond`, `List-Unsubscribe` → `auto_reply_header`)
     6. Promotional content keyword scan (when `promotion_filter_enabled=True` → `promotional_content`)
   - `EmailFilterService.check_loop_risk(ticket, settings) -> bool`
   - `EmailFilterService.log_decision(brand_id, sender, thread_id, result)` — inserts into `email_filter_log`
3. **`email_poller.py` integration** — Inject filter call after Gmail message fetch, before `_create_ticket_from_email` / `message_processor.process_message`. If `FilterResult.decision == "blocked"`, log and skip. If `decision == "allowed"` but `loop_risk`, log and skip. Pass `email_category` and `sender_type` when creating ticket.
4. **`message_processor.py` increment** — After confirmed AI reply send, call `supabase_update` to increment `auto_reply_count` and set `loop_risk = (new_count >= max_auto_replies)`.
5. **Filter settings endpoints** (`v2_email_filter.py`) — `GET` returns current settings from `system_settings`; `PATCH` validates and updates. Register in `main.py`.

### Milestone 2 — Admin Configuration & Dashboard Visibility (P2/P3)

Only after Milestone 1 is verified end-to-end:

6. **Settings UI tab** — React form in `ai-ops-console/src/pages/Settings.jsx` for blocklist, whitelist, max-replies, toggles. Backed by the Milestone 1 endpoints.
7. **Dashboard widget** — Filtered Emails widget showing last-7-days breakdown by reason + prevented-loops count. Backed by `GET /api/v1/filter-logs?summary=true`.

### Fallback behaviour

If `email_filter_service.py` raises an unhandled exception during evaluation:
- Log the error at WARNING level
- Return `FilterResult(decision="allowed", reason=None, ...)` — never block a real customer email due to a filter crash
- The exception is not re-raised; the poller continues normally

### Loop-risk manual reset

`PATCH /api/tickets/{ticket_id}` (existing endpoint) with `{ "loop_risk": false }` resets the flag. No new endpoint required — the existing ticket PATCH already handles field updates.

---

## Key Decisions (from research.md)

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | Filter runs synchronously in poller | Guarantees filtering before ticket creation; no async pipeline complexity |
| 2 | Gmail labels from existing `labelIds` field | No extra API calls; already in `messages.get` response |
| 3 | Filter config in `system_settings` columns | Avoids new table; already per-brand scoped |
| 4 | New `email_filter_log` table | Independent queryability for dashboard; no FK to tickets |
| 5 | Loop detection in poller; count increment in processor | Poller stops processing; processor knows actual send outcome |
| 6 | No new packages | stdlib `re`/`json`/`logging` sufficient |
| 7 | v1 JWT on all new endpoints | Constitution Principle X — no auth mixing |
| 8 | Whitelist bypasses sender+category checks only, not headers | Headers describe the message, not the sender identity |
