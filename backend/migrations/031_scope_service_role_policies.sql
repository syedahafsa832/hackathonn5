-- Security Advisor: "RLS Policy Always True" on pending_actions and
-- product_variants. Confirmed before changing anything: the "Service role
-- full access" policy on both tables actually applied to roles={public}
-- (everyone), not service_role as the name claims — qual=true, with_check=
-- true, no real restriction. Table-level GRANTs already block anon/
-- authenticated in practice (028_lockdown_anon_authenticated_access.sql),
-- but the policy itself provided zero restriction and would immediately
-- re-expose both tables if grants were ever restored independently.

ALTER POLICY "Service role full access" ON pending_actions TO service_role;
ALTER POLICY "Service role full access" ON product_variants TO service_role;
