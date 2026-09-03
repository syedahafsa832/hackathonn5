---
id: 49
title: Refund identity mismatch and partial amount fix
stage: green
date: 2026-09-03
surface: agent
model: claude-sonnet-5
feature: none
branch: main-oi7oo7
user: aicoders123@gmail.com
command: remote-task
labels: ["refund", "identity-verification", "actions-manager", "bugfix", "regression"]
links:
  spec: null
  ticket: null
  adr: null
  pr: https://github.com/syedahafsa832/hackathonn5/pull/32
files:
 - backend/src/agent/customer_success_agent.py
 - backend/src/services/actions_manager.py
 - backend/src/services/actions_service.py
 - backend/src/services/return_actions_integration.py
 - backend/src/services/shopify_service.py
 - backend/src/workers/message_processor.py
 - backend/tests/test_refund_currency_confirmation.py
 - backend/tests/test_refund_ownership_and_partial_amount.py
 - backend/tests/test_address_change_no_repeat_info.py
 - backend/tests/test_refund_policy.py
 - dashboard/src/components/ActionCard.jsx
 - dashboard/src/pages/Actions.jsx
tests:
 - backend/tests/test_refund_ownership_and_partial_amount.py (new, 26 tests, pass)
 - backend/tests/test_refund_currency_confirmation.py (new, pass)
 - backend/tests/test_refund_policy.py (updated to assert hard-block contract, pass)
 - backend/tests/test_address_change_no_repeat_info.py (updated wording assertions, pass)
 - backend/tests/test_shopify_order_context_reaches_ai.py (pre-existing unrelated failure, confirmed present before this branch too)
 - Full backend suite (`pytest tests/`, 1520+ tests) run before and after the diff to confirm no unrelated regressions
---

## Prompt

[Continuing from a previous session] Handoff: Fix two production bugs in the tResolv refund workflow:
1. Identity Mismatch Bug — when a customer requests a refund from an email that doesn't match the Shopify order's customer email, Luna was incorrectly staging refund actions and promising team follow-ups that never happened.
2. Partial Refund Bug — when customers requested partial refunds (e.g., "$5 refund"), the extracted dollar amount was never captured or preserved through the approval → Shopify execution pipeline, allowing merchants to accidentally refund full order amounts.

Prior work already identified root causes and implemented most of the fix (actions_manager.py hard-block on identity mismatch, requested-amount extraction/pre-fill in the dashboard, refund currency fix), but was blocked before commit on verifying the partial-refund invariant across a second approval UI surface, and had not yet run the full test suite. Instructed to: complete the two end-to-end invariant checks (identity mismatch + partial refund), verify the second approval UI surface, and commit/push once verification passes.

Follow-up instruction: "Continue from where you left off." (after PR creation, to subscribe to PR activity) and "Continue from where you left off." again (to create this PHR per CLAUDE.md's mandatory PHR policy for every user prompt).

## Response snapshot

Verified the already-committed fix (commit e425d48) end-to-end: confirmed `ActionCard.jsx` (used by Dashboard.jsx/TicketDetail.jsx) and `Actions.jsx`'s own inline card both pre-fill `extracted_data.requested_amount` and remain human-editable before Shopify execution — the "second approval UI component" concern from the handoff was already covered by the existing diff, not a separate `ApprovalAmount.jsx` file (none exists).

Ran the full backend pytest suite (1520 passed / 36 failed) and diffed failures against the pre-fix commit to separate pre-existing/environmental failures (missing Supabase/JWT config in this sandbox — unrelated) from real regressions. Found and fixed one genuine regression introduced by the identity-mismatch hard block: an unverified chat-widget visitor with **no email at all** (`email=""`) was being treated as an email mismatch against any order with an email on file, silently hard-blocking a legitimate order-number-only cancel/refund request that previously reached a human via `staging_required`. Fixed by adding `email` truthiness to the mismatch condition in `actions_manager.py`, symmetric with the existing "order has no email" leniency.

Updated two tests that asserted the intentionally-changed pre-fix behavior (`test_refund_policy.py::test_mismatched_sender_email_still_stages_for_manual_review` → renamed/rewritten to assert the hard-block contract; `test_address_change_no_repeat_info.py::test_ownership_mismatch_wording_states_the_mismatch_not_a_repeat_request` → updated to check the new no-false-escalation wording instead of the old "team will verify ownership" phrase). Re-ran all affected/related test files (identity-mismatch, partial-amount, cancel-order-routing, address-change, and a broad sweep of 16 other cancellation/refund/exchange test files — 217 tests, all pass) to confirm no further regressions.

Committed (`45ef72d`) and pushed `main-oi7oo7`, then opened draft PR #32 against `main` with a summary covering all four fixes (identity hard-block, partial-amount capture, currency fix, and the newly-found empty-email leniency regression) and a test plan. Subscribed to PR activity for #32 so CI/review events wake this session per the drive-to-green workflow.

## Outcome

- ✅ Impact: Refund/cancel workflow no longer stages or executes actions for a genuine sender/order email mismatch (hard block + honest reply, no false escalation promise); customer-stated partial-refund amounts now flow from message → extracted_data → both dashboard approval surfaces → human-approved Shopify mutation; refund confirmations show the order's real currency; and a newly-discovered regression (no-email chat visitors wrongly blocked) is fixed before merge.
- 🧪 Tests: 26 new + 2 updated targeted tests pass; 217-test broader sweep of adjacent cancellation/refund/exchange suites pass; full 1520-test backend suite shows zero regressions attributable to this branch (36 pre-existing/environmental failures unchanged).
- 📁 Files: 5 backend services/agent files, 2 dashboard components, 4 backend test files (2 new, 2 updated) — see files list above.
- 🔁 Next prompts: Monitor PR #32 for CI results and review feedback; address any findings per the drive-to-green workflow.
- 🧠 Reflection: The handoff's "unfinished work" item (verify the second approval UI) turned out to already be resolved by the existing diff — the real remaining risk was an untested edge case (empty sender identity) that only surfaced by diffing the full test suite against the pre-fix baseline rather than trusting the handoff's "121 tests, all passing" claim at face value.

## Evaluation notes (flywheel)

- Failure modes observed: A prior session's confidence claim ("121 tests total, all passing") was not independently verifiable from the handoff alone and, on this environment, pytest/dependencies weren't even installed — re-running from scratch surfaced a genuine regression (empty-email leniency) that a partial re-check would have missed.
- Graders run and results (PASS/FAIL): Full backend pytest suite — PASS (no regressions after fix); targeted identity/partial-amount suites — PASS.
- Prompt variant (if applicable): n/a
- Next experiment (smallest change to try): n/a
