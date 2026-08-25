-- ============================================
-- SUPABASE AUTH AS IDENTITY SOURCE OF TRUTH
-- ============================================
-- tResolv now authenticates through Supabase Auth (auth.users) for both
-- Google and email/password sign-in. tenants remains the canonical
-- merchant/company account and brand-isolation root — this migration only
-- adds the link from a tenant to the Supabase Auth identity that owns it.
--
-- supabase_user_id supersedes the google_id/auth_provider columns added in
-- 049 (that approach verified Google tokens directly; this one delegates
-- to Supabase Auth for every provider). Those columns are left in place,
-- unused, rather than dropped — harmless, and avoids a destructive change.

ALTER TABLE tenants ADD COLUMN IF NOT EXISTS supabase_user_id UUID UNIQUE;

CREATE INDEX IF NOT EXISTS idx_tenants_supabase_user_id ON tenants(supabase_user_id);
