---
id: 51
title: Cancellation Autopilot Execution
stage: green
date: 2026-08-23
surface: agent
model: claude-sonnet-5
feature: 007-autopilot-automation
branch: main-42s6pf
user: aicoders123@gmail.com
command: none
labels: ["cancellation-autopilot", "automatic-execution", "action-lifecycle", "dashboard-ui", "kill-switch"]
links:
  spec: specs/007-autopilot-automation/cancellation-autopilot-execution.md
  ticket: null
  adr: null
  pr: null
files:
 - backend/migrations/047_cancellation_autopilot.sql
 - backend/src/api/routes/v2_brands.py
 - backend/src/services/return_actions_integration.py
 - backend/tests/test_cancellation_autopilot.py
 - dashboard/src/api/services.js
 - dashboard/src/hooks/useApi.js
 - dashboard/src/pages/Automation.jsx
 - specs/007-autopilot-automation/cancellation-autopilot-execution.md
tests:
 - backend/tests/test_cancellation_autopilot.py (19 new, all passing)
 - targeted re-run: test_customer_voice_analytics.py, test_action_lifecycle_safety.py, test_no_fabricated_response_time_promises.py (26 passed, unchanged)
 - full backend suite: 800 passed (781 baseline + 19 new)
 - frontend build: npx vite build succeeds
---

## Prompt

tResolv — Phase 4: Cancellation Autopilot Execution
We now have the Autopilot readiness layer implemented.
Current verified baseline:
* Backend: 781 passed
* Frontend build: passes
* Category readiness exists.
* Cancellation has a readiness UI.
* "Enable Cancellation Autopilot" is currently disabled/not implemented.
* Human approval remains mandatory.
* Existing Shopify live-state verification exists.
* Existing action state machine, idempotency, tenant isolation, policy checks, and audit logging exist.
* `write_orders` is already configured.
* `write_draft_orders` is unrelated to cancellation and should NOT be changed for this task.
* `046_action_edit_tracking.sql` remains unapplied.
* Do NOT re-audit all previous security work.

GOAL
Implement Cancellation Autopilot only.
Do NOT implement Autopilot for:
* refunds
* exchanges
* address changes
* customer profile changes
Those remain Copilot/human-approved.
The merchant must explicitly enable Cancellation Autopilot.

1. Merchant activation
The Automation page currently shows:
Enable Cancellation Autopilot
Turn this into a real merchant-controlled activation flow.
Before activation, verify server-side:
* cancellation readiness is `ready_for_review`
* sufficient real cancellation outcomes exist according to the existing readiness logic
* merchant/tenant is authenticated
* the merchant owns the relevant Shopify connection
* the merchant is entitled to the feature
* the cancellation category is the category being enabled
Never trust a frontend toggle as authorization.
Activation must happen through a dedicated authenticated backend endpoint.
Do NOT use a generic settings update endpoint.
Example conceptual API:
`POST /automation/cancellation/enable`
and:
`POST /automation/cancellation/disable`
Use the project's existing patterns rather than inventing a new architecture.

2. IMPORTANT: Keep human approval intact unless explicitly enabled
Current behavior:
Customer requests cancellation
→ Luna verifies order
→ policy check
→ action staged
→ human approves
→ Shopify cancellation
After merchant explicitly enables Cancellation Autopilot:
Customer requests cancellation
→ Luna verifies order
→ policy check
→ server-side safety checks
→ automatic cancellation
→ Shopify confirms success
→ customer receives success message
If ANY safety check fails:
→ DO NOT cancel
→ create/escalate to human review
There must be no "best effort" automatic cancellation.

3. Hard eligibility rules
Autopilot may only automatically cancel an order when ALL required existing checks pass.
At minimum verify:
* correct tenant/store
* valid authenticated Shopify connection
* order exists
* order belongs to the merchant's Shopify store
* order is still eligible for cancellation
* order is not fulfilled/shipped if existing logic prohibits cancellation
* current store cancellation policy permits it
* current Shopify state is freshly verified
* action is not already executed
* action is not already rejected
* action is not already being processed
* merchant has Cancellation Autopilot enabled
Reuse existing deterministic policy and Shopify verification functions.
Do NOT duplicate them.
Do NOT rely on Luna's response as authorization.

4. Model-independent authorization
This is critical.
Luna must never be able to decide:
"I think this cancellation is safe, so I'll execute it."
The backend must independently decide whether the cancellation is eligible for Autopilot.
The model can identify the customer's intent.
The backend decides whether automatic execution is permitted.
If the model proposes cancellation for an ineligible order:
escalate.

5. Existing action lifecycle
Do NOT bypass `actions_service.py`.
Do NOT create a second cancellation execution path.
Use the existing action lifecycle/state machine wherever possible.
The automatic path should still produce a proper action/audit record so the merchant can see:
Cancellation automatically completed
rather than silently mutating Shopify.
Preserve:
* idempotency
* atomic state transitions
* audit trail
* tenant isolation
* live Shopify verification
* existing failure handling
A duplicate message, webhook, retry, or concurrent worker must NOT cause two Shopify cancellations.

6. Failure behavior
If Shopify rejects/fails the cancellation:
* do NOT tell the customer it succeeded
* do NOT mark the action successful
* preserve the failure reason internally
* escalate to human review
* give the customer truthful wording
Example:
"I couldn't complete the cancellation automatically, so I've sent this to our team for review."
Do not promise:
"A human will respond within 2 hours."
No fabricated response-time promises.

7. Customer success message
When Shopify genuinely confirms cancellation:
For chat, keep it short and natural.
Example:
"Done! Your order #1013 has been cancelled successfully."
For email, use the existing email style and avoid robotic wording.
Never send a success message before confirmed Shopify success.

8. Merchant dashboard
After enabling Autopilot, the Automation page should clearly show:
Cancellation Autopilot
🟢 ON
Luna can automatically cancel eligible orders that meet your store's rules.
47 cancellation requests handled automatically
95.7% successful
2 escalated for review
[Turn off Autopilot]
Again, every number must come from real backend data.
If there is no data, show the actual zero/empty state.
Never fabricate statistics.

9. Explain why something was escalated
For an automatically processed workflow, merchants need visibility.
For example:
Escalated
Order #1013 was not cancelled automatically.
Reason: Order had already shipped.
Or:
Reason: Store cancellation policy did not allow automatic cancellation.
Reuse existing policy/evidence data.
Do not expose raw prompts or internal implementation details.

10. Kill switch
Turning Autopilot OFF must immediately prevent new automatic cancellations.
Existing already-running actions must finish safely according to the existing action lifecycle.
Do NOT cancel or corrupt an already executing Shopify request simply because the merchant switched the setting off.

11. Refunds and exchanges
Do NOT touch their execution behavior.
They must remain human-approved.
The Automation page should continue showing:
Refund Autopilot
Coming soon
Exchange Autopilot
Coming soon
No fake functionality.

12. Tests
Add focused regression tests covering at minimum:
1. Merchant cannot enable Autopilot when readiness is insufficient.
2. Merchant can enable when readiness is sufficient.
3. Unauthorized user cannot enable it.
4. Wrong tenant cannot enable it.
5. Disabled Autopilot never automatically executes cancellation.
6. Enabled Autopilot executes an eligible cancellation exactly once.
7. Duplicate requests cannot execute twice.
8. Ineligible order escalates instead.
9. Shopify failure does not produce success messaging.
10. Shopify failure escalates correctly.
11. Policy failure escalates.
12. Fulfilled/shipped order escalates.
13. Existing Copilot behavior remains unchanged.
14. Refunds remain human-approved.
15. Exchanges remain human-approved.
16. Turning Autopilot off prevents new automatic executions.
17. Audit/action records are created correctly.
18. Tenant isolation remains intact.
Mock Shopify appropriately in unit tests.
Do NOT perform real destructive Shopify cancellations during tests.

13. Frontend
Keep the existing Automation page design.
Improve only what is necessary to support the real state:
* OFF
* Not ready
* Ready for review
* ON
Make the activation confirmation clear.
Example:
Turn on Cancellation Autopilot?
Luna will automatically cancel eligible orders when all of your store's cancellation rules are satisfied.
Anything uncertain will still be sent to your team.
Cancel
Turn on Autopilot
Do not hide the fact that this is an automatic action.

14. STOP CONDITIONS
STOP before implementation if you discover that safe Autopilot requires:
* bypassing the existing approval state machine
* changing Shopify OAuth scopes
* changing authentication
* changing RLS/security policies
* changing secrets
* applying a production migration
* creating a second Shopify cancellation implementation
* removing existing safety checks
Report the blocker instead of hacking around it.
If a migration is genuinely necessary, create it but DO NOT apply it automatically.

15. Execution discipline
Do not perform another giant audit.
Do not reread the entire repository.
Do not research competitors.
Do not rewrite architecture.
Find the existing cancellation flow and extend it with the smallest isolated change possible.
Reuse existing:
* action lifecycle
* Shopify cancellation implementation
* readiness analytics
* policy checks
* tenant checks
* idempotency
* audit logging
* Automation page
Run targeted tests first.
Then run the full backend suite once.
Then run the frontend build.
Finally inspect the diff for unrelated changes.

Final response
Return only:
1. What changed
2. Cancellation Autopilot flow
3. Safety gates
4. Files changed
5. Tests
6. Full backend result
7. Frontend build result
8. Migration/deployment requirements
9. Anything still blocked
No giant audit report.

## Response snapshot

Implemented Cancellation Autopilot as the smallest isolated extension of the existing cancellation flow — no new execution path, no bypass of the approval state machine, no STOP CONDITION triggered.

**Backend — activation (`v2_brands.py`):** Extracted the existing inline readiness computation into `_compute_cancellation_readiness(brand_id)`, called both by `/analytics` (Automation page display) and the two new endpoints. Added `POST /{brand_id}/automation/cancellation/enable` — re-verifies tenant auth (`get_current_tenant`), brand ownership (`_get_owned_brand`), a connected Shopify store, entitlement (`plan_service.check_limit("shopify_actions")`, the same primitive gating real Shopify-executing actions elsewhere), and a fresh server-side readiness recomputation (`status == "ready_for_review"`) — never trusting the frontend's last-rendered readiness object. `POST /.../disable` requires only auth+ownership, no readiness gate, by design (kill switch). Both flip a new `brands.cancellation_autopilot_enabled` boolean.

**Backend — execution hook (`return_actions_integration.py`):** Added `_maybe_autopilot_cancel()`, called from exactly one place — the pre-existing "CANCEL QUEUED" branch, reached only when the order is freshly Shopify-verified, unfulfilled, eligible, and has no merchant free-text cancellation policy requiring human judgment (that case already exits earlier, unconditionally, autopilot-enabled or not). This is structurally the only reachable path with every hard eligibility rule already satisfied — Luna's own output is never consulted for authorization. The hook fetches the brand's `cancellation_autopilot_enabled` flag fresh (fail-closed if unset/missing), and when true, calls `actions_service.approve_action(..., approved_by="autopilot", idempotency_key=f"autopilot-{action_id}")` — the exact function human approval already calls, inheriting the atomic pending→approved claim, live Shopify re-verification, audit trail, and existing failure handling unchanged. Success returns a customer-facing "Done! Your order #N has been cancelled successfully" instruction only after Shopify's real confirmation; failure never claims success, preserves the real error internally, and returns truthful escalation wording with no fabricated response-time promise (locked in by the existing `test_no_fabricated_response_time_promises.py`, re-run and still passing against the new message).

**Frontend (`Automation.jsx`):** Cancellation's category badge now reads real `enabled` state (🟢 Autopilot ON / Copilot). The OFF-state detail keeps the existing readiness card (Not ready / Ready for review) and adds a real "Turn on Cancellation Autopilot?" confirmation dialog with the exact provided copy before calling `/enable`. The ON-state renders a new card sourced entirely from backend data: handled-automatically count, success rate, escalated-for-review count (an honest empty state when zero, never a fabricated number), a short list of recent escalations with their real reason, and a "Turn off Autopilot" button calling `/disable`. Refunds/Exchanges rows are untouched — still genuinely disabled "Coming soon."

**Migration:** `047_cancellation_autopilot.sql` — `ALTER TABLE brands ADD COLUMN IF NOT EXISTS cancellation_autopilot_enabled BOOLEAN DEFAULT false`. Created only, not applied (per STOP CONDITIONS) — safe pre-migration because `supabase_select()`'s plain `SELECT *` returns the column as absent/falsy rather than erroring, so the feature defaults to fully off everywhere until both the migration is applied and a merchant explicitly enables it.

**Tests:** 19 new tests in `test_cancellation_autopilot.py` covering all 18 named scenarios (readiness-gated/entitlement-gated/ownership-gated enable, auth-required, disable always allowed, exactly-once execution with the real `approved_by`/`idempotency_key` asserted, duplicate-request short-circuit before autopilot is ever reached, fulfilled-order and custom-policy escalation without an autopilot attempt, Shopify-failure escalation wording, unchanged Copilot/refund/exchange behavior, tenant-id pass-through). All Shopify interaction is mocked; no real Shopify calls in tests.

## Outcome

- ✅ Impact: Cancellation-only automatic execution, merchant-activated through a dedicated re-verifying endpoint, built entirely on the existing action lifecycle with zero new authorization surface — every hard eligibility rule maps to a pre-existing enforcement point plus one new enabled-flag check.
- 🧪 Tests: 19 new (all 18 named scenarios covered); full suite 800 passed (781 baseline + 19); frontend build clean.
- 📁 Files: 1 migration (unapplied), 1 backend route file extended, 1 backend service file extended, 1 new backend test file, 3 frontend files (API client, hooks, Automation page), 1 new findings doc.
- 🔁 Next prompts: applying migration 047 (and the still-open 046) to production; refund/exchange Autopilot remain explicitly out of scope and un-built, per the task.
- 🧠 Reflection: Tracing every one of the task's "hard eligibility rules" against the existing codebase before writing any code (rather than assuming new checks were needed) showed only one was genuinely missing — the enabled flag itself — which kept the diff to two call sites instead of a parallel eligibility system.

## Evaluation notes (flywheel)

- Failure modes observed: none required a fix-and-retest cycle — all 19 new tests and the full 800-test suite passed on first run after implementation.
- Graders run and results (PASS/FAIL): full backend suite (800 passed) and `vite build` (clean) as regression graders; the existing `test_no_fabricated_response_time_promises.py` source-scan doubled as a grader for the new autopilot escalation message, passing unmodified.
- Prompt variant (if applicable): n/a
- Next experiment (smallest change to try): when a task's numbered "hard eligibility rules" list reads like a checklist, verify each item against the existing codebase individually before designing new code — most were already enforced, and confirming that first is what kept this a two-call-site change instead of a new eligibility subsystem.
