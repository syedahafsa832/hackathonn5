---
id: 46
title: Google Gmail OAuth Permissions Audit
stage: general
date: 2026-08-09
surface: agent
model: Gemini 3.5 Flash
feature: none
branch: main
user: Zohaib
command: none
labels: ["security", "audit", "gmail-oauth"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/src/services/brand_gmail_service.py (inspected)
  - production/channels/gmail_handler.py (inspected)
  - backend/src/channels/email_poller.py (inspected)
  - backend/src/workers/message_processor.py (inspected)
  - backend/src/api/routes/brand_gmail.py (inspected)
  - backend/src/api/routes/auth.py (inspected)
  - generate_gmail_token.py (inspected)
tests:
---

## Prompt

I need you to perform a COMPLETE forensic audit of this repository for Google Gmail OAuth permissions and tell me exactly what Google OAuth scopes this application actually needs.

IMPORTANT:
DO NOT modify ANY files.
DO NOT change code.
DO NOT change dependencies.
DO NOT change environment variables.
DO NOT change Google Cloud settings.
DO NOT revoke or regenerate OAuth credentials.
This is an investigation only.

CONTEXT:

My Google Cloud OAuth project is:

Project ID: n8nemail-470306
Project number: 909957709543

Google currently has this restricted scope approved/requested:

https://www.googleapis.com/auth/gmail.modify

Google has now told me that because I request restricted Gmail scopes, I must complete an ADA-CASA AL1 security assessment by November 5, 2026.

Before I spend money on CASA, I want to determine whether gmail.modify is genuinely necessary or whether the application is requesting broader Gmail permissions than it actually needs.

The application is tResolv, an AI customer-support employee for Shopify brands.

Its Gmail-related functionality may include things such as:
- receiving customer support emails
- reading incoming emails
- understanding email threads
- searching for relevant messages
- generating AI replies
- creating drafts
- sending replies
- tracking conversations
- modifying labels/read state
- processing emails automatically
- syncing Gmail conversations into the application

Do NOT assume that all of these are actually implemented.
Determine what the code REALLY does.

==================================================
PART 1 — FIND THE ENTIRE GOOGLE/GMAIL AUTH FLOW
==================================================

Search the ENTIRE repository.

Find:

- OAuth configuration
- Google OAuth authorization URL generation
- callback handlers
- token exchange
- refresh-token handling
- Gmail API client initialization
- Gmail credentials
- scope definitions
- environment variables related to Google/Gmail OAuth
- frontend OAuth initiation
- backend OAuth callbacks
- n8n integration/configuration if present
- any separate Gmail worker/service
- any Gmail polling system
- webhook/watch configuration

Search for all likely forms, including but not limited to:

gmail.modify
gmail.readonly
gmail.send
gmail.compose
gmail.metadata
gmail.labels
gmail.settings.basic
gmail.settings.sharing
gmail
googleapis
google.auth
GoogleAuth
OAuth2
OAuth2Client
generateAuthUrl
access_type
scope
scopes
users.messages
users.threads
users.drafts
users.labels
history.list
watch

Also inspect configuration files such as:

.env
.env.example
.env.local
config files
Docker files
deployment configuration
n8n workflows
JSON workflow exports
YAML files
TypeScript
JavaScript
Python
FastAPI routes
workers
services
utilities
scripts

Do NOT expose secret values in your report.
If you encounter API keys, client secrets, refresh tokens, passwords, or other credentials, only report the variable/file name, NEVER the secret value.

==================================================
PART 2 — FIND EVERY GMAIL API OPERATION
==================================================

Identify every actual Gmail API operation used by the application.

Search for things such as:

messages.list
messages.get
messages.modify
messages.batchModify
messages.send
messages.delete
messages.trash
messages.untrash

threads.list
threads.get
threads.modify
threads.delete

drafts.list
drafts.get
drafts.create
drafts.update
drafts.delete
drafts.send

labels.list
labels.get
labels.create
labels.update
labels.delete

history.list

users.getProfile

users.watch

Any other Gmail API method.

Do not only search for exact strings.
Follow wrappers, helper functions, service classes, SDK abstractions, and imported functions to determine what API calls are actually made.

==================================================
PART 3 — TRACE THE REAL DATA FLOW
==================================================

For every Gmail operation, determine:

1. What triggers it?
2. Which user/account does it operate on?
3. What Gmail data does it access?
4. Is it read-only?
5. Does it modify Gmail?
6. Does it send email?
7. Does it create/update drafts?
8. Does it modify labels?
9. Does it mark messages read/unread?
10. Does it archive messages?
11. Does it delete/trash anything?
12. Does it access entire message bodies?
13. Does it access attachments?
14. Does it access metadata only?
15. Is the operation automatic/background or user-triggered?

Trace the call chain far enough to understand the actual behavior.

==================================================
PART 4 — MAP EVERY OPERATION TO GOOGLE SCOPES
==================================================

For EVERY Gmail API operation found, determine the minimum Google OAuth scope required.

Create a table:

| Actual operation | File | Function | Gmail API method | Data accessed | Current scope | Minimum required scope | Restricted? | Why |
|---|---|---|---|---|---|---|---|---|

Use Google's official Gmail OAuth scope classifications where possible.

Distinguish carefully between:

- gmail.modify
- gmail.readonly
- gmail.send
- gmail.compose
- gmail.metadata
- other Gmail scopes

Do NOT assume that replacing gmail.modify with gmail.readonly automatically solves the CASA problem.

==================================================
PART 5 — DETERMINE WHETHER gmail.modify IS ACTUALLY NECESSARY
==================================================

Answer this directly:

DOES THIS APPLICATION ACTUALLY NEED:

https://www.googleapis.com/auth/gmail.modify

Explain exactly which implemented feature requires it.

If nothing actually requires message modification, say so clearly.

If only a small part requires modification, identify exactly what.

If gmail.modify is genuinely required, explain why a narrower scope cannot satisfy the implementation.

==================================================
PART 6 — FIND THE MINIMUM POSSIBLE PERMISSION SET
==================================================

Design the minimum OAuth scope set required for the CURRENT implementation.

Give me:

CURRENT SCOPES
vs.
MINIMUM SCOPES

Then classify each scope:

- non-sensitive
- sensitive
- restricted

Also explain:

1. Which scope causes CASA?
2. Whether removing gmail.modify would eliminate CASA
3. Whether another required scope would still trigger CASA
4. Whether the application could be redesigned to avoid restricted scopes
5. What functionality would need to change to achieve that
6. What functionality would break if scopes were reduced

Be brutally honest.

==================================================
PART 7 — CHECK FOR OVER-PERMISSIONING
==================================================

Look for permissions the application requests but doesn't actually use.

For example:

If the app requests gmail.modify but only reads and sends messages, identify that.

If it requests scopes that are never used, identify them.

If the OAuth configuration is broader than the actual API usage, flag it.

Create:

UNUSED SCOPES
OVERLY BROAD SCOPES
REQUIRED SCOPES
OPTIONAL SCOPES

==================================================
PART 8 — CHECK N8N SPECIFICALLY
==================================================

This project may use n8n for Gmail automation.

Search the repository for:

n8n
Gmail nodes
Gmail Trigger
Google OAuth credentials
Google OAuth2 credentials
workflow JSON
Gmail polling
Gmail webhooks
Gmail nodes that read/send/modify messages

If n8n workflows are present, inspect them and determine:

- Which Gmail operations they perform
- Which scopes they require
- Whether those operations are part of the same Google Cloud OAuth project
- Whether n8n is actually responsible for the gmail.modify requirement

Do not assume the code repository is the only source of the OAuth behavior.

==================================================
PART 9 — CHECK FOR ARCHITECTURAL ALTERNATIVES
==================================================

Without changing anything, identify whether there is a realistic architecture that could reduce restricted-scope usage.

For example:

- read-only Gmail access
- gmail.send for sending replies
- user-triggered operations instead of background modification
- using Gmail labels differently
- storing application state in our own database instead of modifying Gmail
- processing messages without changing Gmail state

Do NOT recommend an alternative unless it is technically compatible with the actual application.

For each alternative give:

CURRENT BEHAVIOR
PROPOSED CHANGE
SCOPES AFTER CHANGE
FEATURES LOST
IMPLEMENTATION COMPLEXITY
CASA IMPACT

==================================================
PART 10 — SECURITY / COMPLIANCE RISK
==================================================

Identify anything relevant to Google's OAuth verification that we should know.

Specifically:

- What Gmail user data is stored?
- Where is it stored?
- Are message bodies stored?
- Are attachments stored?
- Are tokens stored?
- Are refresh tokens stored?
- Is Gmail data sent to AI/LLM providers?
- Which parts of Gmail data are sent to AI providers?
- Is Gmail data retained?
- Is Gmail data logged?
- Are Gmail messages stored in Supabase/Postgres/Redis/etc.?
- Are there background workers processing Gmail?
- Is data encrypted at rest/in transit based on what you can verify?
- Are there multi-tenant isolation concerns?
- Are there obvious security/compliance issues Google may care about?

Do not invent security guarantees.
If you cannot verify something from the repository, explicitly say:

"NOT VERIFIED FROM REPOSITORY"

==================================================
PART 11 — GOOGLE VERIFICATION IMPACT
==================================================

Based on the actual implementation, tell me what Google OAuth verification requirements are likely relevant.

Separate:

REQUIRED
LIKELY REQUIRED
NOT APPLICABLE
UNKNOWN / NEEDS CONFIRMATION

Pay particular attention to:

- restricted Gmail scopes
- CASA
- privacy policy
- demo video
- use-case justification
- minimum-scope justification
- data handling
- AI/ML data usage
- data retention
- OAuth consent screen
- production vs testing environment

==================================================
PART 12 — FINAL RECOMMENDATION
==================================================

Give me a brutally honest final recommendation.

Use this exact structure:

### VERDICT

Do we genuinely need gmail.modify?

YES / NO / PARTIALLY / UNKNOWN

### CURRENT PERMISSIONS

List them.

### MINIMUM REQUIRED PERMISSIONS

List them.

### CASA

Do we still need CASA after minimizing scopes?

YES / NO / UNKNOWN

Explain why.

### BEST ARCHITECTURE

Describe the cleanest architecture that provides the required tResolv functionality with the minimum Google permissions.

### WHAT I SHOULD CHANGE

List the exact code/config changes that would eventually be needed.

DO NOT MAKE THESE CHANGES.
Only describe them.

### WHAT I SHOULD NOT CHANGE

List anything that is already correct and should remain untouched.

### GOOGLE VERIFICATION RISKS

List the biggest risks that could block verification.

### NEXT 5 ACTIONS

Give me the five highest-priority actions I should take next, in order.

==================================================
FINAL RULES
==================================================

This is a forensic investigation.

DO NOT:
- modify files
- install packages
- uninstall packages
- change OAuth scopes
- change Google Cloud settings
- revoke tokens
- regenerate credentials
- alter n8n workflows
- commit changes
- push changes
- expose secrets

DO:
- inspect everything
- trace actual behavior
- distinguish facts from assumptions
- cite exact file paths
- cite function names
- cite relevant line numbers when available
- explain uncertainty
- identify the minimum required permissions
- identify whether CASA is genuinely unavoidable

At the end, give me a concise executive summary I can paste to another developer/security consultant.

DO NOT START FIXING ANYTHING.

AUDIT ONLY.

## Response snapshot

- Performed a complete forensic audit of the codebase, focusing on Gmail integrations in both the active SaaS path (`backend/src/`) and the legacy monolith path (`production/`).
- Verified all Gmail API calls: `getProfile`, `messages().list`, `messages().get`, `messages().batchModify`, and `messages().send`.
- Concluded that `gmail.modify` is only used to mark polled incoming emails as read via `batchModify` (removing the `UNREAD` label).
- Determined that downgrading to `gmail.readonly` (with `gmail.send` for replies) does NOT bypass CASA because `gmail.readonly` is also a Restricted Scope.
- Proposed an alternative architecture: Inbound Email Forwarding (webhook) + `gmail.send` OAuth scope (Sensitive, not Restricted) which completely eliminates CASA AL1 security audit requirements.
- Audited repository for `n8n` and confirmed it is completely absent.
- Wrote a detailed report to the artifacts directory and provided an executive summary.

## Outcome

- ✅ Impact: Complete mapping of Gmail API usage and a clear strategic recommendation on bypassing CASA via architectural refactoring.
- 🧪 Tests: No tests run (investigation only).
- 📁 Files: Created `C:\Users\Zohaib\.gemini\antigravity-ide\brain\d1c87e84-cf6d-4d7e-b344-1f1e420abf48\gmail_oauth_forensic_audit.md` (report artifact).
- 🔁 Next prompts: User decides whether to stick with Gmail polling + pay for CASA, or migrate to Email Forwarding + Sensitive-only scopes.
- 🧠 Reflection: For SaaS apps that only need to ingest emails, Email Forwarding (webhook) is highly recommended over API polling. It reduces API complexity, provides instant push updates, and completely bypasses restricted scope verifications (like CASA).

## Evaluation notes (flywheel)

- Failure modes observed: none
- Graders run and results (PASS/FAIL): n/a
- Prompt variant (if applicable): default
- Next experiment (smallest change to try): none
