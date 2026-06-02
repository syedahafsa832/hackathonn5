# Data Model: AI Operations Cockpit Frontend

## Core Entities

### UnifiedEvent

The single source of truth for all frontend data.

| Field | Type | Description |
|-------|------|-------------|
| id | string (UUID) | Unique event identifier |
| type | enum | Event type: email_received, ai_decision, action_created, action_approved, execution_completed |
| timestamp | ISO8601 datetime | When event occurred |
| customer.email | string | Customer email address |
| customer.name | string (optional) | Customer display name |
| metadata.channel | string | Source: email, web_form, whatsapp |
| metadata.subject | string (optional) | Email/form subject |
| metadata.message_preview | string (optional) | First 100 chars of message |
| metadata.intent | string (optional) | Detected intent: refund, cancel, question, etc. |
| metadata.sentiment | string (optional) | positive, neutral, negative |
| metadata.confidence | number (0-1) | AI confidence score |
| metadata.decision | string (optional) | auto_reply, action_proposal, human_review |
| metadata.action_type | string (optional) | refund, cancel_order, update_address |
| metadata.order_id | string (optional) | Shopify order ID |
| metadata.risk_level | string (optional) | low, medium, high |
| metadata.execution_status | string (optional) | pending, success, failed |
| metadata.error_details | string (optional) | Error message if failed |
| lifecycle.parent_event_id | string (optional) | Link to parent event |
| lifecycle.child_events | string[] | Array of child event IDs |

### EventFilter

Query parameters for fetching events.

| Field | Type | Description |
|-------|------|-------------|
| type | string (optional) | Filter by event type |
| status | string (optional) | Filter by status |
| since | ISO8601 datetime | Events after this time |
| limit | number | Max events to return |

---

## State Transitions

### Customer Event Lifecycle

```
email_received → ai_decision → (action_created | execution_completed)
                                    ↓
                            action_approved
                                    ↓
                            execution_completed
```

### Action Status Flow

```
pending (action_created) → approved (action_approved) → success (execution_completed)
                                          → rejected
                                          → failed (execution_completed with error)
```

---

## Validation Rules

- Event type must be one of: email_received, ai_decision, action_created, action_approved, execution_completed
- Confidence must be between 0 and 1
- Timestamp must be valid ISO8601 format
- Risk level must be: low, medium, or high
- Execution status must be: pending, success, or failed
- Required fields per type:
  - email_received: customer.email, metadata.channel
  - ai_decision: metadata.intent, metadata.confidence, metadata.decision
  - action_created: metadata.action_type, metadata.order_id, metadata.risk_level
  - action_approved: must have parent_event_id
  - execution_completed: metadata.execution_status