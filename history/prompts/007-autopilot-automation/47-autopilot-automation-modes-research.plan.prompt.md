---
id: 47
title: Autopilot, Automation Modes & Shopify Order Actions Research
stage: plan
date: 2026-08-22
surface: agent
model: claude-sonnet-5
feature: 007-autopilot-automation
branch: main-42s6pf
user: aicoders123@gmail.com
command: none
labels: ["autopilot", "automation-modes", "shopify-actions", "wilmo-research", "readiness", "architecture"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
 - specs/007-autopilot-automation/research.md
tests:
 - none (research/planning task only, no code changes)
---

## Prompt

tResolv — Autopilot, Automation Modes & Shopify Order Actions Research

This is a research + architecture planning task first.

Do NOT implement code yet.

Do NOT re-audit completed security work.

Do NOT change the existing approval system.

Context

tResolv currently requires human approval for sensitive actions such as cancellations and refunds.

That is safe, but it creates a major product problem:

If a merchant has to manually click Cancel in Shopify for every routine cancellation, they could simply open Shopify and do it themselves.

The long-term product must become an AI employee that can eventually resolve eligible support tickets end-to-end, including taking safe Shopify actions.

Research and design how we should get there.

1. Research Wilmo's model — review https://www.wilmo.ai/, /autopilot, /product, /cases. Do not copy their implementation. Understand Autopilot / Copilot / Blocked pattern (AI end-to-end vs. AI proposes+human reviews vs. always-human), and how they describe promotion from Copilot to Autopilot based on merchant ticket performance data. Determine what parts make sense for tResolv.

2. Design tResolv's automation model — category-level automation, merchant controls which categories become autonomous, no global "AI ON/OFF" switch. Recommend safest starting configuration (not assuming the task's example defaults are correct).

3. Design the training → confidence → Autopilot journey — Stage 1 Observe, Stage 2 Copilot (record approved/rejected/edited/CSAT/Shopify success/escalation justification), Stage 3 Readiness (real historical data, do NOT invent numbers), Stage 4 Merchant approval (explicit UI: Review evidence / Enable Autopilot), Stage 5 Autopilot (9-step execution flow: understand → verify ownership → fetch live Shopify state → check policy → check guardrails → execute → verify success → reply → audit).

4. Autopilot must NOT mean unrestricted autonomy — guardrails: tenant ownership, Shopify state verification, merchant policy, action-specific limits, entitlement, customer/order verification, idempotency, action lifecycle protections, audit logging. Luna's model output must never itself authorize an action; backend remains authoritative.

5. Category-specific controls UI — mode, sample size, success rate, approval rate, failure rate, CSAT, recent incidents, last updated, disable/rollback per category.

6. Global kill switch ("Pause Autopilot") plus category-level rollback (e.g. "Pause cancellations" without disabling WISMO).

7. Shopify order mutations — audit exactly which customer/order mutations Shopify supports and which tResolv can safely implement: cancellation, refund, address/shipping changes, customer name changes, customer email changes, quantity/line-item edits, other order edits. Table: Capability | Shopify API | Current integration | Required scope | Can safely automate? | Conditions. Verify against actual Shopify API used by this repo and current Shopify docs — do not assume support, do not invent operations.

8. Address/name/email changes deep dive — what Shopify object owns the data, can it change after order creation/after fulfillment, does changing customer profile data differ from changing order shipping address, could it accidentally affect future orders, what verification is required, what happens when already shipped. Recommend separate actions, never one dangerous "edit order" tool.

9. Product recommendations audit — verify Shopify catalog access, variants, price, inventory, images, URLs, metadata, semantic/product similarity, complementary/related products, out-of-stock filtering, price/variant accuracy. Test actual recommendation paths, not just "a search endpoint exists." Identify what's missing for genuinely useful recommendations. Do not implement yet.

10. "Human team training" product concept — how tResolv can learn from human decisions (approvals, rejections, edits, escalations, policy evidence, Shopify outcomes, CSAT, customer feedback) without pretending literal model retraining happens per ticket. Determine which signals should affect readiness score, routing, policy configuration, response quality, future automation eligibility. Prefer deterministic evaluation/configuration over fine-tuning.

11. Customer experience during Copilot — if a customer asks "Can you cancel my order?" and Autopilot isn't enabled, Luna must not pretend the action happened. Design messaging for Copilot during business hours, Copilot outside business hours, Autopilot, and Blocked/high-risk requests. Customer should never be left thinking "someone will reply soon" when the merchant may not respond until tomorrow.

12. Product differentiation — why would a Shopify merchant choose tResolv over Shopify + existing helpdesk? Identify the strongest realistic differentiators among: category-level autonomy, merchant-specific learning, policy-grounded actions, real Shopify execution, human approval during training, measurable readiness, controlled graduation to Autopilot, rollback/kill switch, transparent action history, customer feedback loop. Recommend the 3 strongest.

13. Output — Return: (1) what Wilmo's model actually does, (2) what to copy conceptually, (3) what NOT to copy, (4) proposed tResolv automation modes, (5) proposed training→Copilot→Readiness→Autopilot journey, (6) recommended default modes by ticket category, (7) Autopilot guardrails, (8) kill-switch design, (9) Shopify mutation capability matrix, (10) product recommendation audit, (11) customer messaging strategy, (12) strongest tResolv differentiators, (13) recommended implementation phases.

No speculative code. No migrations. No new dependencies. No architecture rewrite. This is a planning task only. STOP after the research and recommendations.

## Response snapshot

Delivered `specs/007-autopilot-automation/research.md`, a 13-section planning document, built from four parallel research streams:

1. **Wilmo's public model** (web-search-mediated — direct page fetch to wilmo.ai was blocked by environment egress policy, flagged explicitly in the doc). Found: three tiers Autopilot / Copilot / **"Human Review"** (not "Blocked" as the brief assumed); every category starts in Copilot; promotion is merchant-initiated and gated on that merchant's own historical performance data, no global toggle; per-category human-edit-rate is their key visible Copilot metric; three Danish case studies (78-85% Autopilot in 26-36 days, DKK savings); no CSAT claim or kill-switch mechanism found publicly (flagged as unconfirmed, not absent).

2. **Shopify mutations + action lifecycle** (full codebase audit with file:line citations). Key finding: the LLM never has write-capable tools and never executes a mutation directly — every action is staged as `pending` and requires a separate authenticated approval call, with a regex-based backstop (`_enforce_no_unconfirmed_action_success`) forcing escalation if the model falsely claims completion. But found **three parallel, inconsistently-guarded approval surfaces** (`actions`, legacy `pending_actions`, `brand_actions`) — most notably `/api/brand-actions/approve/{id}` has **no authentication/ownership check at all** and is live in production. `auto_approve_threshold` exists as a brand column but is never enforced in the live path — confirming there is currently zero functioning Autopilot execution capability; Copilot is the only mode that works today.

3. **Product recommendations** — real, live-Shopify-grounded (fresh REST fetch per call, never stale-cache), rule-based scoring (not semantic), genuinely wired into conversation via keyword triggers, well-tested with an "honest degradation" contract (never fabricates complementary/pairing data, never fakes availability). Two real conversational gaps found: no cross-turn pronoun resolution ("like this"/"that"), no dedicated variant/color-swap query path.

4. **Analytics/readiness/policy data** — `autopilot_readiness` endpoint exists but is hardcoded to `cancel_order` only, all-time (unwindowed), and has a correctness bug: `status='failed'` (real Shopify execution failures) is excluded from the approval-rate denominator entirely. No per-category CSAT is computed despite the join path existing in the schema. No "AI proposed vs. human edited" tracking exists for any action type. Merchant policy engine (`return_policy_days`, exclusion lists, final-sale tags) is real, structured, and deterministically enforced with citable reason strings — good readiness/evidence material already in place.

The document covers all 13 requested sections: Wilmo's actual model with confidence caveats; what to copy (category-level control, merchant-gated data-driven promotion, edit-rate visibility) vs. not copy (unverifiable percentage marketing, their rollout pace, a global toggle, dropping CSAT); three-mode design (Blocked/Copilot/Autopilot) mapped to real code state; the 5-stage journey mapped field-by-field onto existing vs. missing schema/tracking, including a generalized readiness formula; category defaults constrained by actual implementation status (address changes split pre/post-fulfillment; line-item edits and customer-profile changes marked Blocked/not-yet-built rather than merely risky); a guardrail table mapping every requested guardrail to real code or an explicit gap, with the two pre-existing security findings (unauthenticated brand-actions route, default OAuth scope missing `write_orders`) called out as blockers to close before Autopilot ships; kill-switch design (global + category-level, reusing existing audit-log patterns); a full Shopify mutation capability matrix with a dedicated address/name/email deep-dive explaining why order-scoped and customer-profile-scoped changes must be separate tools; the product-recommendation audit findings; customer messaging rules for all four states (grounded to real configured SLAs, never fabricated timing); three recommended differentiators tied to genuinely-existing infrastructure (auditable action history, honest-execution guarantee, data-backed category graduation); and a four-phase rollout (Phase 0 prerequisites/security closes, Phase 1 readiness visibility, Phase 2 first real Autopilot on lowest-risk categories, Phase 3 expansion, Phase 4 explicitly deferred work).

No code, migrations, or dependencies were touched, per the task's constraints.

## Outcome

- ✅ Impact: Produced a complete, code-grounded architecture/planning document for tResolv's path from human-approval-only (Copilot) to a category-level Autopilot model, with every current-state claim cited to real file:line locations and two previously-unknown security gaps surfaced (unauthenticated brand-actions approval route; default OAuth scope omitting write_orders).
- 🧪 Tests: none — planning-only task, no code changes made.
- 📁 Files: created `specs/007-autopilot-automation/research.md`.
- 🔁 Next prompts: if the team wants to proceed, a natural next step is `/sp.specify` for the Phase-1/Phase-2 scope (readiness generalization + first real Autopilot category) once the Phase-0 prerequisites (auth gap, OAuth scope check, denominator bug, edit-tracking) are triaged and prioritized by the team.
- 🧠 Reflection: Parallelizing the four research streams (Wilmo web research + three codebase audits) via background subagents kept this tractable — the codebase audits alone traced ~15k lines across the action lifecycle, Shopify client, recommendation engine, and analytics endpoint with precise citations, which would have been hard to do reliably in a single linear pass without losing context budget.

## Evaluation notes (flywheel)

- Failure modes observed: direct WebFetch to wilmo.ai was blocked by environment egress policy (403/EGRESS_BLOCKED) — the Wilmo research subagent correctly fell back to web search and explicitly flagged reduced-confidence sourcing throughout rather than presenting search-engine summaries as verified quotes.
- Graders run and results (PASS/FAIL): none formal — self-checked that every tResolv factual claim in the final document carries a file:line citation traceable to the four subagent reports, and that no numeric readiness/performance figure was invented anywhere (all such figures are marked as "not yet computed" or left as formulas rather than example numbers).
- Prompt variant (if applicable): n/a
- Next experiment (smallest change to try): if wilmo.ai access is needed with higher confidence in a future session, try fetching via a different method/proxy path before falling back to search-only research.
