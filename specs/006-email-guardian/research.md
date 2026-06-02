# Research: Email Guardian — AI Classification + Confidence Gate

**Feature**: 006-email-guardian
**Date**: 2026-05-15
**Status**: Complete — no NEEDS CLARIFICATION markers remain

---

## Decision 1: AI Classifier Implementation

**Decision**: Use the existing Mistral API OpenAI-compatible client already initialized in `customer_success_agent.py`. Create a dedicated classification prompt in `email_guardian_service.py` that returns structured JSON `{"classification": "<label>", "confidence": <float>}`.

**Rationale**: The Mistral client (via `openai.OpenAI` with Mistral's base_url) is already present in the codebase at `backend/src/agent/customer_success_agent.py:38-41`. No new package or API key is needed. Constitution XI prohibits new packages unless absolutely necessary. Using the same client pattern (with `response_format={"type": "json_object"}` and a Mistral fallback without it) ensures parity with existing agent code.

**Classifier prompt approach**: Single-turn classification prompt with the email subject and first 1,000 characters of body. Temperature=0.0 for deterministic output. Max tokens=80 (only the JSON label and confidence needed). Fallback to `classification="unknown", confidence=0.0` on any API error.

**Alternatives considered**:
- Keyword heuristics only — rejected because the spec requires AI classification for Layer 4 specifically; Layer 2 already covers keyword/prefix checks.
- Separate embedding-based classifier — rejected; adds vector search complexity and a new model dependency. Mistral chat completion is simpler and already available.
- Dedicated Mistral classification endpoint — not available; standard chat completions suffice.

---

## Decision 2: Guardian Position in Poller Pipeline

**Decision**: Insert the guardian call in `email_poller.py` immediately after `filter_result.decision == "blocked"` check (currently line ~121). If layers 1–3 allow the email, the guardian then runs Layer 4 (AI classification) and Layer 5 (confidence gate) before any ticket creation.

**Rationale**: The existing filter service handles fast, zero-cost rule checks (no API calls). Running it first avoids unnecessary Mistral API calls for emails that are already blocked by domain/prefix/header rules. The guardian only fires on emails that pass all rule-based layers.

**Integration point** (email_poller.py ~line 121):
```python
filter_result = email_filter_service.evaluate(email, brand_id)
email_filter_service.log_decision(...)
if filter_result.decision == "blocked":
    continue
# ← INSERT: guardian.evaluate(email, brand_id, settings) here
```

---

## Decision 3: Quarantine Storage

**Decision**: New table `email_quarantine` in Supabase. Fields: id, brand_id, sender_email, subject, body_preview (500 chars), thread_id, ai_classification, ai_confidence, status (pending/promoted/discarded/expired), actioned_by, actioned_at, expires_at (created_at + 7 days), created_at.

**Rationale**: Quarantine is a new entity with distinct lifecycle states (pending → promoted/discarded/expired). Storing it in `email_filter_log` would conflate filtering audit records with actionable queue items. A separate table enables clean pagination, status filtering, and operator actions without touching the append-only filter log.

**Alternatives considered**:
- Adding `needs_human_review` flag to tickets — rejected; quarantined emails have no ticket yet, so this would require creating a ticket first, which defeats the purpose of the confidence gate.
- Redis queue — rejected; no persistence across restarts and no operator-facing query capability.

---

## Decision 4: AI Classification Logging

**Decision**: Extend `email_filter_log` with two new nullable columns: `ai_classification TEXT` and `ai_confidence FLOAT`. These are populated when the guardian runs Layer 4; they remain NULL for emails blocked by layers 1–3 (guardian never runs for those).

**Rationale**: Reusing the existing audit log table avoids a new `guardian_log` table and keeps all filter decisions in one place for the dashboard widget query. The existing `filter_reason` column already captures `ai_classification` and `low_confidence` as reason values when the guardian makes the blocking decision.

---

## Decision 5: New system_settings Columns

**Decision**: Add three columns to `system_settings`: `support_only_mode BOOLEAN DEFAULT true`, `confidence_threshold FLOAT DEFAULT 0.75`, `auto_reply_enabled BOOLEAN DEFAULT true`.

**Rationale**: Consistent with how feature 005 added filter settings to the same table. The `_get_filter_settings()` helper in `v2_email_filter.py` already loads this table with fallback to the global default store — extending it is simpler than a new settings table.

**Default values rationale**:
- `support_only_mode=true`: Safe production default per spec US2.
- `confidence_threshold=0.75`: Empirically calibrated to catch obvious low-quality classifications while allowing genuine support emails through. Configurable per brand.
- `auto_reply_enabled=true`: Backward compatible — existing brands keep auto-reply behavior.

---

## Decision 6: Quarantine Auto-Expiry

**Decision**: Expiry is enforced at query time via `WHERE expires_at > now() AND status = 'pending'` in the quarantine list endpoint. A separate cleanup job marks expired records `status='expired'` — implemented as a simple function called at the start of each quarantine list request (lazy expiry).

**Rationale**: No CRON infrastructure exists in the Docker stack (no Celery, no scheduler service). Lazy expiry on read is zero-infrastructure and correct for the spec requirement (7-day expiry). The email poller (every 60 seconds) could also trigger cleanup, but calling it from the API route is simpler and ensures cleanup happens when operators actually use the queue.

---

## Decision 7: Quarantine Promote Flow

**Decision**: `POST /api/v1/quarantine/{id}/promote` creates a ticket via direct `supabase_insert("tickets", {...})` using the quarantined email's data (sender_email, subject, body_preview as description), sets `quarantine.status='promoted'`, and returns the new ticket_id.

**Rationale**: Re-uses existing ticket creation pattern from email_poller.py. No separate ticket creation service needed. The promoted ticket has `email_category='support'`, `sender_type='human'`, and no auto_reply_count increment (human promoted it, so loop counter starts fresh).

---

## Decision 8: Frontend Route

**Decision**: Add `/quarantine` route to `ai-ops-console/src/App.jsx` after the `/actions` route (currently line ~36). New page: `ai-ops-console/src/pages/QuarantineQueue.jsx`.

**Rationale**: Quarantine is an operator workflow page, not a settings page. Placing it between `/actions` and `/brands` in the nav puts it with other operational queue screens.
