---
id: 50
title: Careers page and application form
stage: general
date: 2026-09-21
surface: agent
model: claude-sonnet-5
feature: none
branch: main
user: syedahafsa832
command: (free-form request)
labels: [careers, nextjs, supabase, rls, marketing-site]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - D:/RESOLV/resolv-site/app/careers/page.jsx
  - D:/RESOLV/resolv-site/app/careers/apply/page.jsx
  - D:/RESOLV/resolv-site/app/careers/careers.css
  - D:/RESOLV/resolv-site/components/careers/ApplyForm.jsx
  - D:/RESOLV/resolv-site/components/careers/icons.jsx
  - D:/RESOLV/resolv-site/lib/careers/config.js
  - D:/RESOLV/resolv-site/lib/careers/submitApplication.js
  - D:/RESOLV/resolv-site/supabase/careers/001_applications.sql
  - D:/RESOLV/resolv-site/.env.example
  - D:/RESOLV/resolv-site/package.json (+ @supabase/supabase-js)
tests:
  - resolv-site production build (32/32 pages, /careers + /careers/apply generated)
  - browser: /careers, /careers/apply desktop + 375px mobile, empty-form validation
  - E2E submit -> row in careers Supabase (status=new); test row deleted afterwards
  - anon REST: select/update/delete/insert-status/insert-id denied; bad email + duplicate rejected
---

## Prompt

User asked for a new /careers page and /careers/apply custom application form for the tResolv website
(single open role: Lead Acquisition, commission-based, no experience required, Gen-Z premium dark design,
no invented commission %), backed by a SEPARATE careers Supabase project (applications table with status
new/reviewing/shortlisted/interview/selected/rejected, RLS insert-only for the public, no service-role key
in the client), leaving the selection-email automation and /team onboarding portal for a later phase.
Full brief was given verbatim in the chat (15 application questions, validation/test checklist, final summary
format). Mid-task the user pasted the careers project's URL + publishable key (Supabase Next.js quickstart).

## Response snapshot

Initially built in the dashboard (Vite) app, then found the real marketing site (D:/RESOLV/resolv-site,
Next 14 App Router), reverted the dashboard changes and ported. Applied the schema to the careers project
(byuekdiesrfawsvedqsx), verified separation from the product project (jnistnazancykmczbpeq), tested E2E and RLS.

## Outcome

- ✅ Impact: /careers and /careers/apply live on the marketing site; submissions land in the separate careers DB.
- 🧪 Tests: see front matter; all passed.
- 📁 Files: see front matter.
- 🔁 Next prompts: add real commission text; selection-email automation via existing backend; /team portal.
- 🧠 Reflection: should have inspected the repo list (launch.json) for the marketing site before building in the dashboard.

## Evaluation notes (flywheel)

- Failure modes observed: built in the wrong project first (dashboard SPA vs marketing site).
- Next experiment: check .claude/launch.json and sibling directories when "the website" is ambiguous.
