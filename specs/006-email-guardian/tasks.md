# Tasks: Email Guardian — AI Classification + Confidence Gate

**Input**: Design documents from `specs/006-email-guardian/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅, quickstart.md ✅

**Milestone 1** (Phases 1–4, T001–T012): Safety-critical AI filter + support-only mode — ships without UI
**Milestone 2** (Phases 5–6, T013–T019): Quarantine Queue API + operator UI + settings controls
**Milestone 3** (Phase 7, T020–T021): Dashboard analytics update

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no shared dependencies)
- **[Story]**: Maps task to user story (US1–US5)
- Exact file paths required in every description

---

## Phase 1: Setup

**Purpose**: Create the migration file before any service code references the new columns.

- [X] T001 Create `backend/migrations/012_email_guardian_schema.sql` — `BEGIN`/`COMMIT` transaction with: (1) `ALTER TABLE email_filter_log ADD COLUMN IF NOT EXISTS ai_classification TEXT, ADD COLUMN IF NOT EXISTS ai_confidence FLOAT`; (2) `ALTER TABLE system_settings ADD COLUMN IF NOT EXISTS support_only_mode BOOLEAN DEFAULT true, ADD COLUMN IF NOT EXISTS confidence_threshold FLOAT DEFAULT 0.75, ADD COLUMN IF NOT EXISTS auto_reply_enabled BOOLEAN DEFAULT true`; (3) `CREATE TABLE IF NOT EXISTS email_quarantine` with all columns from data-model.md (id, brand_id, sender_email, subject, body_preview, thread_id, ai_classification, ai_confidence, status CHECK IN pending/promoted/discarded/expired, actioned_by, actioned_at, expires_at DEFAULT now()+7days, created_at) plus `CREATE INDEX idx_email_quarantine_brand_status ON email_quarantine (brand_id, status, created_at DESC)`

**Checkpoint**: Migration file valid. Apply in Supabase SQL editor before running any guardian code.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Guardian service skeleton and settings loader that every filter layer depends on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T002 Create `backend/src/services/email_guardian_service.py` with: `GuardianResult` dataclass (`decision: str` — "allowed"/"blocked"/"quarantined", `classification: str`, `confidence: float`, `reason: Optional[str]`, `quarantine_id: Optional[str]`, `auto_reply_enabled: bool`); `GUARDIAN_ALLOW = GuardianResult(decision="allowed", classification="customer_support", confidence=1.0, reason=None, quarantine_id=None, auto_reply_enabled=True)`; `EmailGuardianService` class stub with `__init__(self)`; module-level singleton `email_guardian_service = EmailGuardianService()`; imports for `logging`, `re`, `os`, `dataclasses`, `typing`, `openai.OpenAI`, `src.lib.supabase_client`
- [X] T003 Implement `EmailGuardianService._load_settings(brand_id: str) -> dict` in `backend/src/services/email_guardian_service.py` — calls `supabase_select("system_settings", {"store_id": f"eq.{brand_id}"})`, falls back to global default store `00000000-0000-0000-0000-000000000000` if no brand-specific row, returns dict with keys: `support_only_mode` (bool, default True), `confidence_threshold` (float, default 0.75), `auto_reply_enabled` (bool, default True); swallows all exceptions returning defaults
- [X] T004 Extend `_get_filter_settings()` defaults and loader in `backend/src/api/routes/v2_email_filter.py` — add three new keys to the `defaults` dict: `"support_only_mode": True`, `"confidence_threshold": 0.75`, `"auto_reply_enabled": True`; load them from the `system_settings` row using `.get()` with the same pattern as existing fields; this ensures `GET /settings/email-filter` returns the new fields even before the Pydantic model is updated

**Checkpoint**: `email_guardian_service.py` imports cleanly; `_load_settings` returns defaults for unknown brand_id.

---

## Phase 3: User Story 1 — AI Classification Blocks Non-Support Emails (Priority: P1) 🎯 MVP

**Goal**: Every email that passes Layers 1–3 is classified by the Mistral AI before ticket creation. Non-support emails are silently discarded and logged.

**Independent Test**: Send a LinkedIn outreach email from a non-blocklisted address; wait one poll cycle; verify no ticket, and `email_filter_log` row has `ai_classification='outreach'`, `filter_reason='ai_classification'`.

### Implementation for User Story 1

- [X] T005 [P] [US1] Implement `EmailGuardianService._classify_email(subject: str, body: str) -> tuple[str, float]` in `backend/src/services/email_guardian_service.py` — initializes `OpenAI(api_key=os.getenv("MISTRAL_API_KEY"), base_url=os.getenv("MISTRAL_API_BASE_URL", "https://api.mistral.ai/v1"))` (same pattern as `customer_success_agent.py:38-41`); builds prompt asking to classify the email into one of: customer_support/promotion/newsletter/outreach/spam/automation/unknown and return JSON `{"classification": "<label>", "confidence": <0.0-1.0>}`; calls `chat.completions.create(model=os.getenv("MISTRAL_MODEL","mistral-large-latest"), messages=[...], temperature=0.0, max_tokens=80)`; first tries with `response_format={"type":"json_object"}`, falls back without it; parses JSON response; on any exception returns `("unknown", 0.0)`
- [X] T006 [P] [US1] Implement `EmailGuardianService._create_quarantine_record(brand_id: str, email: dict, classification: str, confidence: float) -> Optional[str]` in `backend/src/services/email_guardian_service.py` — calls `supabase_insert("email_quarantine", {"brand_id": brand_id, "sender_email": email.get("sender_email",""), "subject": email.get("subject",""), "body_preview": (email.get("body") or "")[:500], "thread_id": email.get("thread_id"), "ai_classification": classification, "ai_confidence": confidence, "status": "pending"})`; returns the inserted row's `id` or None on error; swallows all exceptions with a warning log
- [X] T007 [US1] Implement `EmailGuardianService.evaluate(email: dict, brand_id: str) -> GuardianResult` in `backend/src/services/email_guardian_service.py` — (1) calls `_load_settings(brand_id)` to get support_only_mode, confidence_threshold, auto_reply_enabled; (2) extracts subject and body from email dict; (3) calls `_classify_email(subject, body)` → (classification, confidence); (4) if `support_only_mode=True` and classification != "customer_support": return `GuardianResult(decision="blocked", classification=classification, confidence=confidence, reason="ai_classification", quarantine_id=None, auto_reply_enabled=settings["auto_reply_enabled"])`; (5) if classification == "customer_support" and confidence < confidence_threshold: call `_create_quarantine_record(...)`, return `GuardianResult(decision="quarantined", classification=classification, confidence=confidence, reason="low_confidence", quarantine_id=qid, auto_reply_enabled=False)`; (6) return `GuardianResult(decision="allowed", classification=classification, confidence=confidence, reason=None, quarantine_id=None, auto_reply_enabled=settings["auto_reply_enabled"])`; wraps entire body in `try/except Exception` that logs WARNING and returns `GUARDIAN_ALLOW`
- [X] T008 [US1] Implement `EmailGuardianService.log_guardian_decision(brand_id: str, sender_email: str, thread_id: Optional[str], result: GuardianResult) -> None` in `backend/src/services/email_guardian_service.py` — inserts a new row to `email_filter_log` via `supabase_insert` with: `brand_id`, `sender_email`, `thread_id`, `decision=result.decision`, `filter_reason=result.reason`, `email_category="unknown"`, `sender_type="automated"`, `ai_classification=result.classification`, `ai_confidence=result.confidence`; swallows all exceptions silently
- [X] T009 [US1] Integrate guardian into `backend/src/channels/email_poller.py` — import `from src.services.email_guardian_service import email_guardian_service` at module level; after the existing `if filter_result.decision == "blocked": continue` block (~line 121), add: `guardian_result = email_guardian_service.evaluate(email, brand_id)`; `email_guardian_service.log_guardian_decision(brand_id, sender, thread_id, guardian_result)`; `if guardian_result.decision in ("blocked", "quarantined"): logger.info(f"[Poller] Guardian {guardian_result.decision}: {sender} reason={guardian_result.reason}"); continue`; store `auto_reply_enabled = guardian_result.auto_reply_enabled` for use in ticket creation
- [X] T010 [US1] Pass `auto_reply_enabled` through to message processor in `backend/src/channels/email_poller.py` — when building the message dict passed to `message_processor.process_message(...)`, include `"auto_reply_enabled": auto_reply_enabled`; in `backend/src/workers/message_processor.py` Stage 10, wrap the email send block with `if message.get("auto_reply_enabled", True):` so the AI reply is skipped when `auto_reply_enabled=False`

**Checkpoint**: US1 + US2 complete — non-support emails blocked by AI; genuine support emails create tickets. Verify with quickstart.md Scenarios 1, 2, 6, 9.

---

## Phase 4: User Story 2 — Support-Only Mode as Production Default (Priority: P1)

**Goal**: Brand owners can read and update `support_only_mode`, `confidence_threshold`, and `auto_reply_enabled` via the settings API. New brands default to safe values.

**Independent Test**: `GET /api/v1/settings/email-filter` for a new brand returns `support_only_mode=true`, `confidence_threshold=0.75`; `PATCH` with `{"support_only_mode": false}` persists and changes guardian behavior on next poll.

### Implementation for User Story 2

- [X] T011 [US2] Update `EmailFilterSettingsResponse` and `EmailFilterSettingsPatch` Pydantic models in `backend/src/api/routes/v2_email_filter.py` — add to `EmailFilterSettingsResponse`: `support_only_mode: bool = True`, `confidence_threshold: float = 0.75`, `auto_reply_enabled: bool = True`; add to `EmailFilterSettingsPatch`: `support_only_mode: Optional[bool] = None`, `confidence_threshold: Optional[float] = Field(None, ge=0.0, le=1.0)`, `auto_reply_enabled: Optional[bool] = None`
- [X] T012 [US2] Update `PATCH /settings/email-filter` upsert logic in `backend/src/api/routes/v2_email_filter.py` — include `support_only_mode`, `confidence_threshold`, and `auto_reply_enabled` in the update payload when present in the request body (same pattern as existing `max_auto_replies` and `promotion_filter_enabled` fields); return the full updated settings response including new fields

**Checkpoint**: US2 complete — verify quickstart.md Scenarios 7, 8, 11.

---

## Phase 5: User Story 3 — Quarantine Queue (Priority: P2)

**Goal**: Operators see quarantined emails in a queue and can promote them to tickets or discard them.

**Independent Test**: Set `confidence_threshold=0.99` via PATCH; send any real email; verify quarantine record created; call `POST /quarantine/{id}/promote`; verify ticket created and quarantine status = 'promoted'.

### Implementation for User Story 3 — Backend

- [X] T013 [US3] Create `backend/src/api/routes/v2_quarantine.py` with `GET /quarantine` endpoint — `router = APIRouter(prefix="/quarantine", tags=["quarantine"])`; uses `Depends(get_current_tenant)` for v1 JWT; resolves brand_id from tenant using `supabase_select("brands", {"tenant_id": f"eq.{tenant.tenant_id}"})` (active+Gmail preferred, same helper pattern as v2_email_filter.py); runs lazy expiry: `supabase_update("email_quarantine", {"brand_id": f"eq.{brand_id}", "status": "eq.pending", "expires_at": "lt.now()"}, {"status": "expired"})`; then queries `supabase_select("email_quarantine", {"brand_id": f"eq.{brand_id}", "status": "eq.pending", "order": "created_at.desc", "limit": f"{limit}", "offset": f"{offset}"})`; returns `{"items": [...], "total": len(all_pending), "pending": pending_count}`; supports `?status=pending&limit=20&offset=0` query params
- [X] T014 [US3] Add `POST /quarantine/{id}/promote` to `backend/src/api/routes/v2_quarantine.py` — fetch quarantine record by id; verify `brand_id` matches tenant's brand (return 404 if not); verify `status == "pending"` (return 404 if already actioned); call `supabase_insert("tickets", {"subject": q["subject"], "description": q["body_preview"], "customer_email": q["sender_email"], "status": "open", "source_channel": "email", "gmail_thread_id": q["thread_id"], "email_category": "support", "sender_type": "human", "store_id": brand_id, "auto_reply_count": 0})` (mirror the poller ticket creation pattern); call `supabase_update("email_quarantine", {"id": f"eq.{id}"}, {"status": "promoted", "actioned_by": tenant.email, "actioned_at": now_iso})`; return `{"success": True, "ticket_id": new_ticket["id"]}`
- [X] T015 [US3] Add `POST /quarantine/{id}/discard` to `backend/src/api/routes/v2_quarantine.py` — same tenant+brand ownership check; update quarantine status to 'discarded' with actioned_by and actioned_at; return `{"success": True, "message": "Email discarded"}`
- [X] T016 [US3] Register quarantine router in `backend/main.py` — add registration block: `from src.api.routes.v2_quarantine import router as quarantine_router; register_router(quarantine_router, prefix="/api/v1"); logger.info("✓ Quarantine router registered")`

### Implementation for User Story 3 — Frontend

- [X] T017 [P] [US3] Create `ai-ops-console/src/pages/QuarantineQueue.jsx` — fetches `GET /api/v1/quarantine` on mount using `client.get('/api/v1/quarantine')`; renders a table/list with columns: Sender, Subject, Classification, Confidence (as %), Age, and action buttons "Promote to Ticket" (calls `POST /api/v1/quarantine/{id}/promote`, then refreshes list) and "Discard" (calls `POST /api/v1/quarantine/{id}/discard`, then refreshes list); handles loading skeleton, error state, and empty state ("No emails in quarantine"); uses existing inline style patterns from other pages (no new CSS frameworks)
- [X] T018 [US3] Add `/quarantine` route to `ai-ops-console/src/App.jsx` — import `QuarantineQueue` from `./pages/QuarantineQueue`; add `<Route path="/quarantine" element={<QuarantineQueue />} />` after the `/actions` route

**Checkpoint**: US3 complete — verify quickstart.md Scenarios 3, 4, 5.

---

## Phase 6: User Story 4 — Settings UI Controls (Priority: P2)

**Goal**: Brand owners see and can update `confidence_threshold`, `support_only_mode`, and `auto_reply_enabled` in the Settings page.

**Independent Test**: Navigate to Settings → Email Filters; update confidence_threshold to 0.9; reload; confirm value persists; send email that was previously allowed but now quarantined at new threshold.

### Implementation for User Story 4

- [X] T019 [US4] Add three new controls to the existing `FilterTab` component in `ai-ops-console/src/pages/Settings.jsx` — (1) Support-Only Mode: toggle (checkbox or switch) bound to `settings.support_only_mode`; (2) Auto-Reply: toggle bound to `settings.auto_reply_enabled`, labeled "Send AI replies automatically"; (3) Confidence Threshold: number input (step=0.05, min=0, max=1) bound to `settings.confidence_threshold`, with helper text "Minimum AI confidence to create a ticket (0.75 = 75%)"; wire all three to the existing save handler that calls `PATCH /api/v1/settings/email-filter`; load initial values from existing `GET /api/v1/settings/email-filter` call already in the component

**Checkpoint**: US4 complete — verify quickstart.md Scenario 8 and that settings round-trip correctly.

---

## Phase 7: User Story 5 — Guardian Analytics Dashboard (Priority: P3)

**Goal**: Dashboard "Filtered Emails" widget shows quarantine count, `ai_classification` reason count, and `low_confidence` reason count.

**Independent Test**: After 10+ emails processed (mix of blocked, allowed, quarantined), widget shows non-zero counts for `ai_classification` and/or `low_confidence` in the by_reason breakdown, and a quarantine count matching the `email_quarantine` table.

### Implementation for User Story 5

- [X] T020 [P] [US5] Update `GET /filter-logs?summary=true` in `backend/src/api/routes/v2_email_filter.py` — in the summary aggregation block, add: (a) `total_quarantined` by calling `supabase_select("email_quarantine", {"brand_id": f"eq.{brand_id}", "created_at": f"gte.{cutoff_iso}", "status": "neq.expired"})` and counting results; (b) ensure `ai_classification` and `low_confidence` filter_reason values from `email_filter_log` appear in the `by_reason` dict (they will automatically since the aggregation is by `filter_reason` value); add `total_quarantined` to the summary response dict
- [X] T021 [P] [US5] Update `ai-ops-console/src/components/FilteredEmailsWidget.jsx` — add a fourth stat box for "Quarantined" using `data.total_quarantined` (warning/yellow color); when `total_quarantined > 0`, render a small "Review →" link pointing to `/quarantine`; the existing `REASON_LABELS` map already handles `ai_classification` → "AI Classification" and `low_confidence` → "Low Confidence" if added (add these two entries)

**Checkpoint**: US5 complete — verify quickstart.md Scenario 10.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T022 [P] Add "Quarantine" navigation link in the ai-ops-console sidebar — find the nav link array in the layout/sidebar component (check `ai-ops-console/src/components/` or `Layout.jsx`) and add `{ path: '/quarantine', label: 'Quarantine' }` after the Actions link
- [X] T023 [P] Verify multi-tenant isolation in `backend/src/api/routes/v2_quarantine.py` — confirm all `supabase_select` and `supabase_update` calls on `email_quarantine` include `brand_id=eq.{brand_id}` scoped to the authenticated tenant's brand; no query should return rows across tenants
- [X] T024 Run all 11 quickstart.md scenarios against the running Docker stack (`docker compose up backend email_poller`) and confirm each passes; apply migration first

---

## Dependencies & Execution Order

### Phase Dependencies

| Phase | Depends On | Notes |
|-------|-----------|-------|
| Phase 1: Setup | — | Start immediately |
| Phase 2: Foundational | Phase 1 (migration applied) | BLOCKS all stories |
| Phase 3: US1 | Phase 2 | Core classifier + poller integration |
| Phase 4: US2 | Phase 2 | Settings API update (can run in parallel with US1) |
| Phase 5: US3 | Phase 3 | Quarantine records must exist before queue UI |
| Phase 6: US4 | Phase 4 | Settings UI extends the API from US2 |
| Phase 7: US5 | Phase 3, Phase 5 | Analytics needs both filter log and quarantine data |
| Phase 8: Polish | All phases | Validation + nav |

### Milestone Grouping

**Milestone 1 — Safety (ship first)**: Phases 1–4 (T001–T012)
Guardian AI filter + support-only mode with safe defaults. No UI required. Validates in Docker with quickstart Scenarios 1, 2, 6, 9, 11.

**Milestone 2 — Operator Tooling**: Phases 5–6 (T013–T019)
Quarantine queue + settings UI. Validates with quickstart Scenarios 3, 4, 5, 7, 8.

**Milestone 3 — Analytics**: Phase 7 (T020–T021)
Dashboard widget update. Validates with quickstart Scenario 10.

### Within User Stories

```text
US1: T005/T006 [P] → T007 → T008 → T009 → T010
US2: T011 → T012  (can run in parallel with US1 phases 3-4)
US3: T013 → T014 → T015 → T016 → T017 [P] → T018
US4: T019  (depends on T011, T012 complete)
US5: T020/T021 [P]  (depends on T009 for filter_log data)
```

---

## Parallel Opportunities

```text
Phase 3 (US1): T005 and T006 implement independent methods — run in parallel.
Phase 4 (US2): T011 and Phase 3 US1 work — can overlap once T004 is done.
Phase 5 (US3): T017 (frontend) can start as soon as T013 (backend GET endpoint) is done.
Phase 7 (US5): T020 (backend) and T021 (frontend widget) are in different files — run in parallel.
Phase 8: T022 and T023 touch different files — run in parallel.
```

---

## Implementation Strategy

### MVP First (Milestone 1: Phases 1–4, T001–T012)

1. Apply migration in Supabase SQL editor
2. Build `email_guardian_service.py` skeleton (T002–T003)
3. Extend settings loader (T004)
4. Implement AI classifier + evaluate() + log (T005–T008)
5. Wire into email_poller + message_processor (T009–T010)
6. Expose new settings fields in API (T011–T012)
7. **STOP AND VALIDATE**: Run quickstart.md Scenarios 1, 2, 6, 9, 11 in Docker

### Incremental Delivery

1. Milestone 1 → AI safety in production; no more non-support tickets created
2. Milestone 2 → Operators can review quarantined emails and configure thresholds
3. Milestone 3 → Dashboard visibility into AI filter effectiveness

### Notes

- Apply SQL migration manually before running any guardian code — columns must exist
- The Mistral API key (`MISTRAL_API_KEY`) and base URL (`MISTRAL_API_BASE_URL`) are already in the environment
- Guardian wraps, does not replace, `email_filter_service.py` — both services run per email that reaches the poller
- The `auto_reply_enabled` flag is brand-level, not email-level — it applies to all emails for that brand
- `loop_risk` from feature 005 remains independent; an email can be `auto_reply_enabled=false` AND `loop_risk=true`
- All new endpoints use `get_current_tenant` (v1 JWT) — never v2 Supabase Auth
