-- ============================================
-- GOOGLE OAUTH SIGN-IN/SIGN-UP
-- ============================================
-- Allows tenants to register/login via "Sign in with Google" in addition
-- to email+password. password_hash becomes optional since Google-only
-- accounts never set one; google_id links the tenant to their Google
-- account (verified ID token 'sub' claim).

ALTER TABLE tenants ALTER COLUMN password_hash DROP NOT NULL;

ALTER TABLE tenants ADD COLUMN IF NOT EXISTS google_id VARCHAR(255) UNIQUE;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS auth_provider VARCHAR(20) NOT NULL DEFAULT 'password';

CREATE INDEX IF NOT EXISTS idx_tenants_google_id ON tenants(google_id);
