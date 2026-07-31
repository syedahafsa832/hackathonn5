-- Documents brands.aftership_api_key, which already exists in production
-- (added out-of-band in Supabase) and is already read by tracking_service.py
-- for per-brand AfterShip lookups. This migration is idempotent documentation
-- only, so a fresh environment can be provisioned from migrations alone —
-- it is not a functional change to any running system.

ALTER TABLE brands ADD COLUMN IF NOT EXISTS aftership_api_key TEXT;
