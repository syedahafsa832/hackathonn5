-- Adds the merchant-controlled Cancellation Autopilot flag to brands.
--
-- Defaults to false (fail-closed): every cancellation request continues
-- through the existing Copilot (human-approval) flow exactly as before,
-- both before this migration is applied and after, until a merchant
-- explicitly opts in through the dedicated, server-verified activation
-- endpoint (POST /api/v2/brands/{brand_id}/automation/cancellation/enable
-- - never a generic settings update, never a raw column write from the
-- frontend).
--
-- Deliberately a single additive boolean - no backfill, no NOT NULL, no
-- change to any existing column. Application code
-- (src/services/return_actions_integration.py, src/api/routes/v2_brands.py)
-- reads this column via supabase_client.supabase_select()'s plain
-- `SELECT *`-style REST calls, which return the column as missing/None
-- (falsy) rather than erroring when the underlying column doesn't exist
-- yet - so referencing `cancellation_autopilot_enabled` in code is safe
-- even before this migration has been applied; it just always evaluates
-- to "not enabled" until then, which is the correct fail-safe default.
--
-- NOT applied to production by this change - create only, per task
-- instructions. Apply with the project's usual migration-apply step
-- (e.g. supabase db push) when the team is ready.

ALTER TABLE brands ADD COLUMN IF NOT EXISTS cancellation_autopilot_enabled BOOLEAN DEFAULT false;

COMMENT ON COLUMN brands.cancellation_autopilot_enabled IS
  'Merchant-controlled kill switch for Cancellation Autopilot (this feature '
  'only - refunds/exchanges/address changes remain human-approved '
  'regardless of this flag). Only ever set true by the enable endpoint '
  'after re-verifying readiness/entitlement/ownership server-side; the '
  'disable endpoint (or turning this false directly) takes effect '
  'immediately for new requests but never touches an action that already '
  'claimed "approved" and is mid-flight against Shopify.';
