-- Item 12 of the bug-fix/polish pass: paid plans (starter/growth/enterprise)
-- expire 30 days after manual activation. plan_activated_at records when the
-- admin last activated a paid plan for this tenant — plan_service._resolve_plan()
-- lazily reverts the tenant to the free plan once 30 days have passed (same
-- lazy-expiry idiom already used for trial_end_at). Deliberately NOT cleared
-- on expiry so the frontend can tell "expired paid plan" apart from "never
-- paid" and show a resubscribe prompt instead of the generic upgrade nag.

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS plan_activated_at TIMESTAMPTZ;
