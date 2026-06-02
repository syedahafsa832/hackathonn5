# Research: Resolv MVP Completion

**Branch**: `004-resolv-completion` | **Date**: 2026-05-13

---

## Item A — Auth Middleware (CRITICAL)

**Decision**: Add a v1 token fallback path inside `auth_middleware.get_current_user`.

**Finding**: `auth_service.create_access_token` encodes `sub = tenant_id`. The existing
`auth_middleware.py` calls `supabase_auth_service.verify_jwt(token)` then
`supabase_auth_service.get_user_context(sub)` — which queries the `users` table by
`supabase_auth_id`. A v1 token's `sub` is a `tenant_id` (row in `tenants` table), not a
Supabase auth UUID, so `get_user_context` returns None → 401.

**Fix**: In `auth_middleware.py`, after `verify_jwt` succeeds, attempt `get_user_context`.
If it returns None, fall back: treat `sub` as `tenant_id`, query the `tenants` table via
`auth_service.get_tenant(sub)`, and construct a minimal `UserContext` from the tenant row.
This keeps the two auth systems independent — v1 tenants get a compatible context object.

**Alternatives considered**:
- Merge auth systems (rejected: high risk, out of scope, violates "never break working")
- Add SUPABASE_JWT_SECRET to env (rejected: already done, doesn't fix user lookup)
- Separate middleware per router group (rejected: too much refactor)

---

## Item B — Dashboard Stats

**Decision**: Fix `services.js` to call `/api/tickets` and `/api/v1/actions/stats` instead of
the non-existent `/admin/conversations` and `/admin/leads/hot` routes.

**Finding**: The dashboard `getStats()` in `services.js` calls three routes that don't exist
in the hack5 backend (`/admin/conversations`, `/admin/leads/hot`, `/admin/routing-logs`). Each
`.catch()` returns an empty default — so all stats silently zero out. The correct routes are:
- `/api/tickets` — returns array of all tickets (no auth needed)
- `/api/v1/actions/stats` — returns action counts (tenant auth)
- `/api/ai-mode` — returns open tickets with AI metadata

**Fix**: Rewrite `getStats()` to use the real routes and compute stats from their responses.

---

## Item C — Ticket List Fields

**Decision**: Add `channel` default in `tickets.py` list route and fix frontend column render.

**Finding**: `supabase_service.get_tickets()` returns all database columns including
`customer_email`. The `channel` field may not exist on all rows (legacy rows). Frontend
`Tickets.jsx` renders `c.channel` and `c.customer_email` but the column key for sender is
`customer_email`, not `sender`. If `channel` is null in DB, it renders as empty.

**Fix**: In `tickets.py` list handler, add a transform that defaults `channel` to `"email"`
when null. Frontend column header "Sender" already maps to `customer_email` correctly.

---

## Item D — Worker Crash

**Decision**: No fix required for main loop. Add sleep-on-error guard to inner per-brand
loop as a hardening measure.

**Finding**: `email_poller._polling_loop()` already wraps `_poll_all_inboxes()` in
try/except with logging. The process will not exit on error. However, individual brand polling
inside `_poll_all_inboxes` may not be protected — if one brand fails, the others are skipped.

**Fix**: Wrap the per-brand iteration in an inner try/except and add `asyncio.sleep(5)` on
the outer exception handler for rate-limit protection.

---

## Item E — Approve-AI Email Send

**Decision**: Complete the `/api/v2/tickets/{id}/approve-ai` endpoint and fix the
`/api/tickets/{id}/send-reply` call path from the admin console.

**Finding**: `brand_gmail_service.send_email()` is implemented and returns
`{"success": True, "id": msg_id}`. The `v2_tickets.py` `/approve-ai` endpoint captures the
AI response but never calls `send_email`. The `tickets.py` `/send-reply` works but requires
the brand to have `gmail_connected: true` in the `brands` table.

**Fix**: In `v2_tickets.py` approve-ai endpoint, add the Gmail send call using the same
pattern as `tickets.py` send-reply. Return clear success/error JSON.

---

## Item F — Knowledge Base UI

**Decision**: Add a "Knowledge Base" tab to `Settings.jsx` that calls the existing
`/api/v1/settings/knowledge-base/*` endpoints.

**Finding**: `saas_settings.py` exposes:
- `POST /api/v1/settings/knowledge-base/upload` — upload text content
- `GET /api/v1/settings/knowledge-base/sources` — list sources
- `DELETE /api/v1/settings/knowledge-base/sources/{source_id}` — delete source

`Settings.jsx` currently has two tabs: Account and AI Mode. No KB tab exists.

**Fix**: Add a third tab and a self-contained KB component inline in Settings.jsx.

---

## Item G — Onboarding Wizard

**Decision**: Add a brand count check in `Dashboard.jsx` on mount; redirect to `/onboarding`
when count is zero.

**Finding**: `Onboarding.jsx` (the wizard) exists and is complete. `Dashboard.jsx` does not
check for brands before rendering. The `api/v1/auth/me` or `/api/brands` endpoint can return
brand count. Use `/api/brands` (GET) which returns the tenant's brands array.

**Fix**: In `Dashboard.jsx` useEffect on mount, call `GET /api/brands`. If
`brands.length === 0`, redirect to `/onboarding`.

---

## Item H — Email Threading

**Decision**: In `email_poller.py` per-brand processing, check for existing ticket by
`gmail_thread_id` before creating a new one.

**Finding**: `brand_gmail_service.get_new_emails()` already returns `thread_id` per message.
The email_poller constructs a payload with `gmail_thread_id` but passes it to
`message_processor.process_message()` without any pre-check for existing tickets.
The `tickets` table has a `gmail_thread_id` column.

**Fix**: In email_poller, before dispatching to message_processor, query
`supabase_select("tickets", {"gmail_thread_id": f"eq.{thread_id}"})`. If a match exists,
append the message to that ticket instead of creating a new one.

---

## Item I — Browser Notifications

**Decision**: Wire the existing `useNotifications` hook in Dashboard.jsx to notify on new
tickets and escalated actions.

**Finding**: `useNotifications.js` is fully implemented. It exposes `notify(title, body)`.
It is not imported or called anywhere. Dashboard polls stats — on poll, compare previous
and current counts to detect new tickets.

**Fix**: Import `useNotifications` in Dashboard.jsx, request permission on mount, and call
`notify()` when ticket count increases.
