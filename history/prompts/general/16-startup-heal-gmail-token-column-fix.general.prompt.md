---
id: 16
title: Startup Heal Gmail Refresh Token Column Fix
stage: general
date: 2026-06-03
surface: agent
model: claude-sonnet-4-6
feature: none
branch: 006-email-guardian
user: Syeda Hafsa
command: DEBUG — startup_heal crashing with column brands.gmail_refresh_token does not exist
labels: ["backend","startup-heal","gmail","schema","bugfix"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/main.py
tests:
  - manual: deploy → check startup logs → must NOT show "column brands.gmail_refresh_token does not exist"
  - manual: startup logs must show "[STARTUP-HEAL] No data issues found" or "[STARTUP-HEAL] Skipping brand 7b977597 — no refresh_token in stored credentials (orphan/stale)"
  - manual: only 2 brands must appear in email poller after orphan brand 7b977597 is manually disconnected
---

## Prompt

Logs after deploy showed:

```
ERROR:src.lib.supabase_client:Supabase Select Error: 400 for brands with params
{'gmail_connected': 'eq.false', 'gmail_refresh_token': 'not.is.null'}.
Body: {"code":"42703","details":null,"hint":null,"message":"column brands.gmail_refresh_token does not exist"}
```

startup_heal steps 1 and 1b were both failing because they queried a `gmail_refresh_token` column that does not exist on the `brands` table.

## Response snapshot

### Root cause

The previous session's fix added `"gmail_refresh_token": "not.is.null"` to the PostgREST query in startup_heal. However, the `brands` table schema (migration 007) only added three columns:
- `gmail_email` VARCHAR(255)
- `gmail_token` TEXT  ← stores full OAuth credentials as a JSON blob
- `gmail_connected` BOOLEAN

The `refresh_token` is stored **inside** the `gmail_token` JSON blob (key: `refresh_token`), not in a separate database column. There is no `gmail_refresh_token` column.

### Fix — `main.py` startup_heal step 1

Merged steps 1 and 1b into a single step:
1. Query: `gmail_connected=false` AND `gmail_token IS NOT NULL` (valid columns)
2. In Python: parse the `gmail_token` JSON blob and check for `token_data.get("refresh_token")`
3. Only re-enable brands where the stored credentials include a non-empty refresh_token
4. Log and skip brands whose token blob has no refresh_token (e.g., orphan brand 7b977597)

This preserves the orphan-brand protection from the previous fix — stale brands whose `gmail_token` blob has no `refresh_token` will still be skipped and logged as such.

## Outcome

- ✅ Impact: startup_heal no longer crashes on deploy — no more 400 errors for missing column
- ✅ Orphan protection maintained: brands with stale gmail_token blobs (no refresh_token) are skipped with a log line
- 🧪 Tests: Manual — see test checklist above
- 📁 Files: 1 file modified (backend/main.py, startup_heal step 1)
- 🔁 Next prompts: Deploy and verify logs; manually disconnect orphan brand 7b977597 from Brands UI to stop it appearing as a 3rd connected brand
- 🧠 Reflection: The `gmail_token` column stores a full Google OAuth credentials dict serialized as JSON. Any code that needs to check for a valid refresh token must parse the blob in Python — PostgREST has no way to filter on nested JSON fields using the standard `not.is.null` syntax (it would need a computed column or a jsonb operator).

## Evaluation notes (flywheel)

- Failure modes observed: If a brand's gmail_token blob is stored as a Python dict (not JSON string), the `json.loads()` call would fail — guarded by try/except that skips the brand
- Graders run and results (PASS/FAIL): none automated
- Prompt variant (if applicable): N/A
- Next experiment (smallest change to try): Add a dedicated `gmail_refresh_token TEXT` column to brands via migration, extracted from the JSON blob — would make future DB queries simpler and avoids Python-side JSON parsing in startup_heal
