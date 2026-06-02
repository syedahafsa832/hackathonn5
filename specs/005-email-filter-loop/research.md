# Research: Email Filtering & Loop Prevention

**Feature**: 005-email-filter-loop
**Date**: 2026-05-15
**Status**: Complete — no NEEDS CLARIFICATION items remain

---

## Decision 1: Filter Placement (Synchronous vs Async)

**Decision**: Filtering runs synchronously inside the email poller, before any ticket creation or message processor call.

**Rationale**: The email poller already runs on a 60-second interval in its own process. Inserting a filter check inline adds negligible latency (< 5 ms for heuristic checks) while eliminating the complexity of a separate async pipeline. The spec requires filters to execute before ticket creation — synchronous placement is the only design that guarantees this invariant.

**Alternatives considered**:
- Async background classification queue: Rejected because it introduces a window where a ticket could be partially created before the filter fires. Also requires a message broker (Redis/SQS) — violates the "no new dependencies" principle.
- Supabase Edge Function: Rejected — not in the current stack; adds new infrastructure.

---

## Decision 2: Gmail Category Label Detection

**Decision**: Fetch the `labelIds` array from the Gmail API message object. Map known category label IDs (`CATEGORY_PROMOTIONS`, `CATEGORY_SOCIAL`, `CATEGORY_UPDATES`) to filter decisions. If labels are absent, fall back to sender-pattern and content heuristics.

**Rationale**: The Gmail API already returns `labelIds` in the `messages.get` response used by `brand_gmail_service._build_service`. No additional API call is needed. This is the most reliable signal available (set by Google's own classification) and requires zero additional permissions beyond the existing `gmail.modify` scope.

**Alternatives considered**:
- Parse Gmail category headers from raw MIME: Unreliable, not always present, requires raw format fetch (extra API call).
- NLP/ML classifier: Out of scope for v1 (spec explicitly excludes ML); also requires new dependencies.

---

## Decision 3: Filter Settings Storage

**Decision**: Add new columns to the existing `system_settings` table. The table already holds per-brand/store configuration (`ai_mode`, `confidence_threshold`). New columns: `blocked_domains` (JSONB array), `whitelisted_domains` (JSONB array), `max_auto_replies` (integer, default 2), `promotion_filter_enabled` (boolean, default true), `loop_protection_enabled` (boolean, default true).

**Rationale**: Avoids creating a new table and new join logic. The `system_settings` table is already scoped to `store_id` (brand), already read by the message processor at startup, and already has a fallback to the global default store. All filter settings are per-brand — this fits perfectly.

**Alternatives considered**:
- New `email_filter_settings` table: Rejected — unnecessary indirection; same data shape fits in the existing table.
- Store in tenants table: Rejected — tenants table is tenant-scoped, not brand-scoped. Brands may eventually support multiple configs per tenant.

---

## Decision 4: Filter Log Storage

**Decision**: New table `email_filter_log` — append-only, lightweight, no foreign key to tickets (since filtered emails never become tickets).

**Rationale**: Filter events must be queryable for the dashboard widget independently of the tickets table. A separate log table is the cleanest design: the widget query is `SELECT reason, COUNT(*) FROM email_filter_log WHERE brand_id = ? AND created_at > NOW() - INTERVAL '7 days' GROUP BY reason`.

**Schema**: `(id UUID PK, brand_id UUID, sender_email TEXT, thread_id TEXT, decision TEXT, filter_reason TEXT, email_category TEXT, created_at TIMESTAMPTZ DEFAULT now())`

**Alternatives considered**:
- Log to existing `audit_logs` table: Rejected — audit_logs is tenant-level; the dashboard widget needs brand-level filtering. Schema mismatch would require large metadata blobs.
- File-based logging: Rejected — not queryable for the dashboard.

---

## Decision 5: Loop Detection Location

**Decision**: Loop detection runs in the email poller, inside `_poll_brand_inbox`, immediately after the thread-match check (existing threading logic). If a thread's ticket already has `auto_reply_count >= max_auto_replies`, the new message is discarded. The `auto_reply_count` is incremented inside `message_processor.py` when an AI reply is actually sent.

**Rationale**: Thread matching already happens in both the poller and the message processor. Putting the loop check in the poller (before calling `message_processor`) stops the email from being processed at all when the threshold is hit, which is the safest behaviour. Incrementing in the processor is correct because only the processor knows whether an AI reply was actually generated and queued.

**Alternatives considered**:
- Check inside message_processor only: The email still gets passed to the processor and partially processed before being rejected — wastes compute and risks edge cases.
- Check inside brand_gmail_service: Too early — doesn't have access to ticket state.

---

## Decision 6: New Packages Required

**Decision**: None. All filtering logic uses Python stdlib (`re`, `json`, `logging`) and the existing Supabase REST client (`supabase_select`, `supabase_insert`, `supabase_update`). Gmail label IDs are available from the existing Gmail API response.

**Rationale**: The spec requires production-grade filtering, not ML classification. Heuristic keyword matching and header parsing are fully implementable with stdlib regex and string operations. This honours the constitution's dependency minimalism principle.

---

## Decision 7: API Auth for Filter Settings Endpoints

**Decision**: New endpoints at `GET /api/v1/settings/email-filter` and `PATCH /api/v1/settings/email-filter` use `Depends(get_current_tenant)` — same v1 JWT auth as all other `/api/v1/settings/*` endpoints.

**Rationale**: Constitution Principle X mandates v1 JWT tokens for all admin console routes. The filter settings are brand-specific config, consistent with `/api/v1/settings/account` and `/api/v1/settings/shopify`.

---

## Decision 8: Whitelisted Sender Bypass Scope

**Decision**: Whitelisting is domain-level only (not per-address). A whitelisted domain bypasses sender-pattern checks (FR-003, FR-004) and Gmail category checks (FR-002). Header checks (FR-005) are NOT bypassed — even whitelisted senders cannot trigger AI replies with auto-reply headers, because those headers indicate the message itself is automated regardless of sender legitimacy.

**Rationale**: This prevents the edge case where a legitimate domain's automated system (e.g., a CRM order confirmation) accidentally triggers a support ticket even after whitelisting. The spec states "bypass sender-pattern filters" — headers are a separate, stronger signal about the message itself, not the sender identity.
