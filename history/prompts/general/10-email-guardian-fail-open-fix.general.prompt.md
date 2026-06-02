---
id: 10
title: Email Guardian Fail-Open Fix for Unknown Classification
stage: general
date: 2026-06-02
surface: agent
model: claude-sonnet-4-6
feature: none
branch: 006-email-guardian
user: Syeda Hafsa
command: Fix Email Guardian blocking "hello" emails classified as unknown
labels: ["backend","email-guardian","classification","fail-open","email-poller"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/src/services/email_guardian_service.py
tests:
  - manual: send a simple "hello" email to connected inbox → ticket appears in dashboard
  - manual: send a promotional email → still blocked (reason=ai_classification)
  - manual: send "i need help with my order" → classified as customer_support, allowed with auto_reply
---

## Prompt

Fix Email Guardian blocking "hello" emails classified as unknown. Logs showed:
```
[Guardian] Classifier → unknown (0.50)
[Poller] Guardian blocked: syedahafsa772@gmail.com reason=ai_classification classification=unknown
```

## Response snapshot

### Root Cause

Layer 4 in `email_guardian_service.py` `evaluate()` used a broad negative condition:
```python
if support_only_mode and classification != "customer_support":
    return GuardianResult(decision="blocked", ...)
```
This blocked **everything** that wasn't `customer_support` — including `unknown`. The dead-code block at lines 217-226 intended to quarantine unknowns was never reached because Layer 4 returned first.

### Fix Applied

**Added `BLOCKED_CLASSIFICATIONS` deny-list** (explicit set):
```python
BLOCKED_CLASSIFICATIONS = {"promotion", "newsletter", "outreach", "spam", "automation"}
```

**Changed Layer 4** from negative match to positive deny-list:
```python
if support_only_mode and classification in BLOCKED_CLASSIFICATIONS:
    return GuardianResult(decision="blocked", ...)
```

**Changed unknown handler** from quarantine to allow (fail-open):
```python
if classification == "unknown":
    return GuardianResult(
        decision="allowed",
        classification=classification,
        confidence=confidence,
        reason=None,
        quarantine_id=None,
        auto_reply_enabled=False,  # no AI reply for ambiguous mail
    )
```

`auto_reply_enabled=False` means the ticket lands in the inbox for human review but the AI won't blindly reply to something it couldn't classify.

## Outcome

- ✅ Impact: "hello" and other short/ambiguous emails now create tickets instead of being silently dropped; spam/promo/outreach still blocked
- 🧪 Tests: Manual — send "hello" email, verify ticket appears in dashboard; send promo email, verify still blocked
- 📁 Files: 1 file modified
- 🔁 Next prompts: Test action approval after action_logs timestamp fix; run migration 019 for tenant isolation backfill; add Auto-Submitted header to outgoing AI emails
- 🧠 Reflection: The original design was fail-closed by accident — the deny-list approach is the correct pattern for classifiers that return "unknown" when uncertain

## Evaluation notes (flywheel)

- Failure modes observed: if Mistral API is down, classifier returns ("unknown", 0.0) → now allowed through instead of blocked; this is the correct behavior but means spam could arrive during outages
- Graders run and results (PASS/FAIL): none automated
- Prompt variant (if applicable): N/A
- Next experiment (smallest change to try): add a confidence floor for unknown — if confidence < 0.3 AND unknown, still quarantine rather than allow
