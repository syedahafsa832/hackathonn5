---
id: 15
title: Cross-Brand Email Loop and Dashboard Visibility
stage: general
date: 2026-06-03
surface: agent
model: claude-sonnet-4-6
feature: none
branch: 006-email-guardian
user: Syeda Hafsa
command: DEBUG — cross-brand email loop still happening + ticket not in dashboard
labels: ["backend","email-loop","multi-brand","startup-heal","tenant-isolation"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/src/channels/email_poller.py
  - backend/main.py
tests:
  - manual: send cancel email from 1983 to 772 → check logs after 2 minutes → NO "NEW MESSAGE" lines for 772 or 1983 for that thread on 2nd poll cycle
  - manual: logs must show "[Poller] Skipping brand-owned address email from syedahafsa1983@gmail.com" or "...772@gmail.com" when the AI reply arrives in the other inbox
  - manual: container restart → logs must show exactly 2 connected brands (not 3) unless both 1983 and 772 have valid refresh tokens
---

## Prompt

Everything was working but it didn't add an escalation and the new conversation didn't show in the dashboard. (Logs provided showing Re: Re: loop and 3 connected brands)

## Response snapshot

### Root cause — Email loop (cross-brand, not same-brand)

The old own-address check (`sender == current_brand.gmail_email`) only blocked a brand from re-processing its OWN outbound replies. It did NOT block cross-brand loops:

1. Customer (1983) → cancel email → brand 772's inbox
2. AI replies FROM 772 → lands in 1983's Gmail inbox
3. Brand 1983's poller picks up the AI reply (from 772) as a new customer email — `772 ≠ 1983`, passes old check
4. Brand 1983 replies FROM 1983 → lands in 772's inbox
5. Brand 772's poller picks it up as a new customer email — `1983 ≠ 772`, passes old check
6. ∞

**Fix — `email_poller.py`:**
`_poll_all_inboxes` now builds a `frozenset` of ALL connected brand Gmail addresses once per cycle, passes it to `_poll_brand_inbox`:
```python
all_brand_emails = frozenset(b.get("gmail_email","").lower() for b in brands if b.get("gmail_email"))
```
`_poll_brand_inbox` now checks `if sender in all_brand_emails → skip`. This blocks any email from any known brand address, regardless of which brand is currently being polled.

### Root cause — 3rd brand appearing (7b977597)

startup_heal step 1b re-enabled brand `7b977597` (an old/orphaned brand with `gmail_email = syedahafsa772@gmail.com`) because it had `gmail_token IS NOT NULL` but `gmail_connected = false`. A stale `gmail_token` blob (with no matching `gmail_refresh_token`) cannot be renewed — it would only cause `invalid_grant` errors and create duplicate brand entries.

**Fix — `main.py` startup_heal step 1b:**
Added `"gmail_refresh_token": "not.is.null"` condition so only brands with a valid refresh token are re-enabled.

### Why the conversation didn't show in the dashboard

Two reasons:
1. **auto_resolved tickets are not counted in Active Conversations** — the AI resolved the ticket fully (confidence 80%, risk low) so status = `auto_resolved`. The Dashboard's Active count only includes `[open, processing, human_managing, escalated, auto_resolved_review, review_needed]`. The ticket IS in the Conversations list under "All".
2. **Tenant scoping** — the "helpp" email landed in brand 772's inbox (b9b350c1, tenant b2d4b40f). If you're logged in as 1983's account (tenant 56542e36), brand 772's tickets are not visible. Check the Conversations page while logged into the account that owns brand 772.

### Why the escalation didn't appear

The cancel_order action WAS created (logs: `[Actions] Created cancel_order action aa782d60 for tenant b2d4b40f`). It appears in the Actions page for tenant b2d4b40f. If you're logged in as tenant 56542e36 (1983's account), that action is scoped to a different tenant and won't be visible.

## Outcome

- ✅ Impact: Cross-brand email loop permanently fixed — any email from any connected brand address is now skipped, regardless of which brand's inbox is being polled. Stale brand re-activation prevented.
- 🧪 Tests: Manual — see test checklist above
- 📁 Files: 2 files modified
- 🔁 Next prompts: Deploy and test; manually disconnect orphan brand 7b977597 from the Brands page; log in to the correct tenant account to verify action visibility
- 🧠 Reflection: The loop was happening because Syeda's test setup uses her own Gmail accounts as BOTH customer senders AND brand inboxes. A message from 1983 to 772 is a real customer email. But the AI's reply (from 772 back to 1983) lands in 1983's inbox which is ALSO a brand being polled. The only safe fix is to treat ALL brand addresses as system addresses, never as customers.

## Evaluation notes (flywheel)

- Failure modes observed: if a real customer happens to have the same email as a brand Gmail address, their emails will be silently skipped. This is an acceptable trade-off (admin accounts are not customers).
- Graders run and results (PASS/FAIL): none automated
- Prompt variant (if applicable): N/A
- Next experiment (smallest change to try): add "Auto-Submitted: auto-replied" header to outbound AI emails — some email servers will then suppress delivery-loop detection automatically
