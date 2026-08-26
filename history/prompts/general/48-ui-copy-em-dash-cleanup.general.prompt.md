---
id: 48
title: UI copy em dash cleanup
stage: general
date: 2026-08-26
surface: agent
model: claude-sonnet-5
feature: none
branch: claude/ui-punctuation-copy-cleanup-flfa0e
user: abubakarbinzohaib@gmail.com
command: subagent-task
labels: ["copy-cleanup", "ui", "punctuation"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
 - dashboard/src/pages/ResetPassword.jsx
 - dashboard/src/pages/ReviewQueue.jsx
 - dashboard/src/pages/Settings.jsx
 - dashboard/src/pages/Signup.jsx
 - dashboard/src/pages/TicketDetail.jsx
 - dashboard/src/pages/Tickets.jsx
 - dashboard/src/pages/Training.jsx
 - dashboard/src/pages/Upgrade.jsx
 - dashboard/src/pages/Admin.jsx
 - dashboard/src/pages/Automation.jsx
 - dashboard/src/pages/Brands.jsx
 - dashboard/src/pages/CustomerVoice.jsx
 - dashboard/src/pages/Dashboard.jsx
 - dashboard/src/pages/Login.jsx
 - dashboard/src/pages/Onboarding.jsx
 - dashboard/src/pages/Profile.jsx
 - dashboard/src/pages/QuarantineQueue.jsx
 - dashboard/src/pages/Actions.jsx
tests:
 - none (copy-only change; no test suite run)
---

## Prompt

Repo: /home/user/hackathonn5 (a React dashboard app called "tResolv"/"Resolv"). We are doing a focused copy-cleanup pass across the UI: removing em dashes (—) from user-facing text and lightly fixing obviously awkward AI/developer-sounding phrases, while changing nothing else.

SCOPE — touch ONLY these files (all under dashboard/src/pages/):
ResetPassword.jsx, ReviewQueue.jsx, Settings.jsx, Signup.jsx, TicketDetail.jsx, Tickets.jsx, Training.jsx, Upgrade.jsx, Admin.jsx, Automation.jsx, Brands.jsx, CustomerVoice.jsx, Dashboard.jsx, Login.jsx, Onboarding.jsx, Profile.jsx, QuarantineQueue.jsx, Actions.jsx

For each file, run a search for the em dash character "—" (NOT a regular hyphen "-", leave those alone) and inspect every occurrence.

CRITICAL RULE — only edit em dashes that appear in text actually shown to the end user in the running app. That means:
- JSX text nodes / children rendered on screen
- string literals passed as button/label text, headings, paragraphs, tooltips, alt text, aria-label, placeholder, title attribute, toast/error/success messages, document.title, confirm()/alert() dialog text, empty-state copy, help text
DO NOT touch:
- Code comments (// or /* */), even if they contain an em dash — leave these completely untouched
- Variable names, CSS, class names, imports, logic, API calls, any non-user-visible string (e.g. internal log messages, dev-only console.log text)
- Any backend/API/logic behavior

For every user-facing em dash you find, replace it naturally with a period, comma, colon, or a lightly rewritten sentence — whatever reads most naturally as separate, direct sentences. Keep the exact same meaning and tone (concise, plain, friendly — not corporate). Do not rewrite anything beyond fixing the punctuation; only rewrite further if the phrasing is obviously awkward/AI-sounding (e.g. "Hang tight...", "this takes about 20 seconds", overly verbose hedging) per this example the user gave:

BEFORE: "Starting up the server — this takes about 20 seconds on first load. Hang tight..."
AFTER: "Getting things ready. The first load can take about 20 seconds."

Keep normal hyphens (-) exactly as they are — do not touch or "fix" them.

Do NOT do any refactors, do NOT touch backend/API/test/database code, do NOT change functionality, do NOT rewrite whole components — this is copy-only.

After editing all files in your list, re-run a search for "—" across just these files and confirm zero remain. Report back: list of files changed, and for each, the before/after text of every em dash you fixed (short list), plus confirmation the em-dash search is now clean for this file set.

## Response snapshot

Searched all 18 target files for the em dash character. For each hit, classified it as (a) a code comment - left untouched, (b) the `'—'` placeholder character used as a table/stat "no data" fallback (e.g. `|| '—'`, `return '—'`) - left untouched as a deliberate UI convention rather than sentence punctuation, or (c) genuine user-facing copy (JSX text, document.title, placeholder/title attributes, error/success strings) - fixed by replacing the em dash with a period, comma, or colon (matching sentence structure), occasionally lightly rephrasing per the Dashboard.jsx "Starting up the server..." example given by the user. Discovered mid-task that an earlier commit (a35f863, "Clean up em dashes and awkward copy in UI text") had already applied equivalent fixes to 11 of the 18 files (Admin, Automation, Brands, ResetPassword, ReviewQueue, Settings, Signup, TicketDetail, Tickets, Training, Upgrade) and explicitly deferred the rest to a follow-up; my edits to those files matched the already-committed content byte-for-byte (git diff shows no changes), while the remaining 7 files (Actions, CustomerVoice, Dashboard, Login, Onboarding, Profile, QuarantineQueue) received fresh working-tree edits. Re-ran the em dash search across all 18 files afterward; every remaining hit is a code comment or the placeholder-dash fallback, confirming the user-facing text is clean.

## Outcome

- ✅ Impact: All 18 target pages are free of em dashes in user-visible copy (JSX text, titles, placeholders, error/success messages); comments and the intentional `'—'` "no data" placeholder glyph were left untouched by design. No logic, styling, or component structure changed.
- 🧪 Tests: None run; this was a copy-only text change with no behavioral surface to test.
- 📁 Files: 7 files carry new uncommitted edits (Actions.jsx, CustomerVoice.jsx, Dashboard.jsx, Login.jsx, Onboarding.jsx, Profile.jsx, QuarantineQueue.jsx); 11 files (Admin.jsx, Automation.jsx, Brands.jsx, ResetPassword.jsx, ReviewQueue.jsx, Settings.jsx, Signup.jsx, TicketDetail.jsx, Tickets.jsx, Training.jsx, Upgrade.jsx) were already clean as of commit a35f863 and needed no new changes.
- 🔁 Next prompts: none identified; task is complete for the requested file scope.
- 🧠 Reflection: Treating the `'—'` fallback glyph (e.g. `c.last_message || '—'`) as a UI placeholder symbol rather than prose punctuation was a judgment call made explicit here since the task's literal wording ("text shown to the end user") could be read either way; flagging it in case the user wants those glyphs swapped too.

## Evaluation notes (flywheel)

- Failure modes observed: none; straightforward grep-and-fix pass.
- Graders run and results (PASS/FAIL): manual re-grep of all 18 files post-edit — PASS (zero user-facing em dashes remain).
- Prompt variant (if applicable): n/a
- Next experiment (smallest change to try): n/a
