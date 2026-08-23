---
id: 50
title: Autopilot Readiness Plus Category Controls
stage: green
date: 2026-08-22
surface: agent
model: claude-sonnet-5
feature: 007-autopilot-automation
branch: main-42s6pf
user: aicoders123@gmail.com
command: none
labels: ["autopilot-readiness", "category-controls", "dashboard-ui", "customer-messaging", "pre-autopilot"]
links:
  spec: specs/007-autopilot-automation/pre-autopilot-safety-fixes-implementation.md
  ticket: null
  adr: null
  pr: null
files:
 - backend/src/api/routes/v2_brands.py
 - backend/src/agent/customer_success_agent.py
 - backend/src/api/routes/v2_chat_widget.py
 - backend/src/services/return_actions_integration.py
 - backend/tests/test_customer_voice_analytics.py
 - backend/tests/test_no_fabricated_response_time_promises.py
 - dashboard/src/pages/Automation.jsx
 - dashboard/src/pages/CustomerVoice.jsx
 - dashboard/src/App.jsx
 - dashboard/src/components/Sidebar.jsx
 - specs/007-autopilot-automation/readiness-and-category-controls.md
tests:
 - backend/tests/test_customer_voice_analytics.py (18 passed, 7 new)
 - backend/tests/test_no_fabricated_response_time_promises.py (1 new, passing)
 - full backend suite: 781 passed
 - frontend build: npx vite build succeeds
---

## Prompt

tResolv — Autopilot Readiness + Category Controls. We are moving from security/audit work into product implementation. IMPORTANT: DO NOT RE-AUDIT — previous research and security work is complete. Current baseline: full backend suite 774 passed, existing PR #9, write_orders already configured, write_draft_orders required by the exchange Draft Order flow but NOT yet enabled in the Shopify Partner Dashboard, 046_action_edit_tracking.sql exists but NOT applied to production, recommendation follow-ups fixed, Shopify 403/missing-scope handling fixed, Autopilot execution NOT implemented yet, financial actions still require human approval. Do NOT re-audit, redo threat modeling, rewrite architecture, or research Wilmo again.

PHASE 0 — verify (don't apply) two prerequisites: whether write_draft_orders is now enabled in the Shopify Partner Dashboard (report "requires manual Partner Dashboard configuration" if no access — do not invent enablement); whether 046_action_edit_tracking.sql has been applied to production (verify directly if tooling available; do NOT apply automatically unless explicitly authorized, report as deployment prerequisite if not).

PHASE 1 — implement the merchant-facing Autopilot Readiness layer only (no automatic execution). Start with cancellation; structure so refund/exchange can reuse the mechanism later without over-engineering a generic framework now. Use existing readiness analytics/data (from the previous task) wherever possible, extend minimally rather than duplicating. Numbers must come from real backend data, never fabricated. Readiness must account for successful executions, failed Shopify executions, escalations, sufficient sample size, approval/outcome history — a Shopify execution failure must never disappear from the denominator. No invented arbitrary thresholds beyond the existing one.

PHASE 2 — merchant-facing Automation section: Cancellation (Copilot, "Review readiness"), Refunds (Copilot, "Coming soon"), Exchanges (Copilot, "Coming soon"). Never imply Autopilot is active; never show an enabled toggle that does nothing.

PHASE 3 — safe foundation for future activation only ("Enable Cancellation Autopilot"), gated conceptually on: readiness threshold met, sufficient verified outcomes, explicit merchant approval, category-specific scope, existing policy checks/tenant isolation/action state machine/idempotency/live-Shopify verification/kill switch. Do NOT wire the final automatic execution path. Control stays disabled/not-yet-available when requirements aren't met.

PHASE 4 — concise "why isn't Autopilot ready" explanations (not_ready / almost_there / ready_for_review framing from the task's own examples), no internal implementation details exposed.

PHASE 5 — concise value-prop copy: "Train → Verify → Approve → Automate," never claiming Luna self-trains unless it actually does.

PHASE 6 — fix the customer-facing pending-approval wording problem: truthful, no promised specific response time unless actually guaranteed. Avoid "A human will reply shortly." Use wording like "I've sent this to our team for approval. We'll follow up once it's reviewed." No internal action IDs/policy dumps/system prompts exposed.

PHASE 7 — use the existing dashboard architecture; find the most natural existing location (near Customer Voice/Usage-Plan/Actions-Escalations); no new dashboard architecture, no generic AI-dashboard garbage, no fake charts.

Tests requested (10 items): readiness uses real execution outcomes; Shopify failures remain in the denominator; insufficient sample size prevents readiness; different action categories don't contaminate each other's metrics; merchant cannot activate automation without readiness; merchant approval required before any future activation; existing Copilot behavior remains unchanged; financial actions still require human approval; tenant isolation remains intact; no automatic Shopify mutation occurs from this task. Run targeted tests first, then full backend suite once; run npm run build if frontend files change.

Hard stop conditions: enabling Autopilot execution, changing Shopify OAuth scopes, applying a production migration, changing authentication, changing Supabase RLS/security policies, changing secrets, bypassing the existing approval state machine, automatically cancelling/refunding/exchanging orders — stop and report instead of guessing/sneaking these in.

Final response requested (no audit essay, no repeated security report, no speculative future architecture): (1) What changed, (2) Readiness logic, (3) Dashboard/UI changes, (4) Category controls, (5) Prerequisite status, (6) Files changed, (7) Tests, (8) Full backend result, (9) Frontend build result, (10) Migration/deployment requirements, (11) Anything still blocked.

## Response snapshot

**Phase 0:** Verified both prerequisites without applying anything. `write_draft_orders` confirmed still absent two independent ways — the corrected app scope list from the prior task, and (going further) a live query of the actually-granted `shopify_granted_scopes` on the one connected production brand, which also doesn't include it. `046_action_edit_tracking.sql` confirmed not applied via a direct `information_schema.columns` query against production — neither `was_edited` nor `approved_extracted_data` exist on `actions`. Reported both as prerequisites, applied neither.

**Phase 1 (backend):** Extended `GET /{brand_id}/analytics` with a new `category_readiness.cancellation` object, reusing the exact `cancel_executed`/`cancel_rejected`/`cancel_failed` counts already computed for the existing (denominator-fixed) `autopilot_readiness` field — no duplicate analytics system, no new query. Added a `status` classification (`not_ready`/`almost_there`/`ready_for_review`) using only the existing minimum-sample threshold and the real failure count — no invented number. Worked out from the task's own examples that `escalated` = human rejections (normal, not a red flag) while Shopify execution *failures* specifically are what separate "almost there" from "ready," matching the task's worked example (47 requests, 2 escalated, still "Ready for review"). `autopilot_readiness` (read by the existing `CustomerVoice.jsx` card) left untouched for backward compatibility.

**Phases 2–5 (frontend):** New `Automation.jsx` page, wired into the existing route/sidebar pattern (placed next to Customer Voice, no new architecture). Category control rows for Cancellation (working "Review readiness" toggle)/Refunds/Exchanges (genuinely disabled "Coming soon," not a fake-enabled control). Readiness detail renders real stat blocks + status badge + plain-language "why not ready" explanation, all sourced from `category_readiness.cancellation`. "Enable Cancellation Autopilot" control is `disabled` unless readiness is `ready_for_review`, and even then explicitly labeled not-yet-available — no execution wired to it at all. Value-prop copy added ("Train → Verify → Approve → Automate") without any self-training claim. Replaced `CustomerVoice.jsx`'s old permanently-disabled "Review Autopilot" dead-end button with a working link to the new page, avoiding two different UI treatments of the same data.

**Phase 6:** Audited every customer-facing "action pending approval" message across `return_actions_integration.py` (11 instances), two AI-fallback replies in `customer_success_agent.py`, and the human-takeover notice in `v2_chat_widget.py`. Found and fixed fabricated/vague timing ("within 2 hours," "within 24 hours," "shortly," "soon") with no real SLA backing anywhere in the codebase, replacing with the task's own truthful pattern while preserving every real non-timing detail. Deliberately left alone two instances describing real external-system timing (bank refund posting, carrier tracking activation) since those aren't promises about tResolv's own team speed. Added a source-scanning regression test so a reintroduced fabricated promise fails a test rather than requiring every message branch to be individually exercised.

**Phase 7:** Confirmed via placement next to Customer Voice, reusing `Sidebar.jsx`'s existing NAV array pattern and `App.jsx`'s existing route pattern exactly — no new dashboard architecture.

Tests: 6 new `category_readiness` tests plus 1 new "never writes/calls Shopify" test in `test_customer_voice_analytics.py` (18 total, all passing), 1 new Phase-6 wording regression test (passing). Full backend suite: 781 passed. Frontend build: succeeds. Re-ran every existing action-approval/execution/tenant-isolation test file unchanged — all still pass, confirming Copilot behavior, the approval state machine, idempotency, and tenant isolation are untouched.

## Outcome

- ✅ Impact: Merchants now have a real, data-backed view of whether Luna is ready for cancellation Autopilot, framed honestly (never implying automation is active, never showing a working-looking control that does nothing), plus a genuine customer-messaging fix removing 11 fabricated response-time promises across every action type — all without adding a single line of actual automatic-execution code.
- 🧪 Tests: 8 new tests (7 backend readiness/mutation-guard tests, 1 wording-regression test); full suite 781 passed; frontend build clean.
- 📁 Files: 1 backend route file extended, 3 backend files with wording fixes, 2 new backend test files, 1 new frontend page, 3 existing frontend files updated for routing/nav/teaser-link, 1 new findings doc.
- 🔁 Next prompts: enabling `write_draft_orders` in the Shopify Partner Dashboard (a business/platform action, not code) and applying `046_action_edit_tracking.sql` both remain open prerequisites before any category besides cancellation-readiness-review could realistically move forward; actual Autopilot execution wiring (explicitly out of scope here) would be the next task once the team decides to act on a `ready_for_review` category.
- 🧠 Reflection: Reading the task's own worked UI example carefully (47 requests, 2 escalated, still "Ready for review") before writing the status-classification logic caught what would otherwise have been a wrong first guess (treating any rejection as a readiness blocker) — worth re-reading a spec's own examples as executable test cases before implementing the rule they're demonstrating, not just as illustrative color.

## Evaluation notes (flywheel)

- Failure modes observed: none required a fix-and-retest cycle this session — the status-classification design was checked against the task's own worked example before writing code (see reflection above), which avoided a wrong implementation rather than catching it after the fact.
- Graders run and results (PASS/FAIL): full backend suite (781 passed) and `vite build` (clean) as the regression graders; every one of the task's 10 named test scenarios maps to a passing test (several — Copilot unchanged, financial actions still need approval, tenant isolation — verified by confirming pre-existing suites still pass unmodified, since no code path they exercise was touched).
- Prompt variant (if applicable): n/a
- Next experiment (smallest change to try): when a task provides a worked UI example alongside abstract rules, derive the classification logic from the example first (as done here) rather than from the abstract rule description alone — the abstract text alone ("escalations," "failures") was ambiguous about which counted against readiness; the concrete numbers resolved it unambiguously.
