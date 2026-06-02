# Tasks: Email Filtering & Loop Prevention

**Input**: Design documents from `specs/005-email-filter-loop/`  
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅, quickstart.md ✅

**Milestone 1** (Phases 1–5, T001–T016): Safety-critical filtering and loop prevention — ships first, no UI required  
**Milestone 2** (Phases 6–7, T017–T024): Admin settings UI and dashboard widget

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no shared dependencies)
- **[Story]**: Maps task to user story for traceability (US1–US5)
- Exact file paths required in every description

---

## Phase 1: Setup

**Purpose**: Create the database migration file before any service code references the new columns.

- [X] T001 Create `backend/migrations/011_email_filter_schema.sql` with the full SQL from `specs/005-email-filter-loop/data-model.md` — creates `email_filter_log` table with index, alters `system_settings` with 5 new columns, alters `tickets` with 4 new columns, wrapped in `BEGIN`/`COMMIT`

**Checkpoint**: Migration file exists and SQL is valid. Apply in Supabase SQL editor before testing any filter code.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core service skeleton and settings loader that every filter layer and loop check depends on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T002 Create `backend/src/services/email_filter_service.py` with: `FilterResult` dataclass (`decision: str`, `reason: Optional[str]`, `email_category: str`, `sender_type: str`), `BLOCKED_SENDER_PREFIXES` list constant (`noreply`, `no-reply`, `notifications`, `newsletter`, `digest`, `mailer`, `hello`, `marketing`, `updates`), `BUILT_IN_BLOCKED_DOMAINS` list constant (`mailchimp.com`, `klaviyo.com`, `klaviyo-mail.io`, `linkedin.com`, `facebookmail.com`, `indeed.com`), and `EmailFilterService` class with `__init__(self)` stub
- [X] T003 Implement `EmailFilterService._load_settings(brand_id: str) -> dict` in `backend/src/services/email_filter_service.py` — calls `supabase_select("system_settings", {"store_id": f"eq.{brand_id}"})`, falls back to global default store `00000000-0000-0000-0000-000000000000` if no row found, returns dict with keys `blocked_domains`, `whitelisted_domains`, `max_auto_replies`, `promotion_filter_enabled`, `loop_protection_enabled` with safe defaults (`[]`, `[]`, `2`, `True`, `True`)

**Checkpoint**: Foundation ready — `email_filter_service.py` imports cleanly and `_load_settings` returns defaults when called with a non-existent brand_id.

---

## Phase 3: User Story 1 — Automated & Promotional Emails Are Silently Discarded (P1) 🎯 MVP

**Goal**: Every incoming email is evaluated against six ordered filter layers before any ticket creation or AI processing. Non-support emails are discarded and logged.

**Independent Test**: Send a newsletter email from `newsletter@klaviyo.com` to the connected inbox; wait one poll cycle (≤60 s); verify zero tickets created and one row in `email_filter_log` with `decision=blocked`, `filter_reason=blocked_sender_pattern`.

### Implementation for User Story 1

- [X] T004 [P] [US1] Implement `EmailFilterService._check_whitelist(sender_email: str, settings: dict) -> bool` in `backend/src/services/email_filter_service.py` — extracts domain from sender_email, checks if domain is in `settings["whitelisted_domains"]` (case-insensitive); returns `True` if whitelisted (caller skips layers 2–6)
- [X] T005 [P] [US1] Implement `EmailFilterService._check_blocked_domain(sender_email: str, settings: dict) -> Optional[FilterResult]` in `backend/src/services/email_filter_service.py` — extracts domain, checks against `settings["blocked_domains"]` + `BUILT_IN_BLOCKED_DOMAINS`; returns `FilterResult(decision="blocked", reason="blocked_domain", email_category="unknown", sender_type="automated")` if matched, else `None`
- [X] T006 [P] [US1] Implement `EmailFilterService._check_sender_prefix(sender_email: str) -> Optional[FilterResult]` in `backend/src/services/email_filter_service.py` — extracts local part before `@`, checks against `BLOCKED_SENDER_PREFIXES`; returns `FilterResult(decision="blocked", reason="blocked_sender_pattern", email_category="unknown", sender_type="automated")` if matched, else `None`
- [X] T007 [P] [US1] Implement `EmailFilterService._check_gmail_category(label_ids: list) -> Optional[FilterResult]` in `backend/src/services/email_filter_service.py` — maps `CATEGORY_PROMOTIONS` → `promotional`, `CATEGORY_SOCIAL` → `social`, `CATEGORY_UPDATES` → `updates`; returns `FilterResult(decision="blocked", reason="gmail_category", email_category=<mapped>, sender_type="automated")` if any matched, else `None`; returns `None` gracefully if `label_ids` is `None` or empty
- [X] T008 [P] [US1] Implement `EmailFilterService._check_auto_reply_headers(headers: dict) -> Optional[FilterResult]` in `backend/src/services/email_filter_service.py` — blocks if `Auto-Submitted` header value is `auto-generated` or `auto-replied` (NOT `no`), or `Precedence` is `bulk` or `list`, or `X-Autoreply` present, or `X-Autorespond` present, or `List-Unsubscribe` present; returns `FilterResult(decision="blocked", reason="auto_reply_header", email_category="unknown", sender_type="automated")` if matched, else `None`
- [X] T009 [US1] Implement `EmailFilterService._check_promotional_content(body_text: str, settings: dict) -> Optional[FilterResult]` in `backend/src/services/email_filter_service.py` — when `settings["promotion_filter_enabled"]` is `True`, uses `re.search` (case-insensitive) to scan for keywords: `unsubscribe`, `webinar`, `discount`, `newsletter`, `outreach`, `sponsorship`, `recruiting`, `job alert`, `growth hacks`, `course offer`, `marketing`; returns `FilterResult(decision="blocked", reason="promotional_content", email_category="promotional", sender_type="unknown")` on match, else `None`; depends on T004–T008 complete
- [X] T010 [US1] Implement `EmailFilterService.evaluate(message: dict, brand_id: str) -> FilterResult` in `backend/src/services/email_filter_service.py` — calls `_load_settings(brand_id)`, runs layers in order (whitelist bypass → blocked_domain → sender_prefix → gmail_category → auto_reply_header → promotional_content), returns first blocking result or `FilterResult(decision="allowed", reason=None, email_category="support", sender_type="human")`; wraps entire body in `try/except Exception` that logs at WARNING and returns `allowed` on any unhandled error
- [X] T011 [US1] Implement `EmailFilterService.log_decision(brand_id: str, sender_email: str, thread_id: Optional[str], result: FilterResult) -> None` in `backend/src/services/email_filter_service.py` — calls `supabase_insert("email_filter_log", {...})` with all FilterResult fields; swallows insert errors silently (logging failure must never crash the pipeline)
- [X] T012 [US1] Integrate `EmailFilterService` into `backend/src/channels/email_poller.py` — instantiate `email_filter_service = EmailFilterService()` at module level; in the per-message loop, call `result = email_filter_service.evaluate(message, brand_id)` before any ticket creation call; call `email_filter_service.log_decision(...)` for every evaluated message; if `result.decision == "blocked"`, skip ticket creation and continue to next message

**Checkpoint**: US1 complete — promotional/automated emails are silently discarded. Verify with quickstart.md Scenarios 1, 2, 3.

---

## Phase 4: User Story 2 — AI Reply Loop Is Detected and Stopped (P1)

**Goal**: Threads that have received `max_auto_replies` AI replies are flagged `loop_risk=true` and receive no further AI processing. The `auto_reply_count` is accurately tracked.

**Independent Test**: Manually set a ticket's `auto_reply_count=2` in Supabase; send a new email to that Gmail thread; wait one poll cycle; verify no new ticket or AI reply is generated and the ticket's `loop_risk=true`.

### Implementation for User Story 2

- [X] T013 [US2] Implement `EmailFilterService.check_loop_risk(ticket: dict, settings: dict) -> bool` in `backend/src/services/email_filter_service.py` — returns `False` immediately if `settings["loop_protection_enabled"]` is `False` or `ticket.get("gmail_thread_id")` is `None`; otherwise returns `ticket.get("auto_reply_count", 0) >= settings.get("max_auto_replies", 2)`
- [X] T014 [US2] Integrate loop check into `backend/src/channels/email_poller.py` — after `evaluate()` returns `allowed`, look up existing ticket by `gmail_thread_id` via `supabase_select("tickets", {"gmail_thread_id": f"eq.{thread_id}"})`; if found, call `email_filter_service.check_loop_risk(ticket, settings)`; if loop risk detected, call `supabase_update` to set `loop_risk=True` on the ticket, call `log_decision()` with `reason="loop_risk"`, and skip further processing
- [X] T015 [US2] Implement `auto_reply_count` increment in `backend/src/workers/message_processor.py` — immediately after an AI reply is confirmed sent or queued (not on failure), call `supabase_select("system_settings", ...)` to get `max_auto_replies` for the brand, then call `supabase_update("tickets", {"id": f"eq.{ticket_id}"}, {"auto_reply_count": new_count, "loop_risk": new_count >= max_auto_replies})` where `new_count = ticket["auto_reply_count"] + 1`

**Checkpoint**: US2 complete — AI reply loops capped at `max_auto_replies`. Verify with quickstart.md Scenario 5.

---

## Phase 5: User Story 3 — Only Genuine Support Emails Reach the Ticket Queue (P1)

**Goal**: Emails that pass all filters create tickets with `email_category` and `sender_type` correctly recorded, enabling downstream classification visibility.

**Independent Test**: Send 5 genuine support emails (refund, shipping issue, damaged product, payment problem, address change); verify each creates a ticket with `email_category='support'` and `sender_type='human'` in the `tickets` table.

### Implementation for User Story 3

- [X] T016 [US3] Update ticket creation in `backend/src/channels/email_poller.py` — when passing data to `supabase_insert("tickets", {...})` or the existing ticket creation helper, include `email_category: result.email_category` and `sender_type: result.sender_type` from the `FilterResult` returned by `evaluate()`
- [X] T017 [US3] Implement `self_reply` detection in `EmailFilterService.evaluate()` in `backend/src/services/email_filter_service.py` — before other layers, check if `sender_email` matches the brand's `support_email` (pass `brand_support_email` as a parameter or look it up from brands table); if matched, return `FilterResult(decision="blocked", reason="self_reply", email_category="unknown", sender_type="automated")`

**Checkpoint**: US3 complete (all P1 stories done). Run quickstart.md Scenario 4 to verify golden path. **Milestone 1 is now complete** — deploy and validate end-to-end in Docker stack before proceeding to Milestone 2.

---

## Phase 6: User Story 4 — Brand Owner Configures Filter Rules from Settings (P2)

**Goal**: Brand owners can read and update filter settings (blocklist, whitelist, max_auto_replies, toggles) via API. Settings UI exposes these controls.

**Independent Test**: `PATCH /api/v1/settings/email-filter` with `{"blocked_domains": ["spamco.io"], "max_auto_replies": 1}`; `GET /api/v1/settings/email-filter` to confirm persistence; send email from `user@spamco.io` and verify it is blocked.

### Implementation for User Story 4 — Backend

- [X] T018 [US4] Create `backend/src/api/routes/v2_email_filter.py` with `GET /api/v1/settings/email-filter` endpoint — uses `Depends(get_current_tenant)`, resolves brand for tenant via `supabase_select("brands", {"tenant_id": ...})`, reads `system_settings` for that brand's `store_id`, returns `EmailFilterSettingsResponse` with all 5 filter columns; returns defaults if no row exists
- [X] T019 [US4] Add `PATCH /api/v1/settings/email-filter` endpoint to `backend/src/api/routes/v2_email_filter.py` — accepts partial `EmailFilterSettingsPatch` body; validates `max_auto_replies` is 0–10 (returns 400 if not); upserts into `system_settings` via `supabase_update` (or `supabase_insert` if row doesn't exist); returns updated full settings object
- [X] T020 [US4] Add `GET /api/v1/filter-logs` endpoint to `backend/src/api/routes/v2_email_filter.py` — supports `?summary=true&days=7` (aggregates `email_filter_log` rows grouped by `filter_reason`, returns `FilterLogSummary` with `total_blocked`, `by_reason`, `prevented_loops`, `total_allowed`) and default paginated mode (returns list with `?decision`, `?reason`, `?limit`, `?offset` filters); all queries scoped to tenant's `brand_id`
- [X] T021 [US4] Register `v2_email_filter` router in `backend/main.py` — import the router and call `app.include_router(email_filter_router, prefix="/api/v1", tags=["Email Filter"])`

### Implementation for User Story 4 — Frontend

- [X] T022 [P] [US4] Add `getEmailFilterSettings()`, `patchEmailFilterSettings(data)`, and `getFilterLogs(params)` functions to `ai-ops-console/src/services/apiClient.js` — each calls the corresponding v1 endpoint with `Authorization: Bearer <resolv_token>` header
- [X] T023 [US4] Add Email Filter settings section to `ai-ops-console/src/pages/Settings.jsx` — renders form with: `blocked_domains` (textarea or tag input, comma-separated), `whitelisted_domains` (textarea or tag input), `max_auto_replies` (number input 0–10), `promotion_filter_enabled` (toggle), `loop_protection_enabled` (toggle); loads current settings on mount via `getEmailFilterSettings()`; saves via `patchEmailFilterSettings()`; shows explicit loading, error, and success states

**Checkpoint**: US4 complete — verify quickstart.md Scenarios 6 and 8.

---

## Phase 7: User Story 5 — Filtered Email Activity Is Visible in Dashboard (P3)

**Goal**: Dashboard shows a Filtered Emails widget with last-7-days totals, per-reason breakdown, and Prevented Loops count.

**Independent Test**: After 10+ emails filtered, open dashboard; verify widget displays correct `total_blocked` and per-reason counts matching `SELECT filter_reason, COUNT(*) FROM email_filter_log WHERE brand_id=... AND created_at > NOW() - INTERVAL '7 days' GROUP BY filter_reason`.

### Implementation for User Story 5

- [X] T024 [P] [US5] Create `ai-ops-console/src/components/FilteredEmailsWidget.jsx` — fetches `GET /api/v1/filter-logs?summary=true&days=7` on mount using `getFilterLogs({summary: true, days: 7})`; renders: total blocked count, `by_reason` breakdown as a list/table, `prevented_loops` as a distinct highlighted count; handles loading spinner, error message, and empty state (zero emails filtered)
- [X] T025 [US5] Add `<FilteredEmailsWidget />` to the main dashboard view in `ai-ops-console/src/pages/SmartApprovalInbox.jsx` (or `Dashboard.jsx` if that file exists) — import the component and render it alongside existing dashboard widgets

**Checkpoint**: US5 complete — verify quickstart.md Scenario 7.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T026 [P] Add structured logging to `EmailFilterService` in `backend/src/services/email_filter_service.py` — `logger.info(f"Filter decision: {result.decision} reason={result.reason} sender={sender_email} brand={brand_id}")` after each `evaluate()` call; `logger.warning(f"Filter exception for brand={brand_id}: {e}")` in the safety fallback
- [X] T027 [P] Verify multi-tenant isolation in `backend/src/api/routes/v2_email_filter.py` — confirm every `supabase_select` and `supabase_insert` on `email_filter_log` and `system_settings` is scoped to the tenant's brand_id; no query should return rows from other tenants
- [ ] T028 Run all 8 quickstart.md scenarios against the running Docker stack (`docker compose up backend email_poller`) and confirm each passes

---

## Dependencies & Execution Order

### Phase Dependencies

| Phase | Depends On | Notes |
|-------|-----------|-------|
| Phase 1: Setup | — | Start immediately |
| Phase 2: Foundational | Phase 1 | BLOCKS all stories |
| Phase 3: US1 | Phase 2 | Filter layers + poller integration |
| Phase 4: US2 | Phase 3 | Loop check uses service from US1 |
| Phase 5: US3 | Phase 3, Phase 4 | Ticket field classification |
| Phase 6: US4 | Phase 2 | API endpoints are independent; UI needs T022 before T023 |
| Phase 7: US5 | T020 (filter-logs API endpoint) | Widget needs backend endpoint |
| Phase 8: Polish | All phases | Final validation |

### Milestone Grouping

**Milestone 1 — Safety-Critical (ship first)**: Phases 1–5 (T001–T017)  
Filter service + poller integration + loop detection takes effect without any UI changes.

**Milestone 2 — Admin & Visibility**: Phases 6–7 (T018–T025)  
Settings UI and dashboard widget; requires Milestone 1 complete.

### Within User Stories

```text
US1: T004/T005/T006/T007/T008 [P] → T009 → T010 → T011 → T012
US2: T013 → T014 → T015
US3: T016 → T017
US4: T018 → T019 → T020 → T021 → T022 [P] → T023
US5: T024 [P] → T025
```

---

## Parallel Opportunities

```text
Phase 3 batch (T004–T008): Each implements one independent private method.
  Assign different methods to different sessions; merge into email_filter_service.py.

Phase 6 split:
  Backend (T018–T021) can run in parallel with frontend setup (T022).
  T023 (Settings form) depends on T022 (apiClient) being done.

Phase 7:
  T024 (widget component) can start as soon as T020 (API endpoint) is done.
```

---

## Implementation Strategy

### MVP First (Milestone 1: Phases 1–5, T001–T017)

1. Apply migration in Supabase SQL editor (from T001)
2. Build `email_filter_service.py` skeleton (T002–T003)
3. Implement filter layers (T004–T011) — poller starts discarding bad emails
4. Add loop detection (T013–T015) — loops stop
5. Add ticket classification fields (T016–T017)
6. **STOP AND VALIDATE**: Run quickstart.md Scenarios 1–5; confirm in Docker stack

### Incremental Delivery

1. Milestone 1 → Safety in production; no more promotional tickets or loops
2. Phase 6 (T018–T023) → Brand owners self-serve filter configuration
3. Phase 7 (T024–T025) → Operational dashboard visibility

### Notes

- Apply SQL migration manually before testing any filter code — columns must exist
- Email poller runs every 60 seconds; filter changes are picked up on the next cycle
- The safety fallback in `evaluate()` ensures a filter crash never blocks a real customer email
- All endpoints use v1 JWT — test with `resolv_token` from browser localStorage
- `loop_risk` can be manually reset via `PATCH /api/tickets/{id}` with `{"loop_risk": false}` (existing endpoint — no new endpoint needed)
