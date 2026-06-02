---
description: "Task list for Resolv MVP Completion — 004-resolv-completion"
---

# Tasks: Resolv MVP Completion

**Branch**: `004-resolv-completion` | **Date**: 2026-05-13
**Input**: `specs/004-resolv-completion/plan.md`, `spec.md`, `research.md`, `data-model.md`

**Format**: `- [ ] [TaskID] [P?] [Story?] Description — file: path/to/file.ext`
**[P]** = parallelizable (different files, no incomplete dependency)
**[US#]** = maps to user story in spec.md

---

## MILESTONE 1: Core Fixes

> After this milestone the product works end-to-end: brand owners can log in, see real
> data, and send replies via Gmail.

---

## Phase 1: Auth Middleware Fix (Item A)

**Goal**: v1 JWT tokens (sub = tenant_id) accepted by ALL protected routes.

**Independent Test**:
```bash
TOKEN=$(curl -s -X POST http://localhost:8001/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"you@test.com","password":"password123"}' | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $TOKEN" http://localhost:8001/api/v1/actions/stats
# Expected: 200 (not 401)
```

- [x] T001 [US1] Add v1 token fallback in `get_current_user` in `backend/src/api/middleware/auth_middleware.py` — after `get_user_context(sub)` returns None, call `auth_service.get_tenant(sub)` and construct a `UserContext` from the tenant row; raise 401 only if both lookups fail

  **Verify**: `curl -H "Authorization: Bearer <v1_token>" http://localhost:8001/api/v2/tickets` returns 200 not 401

- [x] T002 [US1] Apply same v1 fallback to `get_optional_auth` in `backend/src/api/middleware/auth_middleware.py` — mirrors T001 but returns None instead of raising 401

  **Verify**: Routes using `get_optional_auth` no longer log "No JWT verification method available"

- [x] T003 [P] [US1] Confirm `SUPABASE_JWT_SECRET` fallback chain in `backend/src/services/supabase_auth_service.py` line ~80 reads `os.getenv("SUPABASE_JWT_SECRET", os.getenv("JWT_SECRET", os.getenv("SECRET_KEY", "")))` — edit if not already correct

  **Verify**: `docker compose logs api` shows no "No JWT verification method available" errors after login

**Checkpoint**: v1 tokens accepted on all routes. Proceed to T004.

---

## Phase 2: Worker Stability (Item D)

**Goal**: Email polling worker never crashes; per-brand errors are isolated.

**Independent Test**:
```bash
docker compose logs email_poller --follow
# Inject error: temporarily set wrong Gmail creds for one brand in DB
# Expected: error logged, polling continues, no process exit
```

- [x] T004 [P] [US3] In `backend/src/channels/email_poller.py`, wrap the per-brand iteration inside `_poll_all_inboxes` (or equivalent method) in try/except — catch `Exception as e`, log `f"[EmailPoller] Brand {brand.get('id')} poll error: {e}"`, and continue to next brand

  **Verify**: One broken brand credential logs an error but other brands continue polling

- [x] T005 [P] [US3] In `backend/src/channels/email_poller.py`, add `await asyncio.sleep(5)` inside the outer exception handler in `_polling_loop` — so a full cycle failure waits 5s before retry instead of tight-looping

  **Verify**: `docker compose logs email_poller` shows consistent ~30s intervals even after an error event

**Checkpoint**: Worker stays alive under any Gmail error condition. Proceed to T006.

---

## Phase 3: Ticket List Fields (Item C)

**Goal**: Every ticket in the list has a non-null `channel` and `customer_email` field.

**Independent Test**:
```bash
curl -s http://localhost:8001/api/tickets | python -c "
import sys, json
tickets = json.load(sys.stdin)
for t in tickets:
    assert t.get('channel'), f'Missing channel on {t[\"id\"]}'
    assert t.get('customer_email'), f'Missing customer_email on {t[\"id\"]}'
print('All tickets have channel and customer_email')
"
```

- [x] T006 [US1] In `backend/src/api/routes/tickets.py` `list_tickets` handler (line ~21), after fetching tickets add a transform loop:
  ```python
  for t in tickets:
      if not t.get('channel'):
          t['channel'] = 'email'
  ```
  Do not change any other logic.

  **Verify**: `GET http://localhost:8001/api/tickets` — every object has `"channel": "email"` (or whichever value is stored) and `"customer_email"` is populated

**Checkpoint**: Ticket list fields complete. Admin console Tickets page shows Channel and Sender columns populated.

---

## Phase 4: Dashboard Stats (Item B)

**Goal**: Dashboard shows real Active Conversations count and AI Handled %.

**Independent Test**: Log in, submit 1 test ticket via `/support/submit`, reload dashboard → Active Conversations ≥ 1.

- [x] T007 [US1] In `ai-ops-console/src/api/services.js`, replace the `getStats()` function body with calls to `GET /api/tickets` and `GET /api/v1/actions/stats`:
  ```javascript
  getStats: async () => {
    const [ticketsRes, actionsRes] = await Promise.all([
      client.get('/api/tickets').catch(() => ({ data: [] })),
      client.get('/api/v1/actions/stats').catch(() => ({ data: {} })),
    ]);
    const tickets = Array.isArray(ticketsRes.data) ? ticketsRes.data : [];
    const a = actionsRes.data || {};
    const open = tickets.filter(t => t.status === 'open' || !t.status);
    const resolved = tickets.filter(t => t.status === 'resolved');
    const aiHandledPct = tickets.length > 0
      ? Math.round((resolved.length / tickets.length) * 100) : 0;
    return {
      activeConversations: open.length,
      totalConversations: tickets.length,
      escalatedChats: tickets.filter(t => t.status === 'escalated').length,
      investorLeads: a.pending_count ?? 0,
      studentLeads: a.total ?? 0,
      aiHandledPct,
      avgLatency: 0,
    };
  },
  ```

  **Verify**: Dashboard Active Conversations matches `curl http://localhost:8001/api/tickets | jq '[.[] | select(.status=="open")] | length'`

- [x] T008 [US1] In `ai-ops-console/src/api/services.js`, replace `getConversations()` to call `GET /api/tickets` instead of `/admin/conversations`:
  ```javascript
  getConversations: async (params = {}) => {
    const res = await client.get('/api/tickets', { params }).catch(() => ({ data: [] }));
    return Array.isArray(res.data) ? res.data : res.data?.tickets || [];
  },
  ```

  **Verify**: Tickets page loads without 404 errors in browser console

- [x] T009 [US1] In `ai-ops-console/src/api/services.js`, replace `getConversationMessages()`, `takeoverConversation()`, `releaseConversation()`, `sendAdminMessage()` to use `/api/tickets/*` endpoints per the plan.md Item E and Item C design:
  - `getConversationMessages(id)` → `GET /api/tickets/${id}`
  - `takeoverConversation(id)` → `PATCH /api/tickets/${id}` with `{ escalate: true }`
  - `releaseConversation(id)` → `PATCH /api/tickets/${id}` with `{ status: 'open' }`
  - `sendAdminMessage(id, text)` → `POST /api/tickets/${id}/send-reply` with `{ body: text }`

  **Verify**: No remaining calls to `/admin/*` routes in services.js

**Checkpoint**: Dashboard shows real data. Tickets load. Proceed to T010.

---

## Phase 5: Approve-AI Email Send (Item E)

**Goal**: Clicking "Approve & Send" on a ticket actually sends the email via Gmail.

**Independent Test**: Open a ticket with an AI draft → click Approve → customer inbox receives email → ticket status shows "resolved".

- [x] T010 [US2] In `backend/src/api/routes/v2_tickets.py`, inside the `approve_ai_response` handler (around line 448), after updating the ticket's `human_approved` field, add Gmail send logic:
  ```python
  from src.services.brand_gmail_service import brand_gmail_service
  brand_id = ticket.get("brand_id") or ticket.get("store_id")
  if brand_id:
      brands = supabase_select("brands",
          {"id": f"eq.{brand_id}", "gmail_connected": "is.true"})
      if brands:
          reply_body = ticket.get("ai_response") or ticket.get("ai_draft")
          subject = ticket.get("subject", "Support")
          send_result = await brand_gmail_service.send_email(
              brands[0],
              ticket["customer_email"],
              f"Re: {subject}" if not subject.startswith("Re:") else subject,
              reply_body
          )
          if send_result.get("success"):
              supabase_update("tickets", {"id": f"eq.{ticket_id}"},
                  {"status": "resolved", "email_sent": True,
                   "email_sent_at": datetime.now(timezone.utc).isoformat()})
              return {"success": True, "message": "AI response approved and email sent"}
          return {"success": False,
                  "error": send_result.get("error", "Gmail send failed")}
      return {"success": False,
              "error": "No Gmail connected. Go to Brands → Connect Gmail first."}
  return {"success": False, "error": "Ticket has no associated brand"}
  ```
  Add required imports at top of file if not already present.

  **Verify**: `POST /api/v2/tickets/{id}/approve-ai` with valid token → returns `{"success": true}` → ticket `email_sent` = true in DB

- [x] T011 [P] [US2] Check that the admin console "Approve & Send" button in `ai-ops-console/src/pages/` (TicketDetail or equivalent) calls `POST /api/tickets/{id}/send-reply` or `POST /api/v2/tickets/{id}/approve-ai` — find the component, confirm the endpoint, update if it still calls a non-existent route

  **Verify**: Browser network tab shows correct POST request when approve is clicked; no 404

- [x] T012 [P] [US2] In `ai-ops-console/src/pages/` ticket detail component, add explicit error display for the approve action — if response `success: false`, show `error` field text to the user (not just a console.error)

  **Verify**: Approve with no Gmail connected → red error message "No Gmail connected. Go to Brands → Connect Gmail first." visible in UI

**Checkpoint ✅ MILESTONE 1 COMPLETE**: Auth works, worker is stable, tickets show fields, dashboard shows real counts, email sending works. Product is functional end-to-end.

---
---

## MILESTONE 2: Missing Features

> After this milestone the product is complete: knowledge base, onboarding, threading, and notifications.

---

## Phase 6: Knowledge Base UI (Item F)

**Goal**: Brand owners can upload, view, and delete knowledge base documents from Settings.

**Independent Test**: Settings → Knowledge Base tab → upload "Test Policy" with content "Our return policy is 30 days." → appears in list → delete → disappears.

- [x] T013 [US4] In `ai-ops-console/src/pages/Settings.jsx`, add a third tab button "Knowledge Base" to the existing tab list (alongside Account and AI Mode), with `onClick={() => setActiveTab('knowledge')}` and matching active styling

  **Verify**: Settings page shows 3 tabs; clicking Knowledge Base does not crash the page

- [x] T014 [US4] In `ai-ops-console/src/pages/Settings.jsx`, add a `KnowledgeBaseTab` section rendered when `activeTab === 'knowledge'` — include: title `<input>`, content `<textarea>`, Upload `<button>`, and a source list area. Use inline styles matching the existing component style (no Tailwind, no new libraries)

  **Verify**: Knowledge Base tab renders the upload form with all three elements

- [x] T015 [US4] Wire the Upload button in the Knowledge Base tab to `POST /api/v1/settings/knowledge-base/upload` with `{ title, content }` — on success, prepend the new source to the list state; show loading state on button during request; show error message if request fails

  **Verify**: Upload a document → it appears in list without page reload; button shows "Uploading..." during request

- [x] T016 [US4] Wire the source list to `GET /api/v1/settings/knowledge-base/sources` on tab mount — show loading spinner while fetching; show "No documents yet. Upload your first document above." when empty; show error message on fetch failure

  **Verify**: Sources list shows existing documents on tab open; empty state shows when no documents exist

- [x] T017 [US4] Add a Delete button per source row wired to `DELETE /api/v1/settings/knowledge-base/sources/{id}` — on success, remove that source from list state; show confirmation (inline text or alert) before deleting

  **Verify**: Delete button removes the source from the list immediately; API call returns 200

**Checkpoint**: Knowledge Base tab is fully functional with real data.

---

## Phase 7: Onboarding Wizard (Item G)

**Goal**: New users with zero brands see the wizard; existing users go straight to dashboard.

**Independent Test**: Register new account → navigate to `/dashboard` → wizard appears. Log in as user with connected brand → dashboard loads directly.

- [x] T018 [US5] In `ai-ops-console/src/pages/Dashboard.jsx`, add a `useEffect` that on component mount calls `GET /api/brands` (or `GET /api/v1/auth/me` if brands list is embedded in profile) — if the response contains an empty brands array, call `navigate('/onboarding')` using react-router-dom's `useNavigate`

  ```javascript
  useEffect(() => {
    const checkBrands = async () => {
      try {
        const res = await client.get('/api/brands');
        const brands = res.data?.brands || res.data || [];
        if (Array.isArray(brands) && brands.length === 0) {
          navigate('/onboarding');
        }
      } catch (e) {
        // brands check failed — stay on dashboard, don't redirect
      }
    };
    checkBrands();
  }, []);
  ```

  **Verify**: New account → dashboard → browser redirects to `/onboarding` within 1 second

- [x] T019 [US5] Confirm `ai-ops-console/src/pages/Onboarding.jsx` is registered in the router (`ai-ops-console/src/App.jsx` or equivalent) at path `/onboarding` — add it if missing

  **Verify**: Navigating to `http://localhost:5173/onboarding` renders the wizard (not a 404 or blank screen)

**Checkpoint**: Onboarding wizard appears for new users; existing users skip it.

---

## Phase 8: Email Threading (Item H)

**Goal**: Customer replies to existing emails append to the original ticket, not create new ones.

**Independent Test**: Submit ticket → approve and send reply → reply from customer email → admin console shows both messages on same ticket; no new ticket created.

- [x] T020 [US6] In `backend/src/channels/email_poller.py`, in the per-brand email processing method (where new emails are dispatched to `message_processor`), add thread matching before dispatching:
  ```python
  thread_id = email_data.get("thread_id") or email_data.get("gmail_thread_id")
  if thread_id:
      existing = supabase_select("tickets",
          {"gmail_thread_id": f"eq.{thread_id}", "status": "neq.resolved"})
      if existing:
          ticket = existing[0]
          msgs = ticket.get("messages") or []
          msgs.append({
              "from": email_data.get("sender", ""),
              "body": email_data.get("body", ""),
              "received_at": datetime.now(timezone.utc).isoformat(),
              "direction": "inbound"
          })
          supabase_update("tickets", {"id": f"eq.{ticket['id']}"},
              {"messages": msgs, "status": "open",
               "updated_at": datetime.now(timezone.utc).isoformat()})
          logger.info(f"[EmailPoller] Appended reply to ticket {ticket['id']}")
          continue  # skip normal new-ticket creation
  # existing new-ticket dispatch code here
  ```
  Import `supabase_select`, `supabase_update`, and `datetime` at the top of the file if not already imported.

  **Verify**: `supabase_select("tickets", {"gmail_thread_id": "eq.<thread_id>"})` returns the original ticket after a reply arrives; ticket count in DB does not increase

**Checkpoint**: Email threading works. Replies go to existing tickets.

---

## Phase 9: Browser Notifications (Item I)

**Goal**: Brand owners receive native OS notifications when new tickets arrive.

**Independent Test**: Grant notification permission in browser → submit test ticket from another tab → OS notification appears within 10 seconds.

- [x] T021 [US7] In `ai-ops-console/src/pages/Dashboard.jsx`, import `useNotifications` from `../hooks/useNotifications` and call `requestPermission()` in a `useEffect` on mount (runs once)

  **Verify**: On first visit to dashboard, browser shows notification permission prompt

- [x] T022 [US7] In `ai-ops-console/src/pages/Dashboard.jsx`, add a `useRef` to track previous ticket count (`prevTicketCount`). In the stats polling callback (wherever `getStats()` result is consumed), compare `stats.activeConversations` to `prevTicketCount.current` — if higher, call `notify('New Ticket', 'A new customer ticket is waiting for your response.')` then update the ref

  **Verify**: Submit a ticket while dashboard is open → OS notification appears without page refresh

- [x] T023 [P] [US7] In `ai-ops-console/src/pages/Dashboard.jsx`, also notify when `stats.investorLeads` (pending actions) increases above previous value — call `notify('Action Required', 'A new action needs your approval.')`

  **Verify**: Approve-ai creates a new pending action → notification fires

**Checkpoint ✅ MILESTONE 2 COMPLETE**: All 9 items done. Product is complete.

---

## Phase 10: Polish

- [x] T024 [P] Remove any remaining `/admin/*` route references in `ai-ops-console/src/api/services.js` — search for `/admin/` string and replace or delete

  **Verify**: `grep -n "/admin/" ai-ops-console/src/api/services.js` returns zero results

- [x] T025 [P] Run through `specs/004-resolv-completion/quickstart.md` verification checklist top-to-bottom and confirm all 9 items pass

  **Verify**: All 9 `curl` and manual checks in quickstart.md return expected results

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (Auth)       ──── MUST complete first — unblocks everything
Phase 2 (Worker)     ──── Can run in parallel with Phase 1 (different files)
Phase 3 (Fields)     ──── Depends on Phase 1 complete
Phase 4 (Dashboard)  ──── Depends on Phase 1 complete
Phase 5 (Email Send) ──── Depends on Phase 1 complete
                          ─── MILESTONE 1 ───
Phase 6 (KB UI)      ──── Depends on Milestone 1 (auth must work for API calls)
Phase 7 (Onboarding) ──── Depends on Milestone 1 (brands API needs auth)
Phase 8 (Threading)  ──── Independent of Milestone 1 (pure backend, no auth)
Phase 9 (Notifs)     ──── Depends on Phase 4 (uses stats polling)
Phase 10 (Polish)    ──── After all phases complete
```

### Parallel Opportunities

Phases 2, 3, 4, 5 can run in parallel **after Phase 1 is complete** (all different files):
- T004/T005 → `email_poller.py` (backend)
- T006/T007/T008/T009 → `services.js` (frontend)
- T010/T011/T012 → `v2_tickets.py` + ticket detail component (backend + frontend)

Within Milestone 2:
- T013–T017 (KB UI) and T018–T019 (Onboarding) and T020 (Threading) can all run in parallel

---

## Implementation Strategy

### MVP (Milestone 1 only — product works)

1. T001, T002, T003 — Auth fix (Phase 1)
2. T006, T007, T008, T009 — Dashboard + Ticket list (Phase 4 in parallel)
3. T010, T011, T012 — Email send (Phase 5)
4. Validate with quickstart.md Milestone 1 checks

### Full Completion (Milestone 1 + 2)

Continue sequentially: T013→T017 (KB), T018→T019 (Onboarding), T020 (Threading), T021→T023 (Notifications), T024→T025 (Polish)

---

## Task Summary

| Milestone | Phase | Tasks | Story |
|-----------|-------|-------|-------|
| 1 | Auth Middleware | T001–T003 | US1 |
| 1 | Worker Stability | T004–T005 | US3 |
| 1 | Ticket Fields | T006 | US1 |
| 1 | Dashboard Stats | T007–T009 | US1 |
| 1 | Email Send | T010–T012 | US2 |
| 2 | Knowledge Base UI | T013–T017 | US4 |
| 2 | Onboarding Wizard | T018–T019 | US5 |
| 2 | Email Threading | T020 | US6 |
| 2 | Browser Notifs | T021–T023 | US7 |
| — | Polish | T024–T025 | — |

**Total tasks**: 25
**Milestone 1**: 12 tasks
**Milestone 2**: 11 tasks
**Polish**: 2 tasks
