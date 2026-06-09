---
id: 40
title: Tracking link direct in chat fix
stage: general
date: 2026-06-08
surface: agent
model: claude-sonnet-4-6
feature: chat-widget
branch: updated_whole_damn_product
user: Syeda Hafsa
command: Show tracking link directly in chat instead of "check your email"
labels: ["agent_prompt", "tracking", "chat_widget", "order_status"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/src/agent/customer_success_agent.py
tests:
  - none
---

## Prompt

and as you said before that we have our tracking link etc is working soo why should the customer go and see that we should fetch it for himmm
1001
22:03
L
Got it! Your order with the Essential Hoodie V7 in XL has shipped. Check your email for the tracking details, it should be there.

## Response snapshot

**Root cause:** The agent prompt had a FORMATTING RULES example that explicitly said:
```
- RIGHT: "Your order has shipped. Check your email for the tracking number."
```
The LLM was following this example literally even though `_build_order_context` already puts the tracking URL in the context and says "Tell them exactly: 'track it here: URL'". The RIGHT/WRONG example took precedence.

**Fixes in `_construct_v3_prompt`:**
1. Removed the bad "RIGHT" example ("Check your email for the tracking number.")
2. Added new **TRACKING RULES** block (placed before FORMATTING RULES, higher priority):
   - If tracking URL present: paste it directly, never say "check your email"
   - Wrong/Right examples showing exact expected format
   - Fallback for tracking number without URL
   - Fallback for fulfilled with no tracking yet
3. Added to COMMON SENSE: "If ORDER DATA says 'fulfilled' AND tracking URL is present — share that URL directly. Never say 'check your email'."

## Outcome

- ✅ Agent will now reply: "Your order has shipped! Here's your tracking link: https://..."
- ✅ Removed conflicting example that was overriding order context instructions
- 📁 Files: 1 file modified

## Evaluation notes (flywheel)

- Failure modes observed: prompt example ("RIGHT: Check your email") overrode explicit tool context instruction
- Graders run and results (PASS/FAIL): none
- Prompt variant (if applicable): none
- Next experiment (smallest change to try): test with order #1001 again to confirm tracking URL appears inline
