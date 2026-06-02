-- 019: Tenant isolation backfill
-- Ensures every brand has tenant_id set so _get_tenant_brand_ids() works
-- and no tenant sees another tenant's tickets.

-- ============================================
-- 1. Link brands to tenants via shopify_domain
-- (Already done by 010, repeated here as idempotent safety net)
-- ============================================
UPDATE brands b
SET tenant_id = t.id
FROM tenants t
WHERE t.shopify_domain IS NOT NULL
  AND t.shopify_domain != ''
  AND b.shopify_domain = t.shopify_domain
  AND b.tenant_id IS NULL;

-- ============================================
-- 2. Link brands to tenants via gmail_email → tenant email
-- Covers brands created during Gmail-first onboarding flow
-- ============================================
UPDATE brands b
SET tenant_id = t.id
FROM tenants t
WHERE b.gmail_email = t.email
  AND b.tenant_id IS NULL
  AND b.gmail_connected = true;

-- ============================================
-- 3. Verify: report how many brands still have no tenant_id
-- (View-only — does not modify data)
-- ============================================
-- SELECT COUNT(*) as orphaned_brands FROM brands WHERE tenant_id IS NULL;
-- Run this manually to verify. Should be 0 after the above updates.
