-- Migration 009: Create initial brand for v1 (single-tenant) users
-- Run this in Supabase SQL Editor if you get "No brand found" errors
-- Adjust the values in the INSERT to match your store

-- Step 1: Relax any NOT NULL constraints that block standalone use
ALTER TABLE brands ALTER COLUMN organization_id DROP NOT NULL;
ALTER TABLE brands ALTER COLUMN slug            DROP NOT NULL;
ALTER TABLE brands ALTER COLUMN shopify_shop_name DROP NOT NULL;
ALTER TABLE brands ALTER COLUMN shopify_access_token DROP NOT NULL;
ALTER TABLE brands ALTER COLUMN support_email   DROP NOT NULL;

-- Step 2: Add missing columns (safe to run multiple times)
ALTER TABLE brands ADD COLUMN IF NOT EXISTS shopify_domain    VARCHAR(255);
ALTER TABLE brands ADD COLUMN IF NOT EXISTS shopify_connected BOOLEAN DEFAULT false;
ALTER TABLE brands ADD COLUMN IF NOT EXISTS shopify_shop_name VARCHAR(255);

-- Step 3: Insert a default brand (only if table is empty)
INSERT INTO brands (name, shopify_shop_name, shopify_domain, is_active)
SELECT 'My Store', 'Default Store', NULL, true
WHERE NOT EXISTS (SELECT 1 FROM brands WHERE is_active = true);
