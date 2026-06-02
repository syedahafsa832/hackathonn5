-- Migration 010: Add tenant_id to brands table for multi-tenant isolation
-- Run in Supabase SQL editor (Dashboard → SQL editor → New query)

-- 1. Add column (nullable so existing rows don't break)
ALTER TABLE brands
  ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id);

-- 2. Backfill: link existing brand rows to tenants via shopify_domain
UPDATE brands b
SET tenant_id = t.id
FROM tenants t
WHERE t.shopify_domain IS NOT NULL
  AND t.shopify_domain != ''
  AND b.shopify_domain = t.shopify_domain
  AND b.tenant_id IS NULL;

-- 3. Index for fast per-tenant lookups
CREATE INDEX IF NOT EXISTS idx_brands_tenant_id ON brands(tenant_id)
  WHERE tenant_id IS NOT NULL;

-- Verify: check how many brands got linked
SELECT
  COUNT(*) FILTER (WHERE tenant_id IS NOT NULL) AS linked,
  COUNT(*) FILTER (WHERE tenant_id IS NULL)     AS unlinked
FROM brands;
