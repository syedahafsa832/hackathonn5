-- ============================================
-- TEAM MEMBERS (invite-based RBAC on top of `tenants`)
-- ============================================
-- The live tenant model is 1 tenants row == 1 Supabase Auth user
-- (tenants.supabase_user_id, see 052_supabase_auth_identity.sql). This adds
-- a second way to reach a tenant: as an invited member with a role, without
-- touching the owner path at all. The app talks to Supabase exclusively via
-- the service-role key (src/lib/supabase_client.py), which bypasses RLS, so
-- RLS here is defense-in-depth only — authorization is enforced in
-- src/api/routes/team.py, matching every other tenant-scoped table.

CREATE TABLE IF NOT EXISTS tenant_members (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    supabase_user_id UUID UNIQUE,  -- set once the invite is accepted
    email VARCHAR(255) NOT NULL,
    full_name VARCHAR(255),
    role VARCHAR(20) NOT NULL DEFAULT 'agent' CHECK (role IN ('admin', 'agent', 'read_only')),
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'active', 'revoked')),
    invited_by UUID REFERENCES tenants(id),
    invite_token VARCHAR(255) UNIQUE,
    invite_expires_at TIMESTAMP WITH TIME ZONE,
    accepted_at TIMESTAMP WITH TIME ZONE,
    revoked_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, email)
);

CREATE INDEX IF NOT EXISTS idx_tenant_members_tenant ON tenant_members(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tenant_members_supabase_user ON tenant_members(supabase_user_id);
CREATE INDEX IF NOT EXISTS idx_tenant_members_token ON tenant_members(invite_token);
CREATE INDEX IF NOT EXISTS idx_tenant_members_email ON tenant_members(email);

DROP TRIGGER IF EXISTS update_tenant_members_updated_at ON tenant_members;
CREATE TRIGGER update_tenant_members_updated_at
    BEFORE UPDATE ON tenant_members
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

ALTER TABLE tenant_members ENABLE ROW LEVEL SECURITY;
REVOKE SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON tenant_members FROM anon;
REVOKE SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON tenant_members FROM authenticated;
