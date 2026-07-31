-- C2 fix: registration-time brand auto-creation (auth_service.py register())
-- only sets name/is_active/tenant_id/gmail_connected. brands' ORIGINAL schema
-- (migration 002) has shopify_shop_name/shopify_access_token/support_email as
-- NOT NULL (shopify_shop_name also UNIQUE), which would reject that insert
-- outright. migration 009 already relaxed these — but as an ad-hoc "run this
-- in Supabase SQL Editor if you get errors" script, not a guaranteed pipeline
-- step, so its actual application to any given environment can't be assumed
-- from the repo alone. Re-stating it here, idempotently (safe to run whether
-- or not 009 ever ran), so the registration insert can no longer be silently
-- rejected by a constraint the code was never actually written to satisfy.

-- Also covers organization_id/slug: an earlier schema variant (migration 006)
-- defined brands around an organization_id-based model with these two as
-- NOT NULL. The app has since moved to tenant_id (migration 010) for
-- scoping; organization_id/slug are vestigial and unused by current code
-- (confirmed: zero references in src/), so the registration insert correctly
-- never supplies them — which only works if they're nullable.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'brands' AND column_name = 'organization_id') THEN
        ALTER TABLE brands ALTER COLUMN organization_id DROP NOT NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'brands' AND column_name = 'slug') THEN
        ALTER TABLE brands ALTER COLUMN slug DROP NOT NULL;
    END IF;
END $$;

ALTER TABLE brands ALTER COLUMN shopify_shop_name    DROP NOT NULL;
ALTER TABLE brands ALTER COLUMN shopify_access_token DROP NOT NULL;
ALTER TABLE brands ALTER COLUMN support_email        DROP NOT NULL;
