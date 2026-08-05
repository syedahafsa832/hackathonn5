-- tResolv is single-store-per-account: enforce it at the database level,
-- not just via the plan-limit check in plan_service.py. Verified live
-- (2026-08) that zero existing tenants have more than one active brand,
-- so this is safe to add now without a data migration.
CREATE UNIQUE INDEX IF NOT EXISTS brands_one_active_per_tenant
  ON brands (tenant_id)
  WHERE is_active = true AND tenant_id IS NOT NULL;
