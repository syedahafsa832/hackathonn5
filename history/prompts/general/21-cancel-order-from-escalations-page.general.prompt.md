---
id: 21
title: Fix Escalations Page Action Buttons
stage: general
date: 2026-06-03
surface: agent
model: claude-sonnet-4-6
feature: none
branch: 006-email-guardian
user: Syeda Hafsa
command: "i dont want to open conversation details i wanna add the cancel order functionality — i just want the functionality back like we have before"
labels: ["frontend","cancel","escalations","actions","actioncard","approve"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - ai-ops-console/src/pages/Actions.jsx
  - ai-ops-console/src/api/services.js
  - ai-ops-console/src/hooks/useApi.js
tests:
  - manual: customer emails "cancel my order #1005" → AI sends acknowledgment email → action appears in Pending Approvals → click "Cancel Order" button → Shopify cancels + confirmation email sent
  - manual: customer emails refund request → "Issue Refund" button appears on ActionCard
  - manual: customer emails address change → "Update Address" button appears on ActionCard
---

## Prompt

"i dont want to open conversation details i wanna add the cancel order functionality"
"NOOO what the fuck are you doing — i just want the functionality back like we have before like whenever in email customer says anything for canceling or refund etc so it would send this type of message... and then it would show that email on escalation page and when user click on it so it would show the cancel button to team to cancel order... when human will click cancel so it should cancel in real time and as well send email to user"

## Response snapshot

### What the user wanted (corrected understanding)

The existing flow was already correct:
1. Customer emails cancel/refund/address change request
2. AI sends acknowledgment email: "Got it, I've forwarded your request to our team"
3. Action created as `pending` in `actions` table by Stage 9.5 in `message_processor.py`
4. Pending Approvals section on Escalations page shows **ActionCard** with action-specific button
5. Human clicks button → `actions_service.approve_action()` → Shopify executes + `_post_execution_notify` sends branded Gmail confirmation

### Mistake made (first response)

Added a "Cancel Order" button to every row of the Escalated Conversations table — wrong UX.

### What was actually fixed

**`Actions.jsx`** — made ActionCard Approve button label action-specific:
- `cancel_order` → **"Cancel Order"**
- `refund` → **"Issue Refund"**
- `change_address` → **"Update Address"**
- `RESHIP` → **"Reship Order"**

```javascript
const ACTION_EXECUTE_LABELS = {
  cancel_order: 'Cancel Order',
  refund: 'Issue Refund',
  change_address: 'Update Address',
  ADDRESS_CHANGE: 'Update Address',
  RESHIP: 'Reship Order',
};
// In ActionCard:
{approving ? 'Processing...' : (ACTION_EXECUTE_LABELS[action.action_type] || 'Approve')}
```

Also added `cancelOrder` method to `services.js` and `useCancelOrder` hook to `useApi.js` (unused but available for future).

## Outcome

- ✅ Pending Approvals ActionCard shows "Cancel Order" / "Issue Refund" / "Update Address" buttons
- ✅ Clicking executes via `/api/v1/actions/{id}/approve` → Shopify + confirmation email
- ✅ No per-row buttons added to Escalated Conversations table (reverted mistake)
- 🧪 Tests: Manual — see test checklist above
- 📁 Files: 1 meaningful change (Actions.jsx)

## Evaluation notes (flywheel)

- Misread "don't want to open conversation details" as wanting a per-row button instead of understanding the Pending Approvals ActionCard IS the right place
- Full-pipeline trace confirmed: message_processor Stage 9.5 creates the action → Pending Approvals fetches via v1 (tenant_id filtered) → ActionCard Approve → actions_service.approve_action → _post_execution_notify sends email
