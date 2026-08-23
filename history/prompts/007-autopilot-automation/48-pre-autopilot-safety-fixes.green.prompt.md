---
id: 48
title: Pre-Autopilot Safety Fixes
stage: green
date: 2026-08-22
surface: agent
model: claude-sonnet-5
feature: 007-autopilot-automation
branch: main-42s6pf
user: aicoders123@gmail.com
command: none
labels: ["security-fix", "shopify-oauth-scope", "product-recommendations", "brand-actions", "pre-autopilot"]
links:
  spec: specs/007-autopilot-automation/research.md
  ticket: null
  adr: null
  pr: null
files:
 - backend/src/api/routes/brand_actions.py
 - backend/src/services/multi_brand_actions.py
 - backend/tests/test_brand_actions_security.py
 - specs/007-autopilot-automation/pre-autopilot-safety-fixes.md
tests:
 - backend/tests/test_brand_actions_security.py (5 new tests, all passing)
 - backend/tests/test_actions_brand_isolation.py (re-run, still passing)
 - backend/tests/test_product_recommendations.py + test_product_discovery.py (33 passed, verification only)
 - full backend suite: 747 passed
---

## Prompt

tResolv — Pre-Autopilot Safety Fixes

The previous research task is complete.

Do NOT implement Autopilot yet.

Do NOT redo the previous research unless needed to verify the two specific findings below.

Critical blockers found

The research identified:

1. /api/brand-actions/approve/{id} appears to have no authentication check and is live in production.
2. The default Shopify OAuth scope requested by the current code does not include write_orders.

These must be investigated and fixed/confirmed before any Autopilot implementation.

Task 1 — Secure the approval endpoint: trace /api/brand-actions/approve/{id}, every route that can approve an action, frontend callers, authentication dependencies, tenant/brand ownership checks, whether the endpoint is actually reachable in production, whether another route already provides the correct secure implementation. If real: add proper authentication, enforce tenant/brand ownership, preserve the existing atomic approval state machine, prevent approval of another merchant's action, prevent duplicate execution, do not weaken existing human approval requirements. Add regression tests specifically attempting: unauthenticated approval, authenticated wrong-tenant approval, valid merchant approval, duplicate approval.

Task 2 — Shopify OAuth scope audit: trace the actual OAuth flow, determine exactly which scopes are requested, compare against implemented/planned mutations (cancellation, refunds, address changes, customer/order info, fulfillment/tracking, product info, inventory, future recommendation features). Do NOT blindly add write_orders — verify from actual Shopify API operations whether required. If genuinely missing, identify exactly which operation requires it, determine whether existing merchants would need reauthorization, do NOT automatically change production OAuth scopes without reporting migration/re-authorization impact.

Task 3 — Verify product recommendations: focused verification only (not a redesign). Check product search, details, variants, prices, inventory, images, URLs, recommendation tool usage, tenant/store scoping. Specifically test: "Do you have anything similar?", "Show me something like this", "Do you have this in another color?", "What about a smaller size?", "What would you recommend with this?" Identify only real gaps.

Task 4 — Shopify order/customer changes research: inspect the current integration for shipping address changes, customer name changes, customer email changes, order contact info changes, any other useful mutation Shopify supports. Report table: Capability | Shopify API operation | Scope | Currently implemented | Safe to implement | Human approval required. Important: an order's shipping address is a snapshot — do not accidentally modify the customer's default address when the customer only wants to change one order.

STOP — do NOT implement Autopilot, automatic refunds/cancellations/address changes, new OAuth scopes, or production migrations unless explicitly required to fix a confirmed security issue. This task is primarily: fix confirmed security blockers + verify existing capabilities + produce the implementation map.

Testing: run targeted approval-authorization tests, targeted Shopify integration tests, recommendation tests, full backend suite. Run frontend build if frontend code changes.

Report only: (1) Confirmed security bugs, (2) Fixes made, (3) Shopify scope findings, (4) Recommendation findings, (5) Order/customer mutation findings, (6) Tests, (7) Full-suite result, (8) Frontend build result, (9) Anything that blocks the next Autopilot phase. Do not start Autopilot implementation in this task.

## Response snapshot

**Task 1 (fixed):** Traced `/api/brand-actions/*` and found the finding was real and broader than scoped — every route in `brand_actions.py` (not just `/approve`) had zero authentication despite being live-registered in `main.py` and confirmed unused by the dashboard frontend (safe to secure with no UI breakage). Fixed by adding `Depends(require_agent_or_admin)` plus an explicit `context.brand_ids` ownership check to all 9 routes, reusing the exact pattern already proven in `v2_actions.py`/`test_actions_brand_isolation.py`. Separately found and fixed a real check-then-act race in `multi_brand_actions.py`'s `approve_action()`/`reject_action()` (no atomic claim existed at all, unlike the already-fixed `actions_service.py`/`v2_actions.py`) by adding the same `status=eq.pending` conditional-UPDATE claim pattern, plus fixed several early-return paths that would have left a claimed row stuck at "approved" forever with no way to retry. Added `test_brand_actions_security.py` with the four exact scenarios requested (unauthenticated, wrong-tenant, valid-merchant, duplicate-approval) — all 5 tests pass, and re-ran `test_actions_brand_isolation.py` plus related hardening suites to confirm no regression to the parallel `actions` table's already-secured flow.

**Task 2 (verified, not shipped):** Corrected the prior research's file citation (`shopify_oauth.py:94`, not `shopify_auth.py`). Confirmed `write_orders` and `write_draft_orders` (a separate scope, needed specifically for the exchange flow) are genuinely missing from the default requested scope, verified operation-by-operation against Shopify's access-scope model. Confirmed widening the scope is not silent for existing merchants (requires per-brand reconnect through an already-existing UI flow) and identified two gaps that would need closing alongside any scope change: the health-check's `REQUIRED_SCOPES` dict doesn't include write scopes so wouldn't detect the gap, and the action-execution path has no scope-aware 403 handling (unlike the import flow). No scope change shipped, per the task's explicit instruction.

**Task 3 (verified, no redesign):** Ran the existing test suites (33 passed). Traced all five example queries against the live routing code and confirmed two real gaps (pronoun resolution, and a corrected finding that "in another color" queries do fire a tool — the wrong one, with a garbage extracted argument — rather than firing no tool at all as previously described). Independently re-verified tenant scoping and live-data freshness rather than re-asserting the prior audit's claims.

**Task 4 (verified table, nothing implemented):** Exhaustive grep confirmed zero customer-profile-mutation code exists anywhere. Produced the requested capability table. Found the pre-fulfillment address-change guard the prior research assumed was missing is actually already implemented (`shopify_service.py:752-756`) — a correction in tResolv's favor. Found one new, unscoped bug worth a separate ticket: `process_refund()`'s `restock` parameter is dead code that never actually triggers a restock.

Full backend suite: 747 passed (one pre-existing failure due to a missing `sqlparse` test dependency not in `requirements.txt`, confirmed unrelated to this session's changes and resolved by installing it locally to verify). No frontend code was touched, so no frontend build was run. Findings written to `specs/007-autopilot-automation/pre-autopilot-safety-fixes.md`.

## Outcome

- ✅ Impact: Closed a real, live, unauthenticated financial-action-execution and PII-leak surface across an entire API router before it could become a launch blocker for Autopilot; fixed a duplicate-execution race in the same code path; confirmed (without shipping) the exact OAuth scope gap and its re-authorization impact; confirmed product recommendations are genuinely usable with two precisely-characterized gaps; produced a verified Shopify mutation capability matrix and corrected two claims from the prior research (one gap turned out already fixed, one new gap was found).
- 🧪 Tests: 5 new tests in `test_brand_actions_security.py`, all passing; `test_actions_brand_isolation.py` and related hardening suites re-run and still passing; `test_product_recommendations.py`/`test_product_discovery.py` re-run for verification (33 passed); full backend suite 747 passed.
- 📁 Files: `backend/src/api/routes/brand_actions.py`, `backend/src/services/multi_brand_actions.py`, `backend/tests/test_brand_actions_security.py`, `specs/007-autopilot-automation/pre-autopilot-safety-fixes.md`.
- 🔁 Next prompts: OAuth scope widening (with the two accompanying fixes it needs) is ready to be scoped as its own task when the team decides to schedule the merchant-reauthorization work; the readiness-denominator bug and edit-tracking columns from `research.md`'s Phase 0 remain open for a future task.
- 🧠 Reflection: Running the three verification/research tasks (OAuth scope, recommendations, mutation table) as parallel background agents while doing the security fix directly kept the critical-path fix moving without waiting on investigation that didn't touch the same files — and surfaced two useful corrections to the prior session's research (the address-change guard already existing, and the refund restock parameter being dead) that a straight re-read wouldn't have caught as precisely.

## Evaluation notes (flywheel)

- Failure modes observed: initial test-writing pass patched `src.api.routes.brand_actions.supabase_select` (a local, function-scoped import, so nothing to patch at module level) instead of the actual source `src.lib.supabase_client.supabase_select` — caught immediately by AttributeError on test run, fixed before proceeding. Also initially used a non-UUID test action id ("action-1") against a route that validates UUID format, producing a 400 instead of the intended 403 — caught by an unexpected-status assertion failure, fixed by using a real UUID constant.
- Graders run and results (PASS/FAIL): full backend suite (747 passed) serves as the regression grader; the 4 requested regression-test scenarios each map to one passing test by name.
- Prompt variant (if applicable): n/a
- Next experiment (smallest change to try): when writing FastAPI TestClient security tests in this codebase going forward, patch `src.lib.supabase_client.<fn>` directly rather than a route module's local import, and always seed test IDs with real UUIDs where a route validates format — would have avoided both fixes above on the first pass.
