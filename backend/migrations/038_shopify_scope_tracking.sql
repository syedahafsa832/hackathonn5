-- Track the Shopify scopes actually granted to a brand's stored access
-- token, checked live at connect time (and lazily refreshed for brands
-- connected before this migration). Lets the app tell a merchant exactly
-- which permissions are missing instead of discovering it mid-import.
ALTER TABLE brands ADD COLUMN IF NOT EXISTS shopify_granted_scopes JSONB;
ALTER TABLE brands ADD COLUMN IF NOT EXISTS shopify_app_name TEXT;
ALTER TABLE brands ADD COLUMN IF NOT EXISTS shopify_scopes_checked_at TIMESTAMPTZ;
