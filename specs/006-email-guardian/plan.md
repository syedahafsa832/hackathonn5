# Implementation Plan: Email Guardian — AI Classification + Confidence Gate

**Branch**: `006-email-guardian` | **Date**: 2026-05-15 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/006-email-guardian/spec.md`

---

## Summary

Adds two AI-powered filter layers on top of the rule-based email filter (feature 005):

- **Layer 4 — AI Intent Classifier**: Calls the existing Mistral API to classify each inbound email (subject + body) into one of 7 categories. Only `customer_support` proceeds when `support_only_mode=true`.
- **Layer 5 — Confidence Gate**: Emails classified as `customer_support` with `confidence < threshold` are quarantined (pending human review) rather than creating a ticket.

Supporting changes: new `email_quarantine` table, three new `system_settings` columns (`support_only_mode`, `confidence_threshold`, `auto_reply_enabled`), updated Settings UI, new Quarantine Queue page, and updated dashboard widget.

The existing `email_filter_service.py` (Layers 1–3 + loop detection) is **not modified**.

---

## Technical Context

**Language/Version**: Python 3.11 (backend), React 18 (ai-ops-console)
**Primary Dependencies**: FastAPI, OpenAI SDK (pointing at Mistral API — already in `requirements.txt`), requests (Supabase REST — already present)
**Storage**: Supabase PostgreSQL (REST via existing `supabase_client.py`)
**Testing**: No automated harness; integration scenarios defined in `quickstart.md`
**Target Platform**: Linux/Docker (same containers as existing stack)
**Project Type**: Web application (backend + ai-ops-console frontend)
**Performance Goals**: Classifier call < 5 seconds per email; well within the 60-second poll cycle
**Constraints**: No new packages (Constitution XI); v1 JWT only (Constitution X); no modifications to `email_filter_service.py` (Constitution XII); classifier errors must fail open (Constitution III)
**Scale/Scope**: ~100–1000 emails/day per brand; classifier called only for emails that pass layers 1–3

---

## Constitution Check

*GATE: Must pass before Phase 0 research. All gates pass.*

| # | Principle | Status | Notes |
|---|-----------|--------|-------|
| I | Code Quality | ✅ PASS | Guardian service is clean, modular, single-responsibility |
| II | Multi-Tenant Security | ✅ PASS | All queries scoped to `brand_id`; settings per-tenant |
| III | Reliability & Stability | ✅ PASS | Classifier failure → fail open (quarantine, not crash) |
| IV | AI Behavior Standards | ✅ PASS | No financial actions; confidence gate adds safety layer |
| V | User Experience | ✅ PASS | Quarantine queue is minimal, 2-click operator workflow |
| VI | Performance | ✅ PASS | Mistral call < 5s; 60-second poll provides headroom |
| VII | Testing Standards | ✅ PASS | 11 independent integration scenarios in quickstart.md |
| VIII | Frontend Architecture | ✅ PASS | Quarantine page is event-driven workflow, not CRUD table |
| IX | Product Philosophy | ✅ PASS | Directly supports "support-only" resolution workflow |
| X | Auth Integrity | ✅ PASS | All new endpoints use `Depends(get_current_tenant)` (v1 JWT) |
| XI | Engineering Discipline | ✅ PASS | No new packages; Mistral OpenAI SDK already in stack; API-first |
| XII | Feature Stability | ✅ PASS | `email_filter_service.py` untouched; only additive changes |

---

## Project Structure

### Documentation (this feature)

```text
specs/006-email-guardian/
├── plan.md              ← this file
├── research.md          ← Phase 0 complete
├── data-model.md        ← Phase 1 complete
├── quickstart.md        ← Phase 1 complete (11 scenarios)
├── contracts/
│   └── email-guardian-openapi.yaml   ← Phase 1 complete
├── checklists/
│   └── requirements.md
└── tasks.md             ← Phase 2 (/sp.tasks — not yet created)
```

### Source Code

```text
backend/
├── migrations/
│   └── 012_email_guardian_schema.sql   [NEW] — quarantine table + settings columns
├── src/
│   ├── services/
│   │   └── email_guardian_service.py   [NEW] — Layers 4+5 + quarantine creation
│   ├── channels/
│   │   └── email_poller.py             [MODIFY] — call guardian after filter layers 1-3
│   └── api/
│       └── routes/
│           ├── v2_quarantine.py        [NEW] — quarantine CRUD endpoints
│           ├── v2_email_filter.py      [MODIFY] — add 3 new settings fields
│           └── (main.py)              [MODIFY] — register quarantine router

ai-ops-console/
└── src/
    ├── App.jsx                          [MODIFY] — add /quarantine route
    ├── pages/
    │   ├── QuarantineQueue.jsx         [NEW] — operator review page
    │   └── Settings.jsx                [MODIFY] — add 3 new controls
    └── components/
        └── FilteredEmailsWidget.jsx    [MODIFY] — add quarantine count + link
```

---

## Key Design Decisions

### 1. Guardian wraps, does not replace, filter service

`email_poller.py` calls `email_filter_service.evaluate()` first (Layers 1–3, fast/free). Only emails that return `decision="allowed"` are passed to `email_guardian_service.evaluate()` (Layers 4–5, requires Mistral API call). This avoids unnecessary API costs for emails already blocked by domain/prefix/header rules.

### 2. Fail-open on classifier error

If the Mistral API call fails or times out, the guardian returns:
```python
GuardianDecision(classification="unknown", confidence=0.0, decision="quarantined", reason="classifier_error")
```
When `support_only_mode=true`, unknown emails are quarantined (not dropped). When `support_only_mode=false`, they proceed to ticket creation. A real customer is never silently lost due to a classifier bug.

### 3. `auto_reply_enabled` flag

When `auto_reply_enabled=False`, the guardian returns `decision="allowed"` but sets a flag that the poller passes to the message processor. The processor skips Stage 10 (email send) when this flag is set. Tickets are still created — human agents can reply manually.

### 4. Quarantine auto-expiry (lazy)

No CRON infrastructure exists. Expiry is enforced lazily:
- On `GET /quarantine`, expired records are bulk-updated to `status='expired'` before the response is assembled.
- `expires_at = created_at + 7 days` set at INSERT time.

### 5. Promote creates a real ticket

`POST /quarantine/{id}/promote` inserts a row into `tickets` with `email_category='support'`, `sender_type='human'`, `auto_reply_count=0`. The ticket creation mirrors the poller's ticket creation path to ensure all required fields are present.

### 6. Settings backward compatibility

`v2_email_filter.py` already serves `GET/PATCH /settings/email-filter`. The three new columns (`support_only_mode`, `confidence_threshold`, `auto_reply_enabled`) are added to the same settings dict with defaults. Existing callers receive the new fields in the response automatically; existing PATCH calls that omit the new fields are unaffected.

---

## Integration Points

| Source | → | Target | Data Passed |
|--------|---|--------|-------------|
| `email_poller.py` | calls | `email_guardian_service.evaluate()` | email dict + brand_id + settings |
| `email_guardian_service` | writes | `email_filter_log` | ai_classification, ai_confidence, decision, reason |
| `email_guardian_service` | writes | `email_quarantine` | quarantine record on low-confidence |
| `email_poller.py` | passes flag | `message_processor` | `auto_reply_enabled` from settings |
| `v2_quarantine.py:promote` | writes | `tickets` | ticket row from quarantine record |
| `FilteredEmailsWidget` | reads | `/api/v1/filter-logs?summary=true` | quarantine count in by_reason |
| `QuarantineQueue.jsx` | reads/writes | `/api/v1/quarantine` | queue items + promote/discard actions |

---

## Milestone Strategy

**Milestone 1 — Safety (ship first, no UI required)**
Phases 1–5 (migration + guardian service + poller integration)
- Apply `012_email_guardian_schema.sql`
- Implement `email_guardian_service.py` with Mistral classifier + confidence gate
- Wire into `email_poller.py`
- `auto_reply_enabled` flag passed to processor
- Validate with quickstart Scenarios 1, 2, 3, 9 in Docker

**Milestone 2 — Operator Tooling**
Phases 6–7 (quarantine API + UI)
- `v2_quarantine.py` endpoints
- `QuarantineQueue.jsx` page
- Validate with quickstart Scenarios 4, 5

**Milestone 3 — Settings + Dashboard**
Phase 8 (settings UI + widget update)
- Settings.jsx new controls
- FilteredEmailsWidget quarantine count
- Validate with quickstart Scenarios 6–8, 10, 11
