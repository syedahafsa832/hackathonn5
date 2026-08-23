-- Adds the merchant-controlled Refund Autopilot flag to brands.
--
-- Separate flag from cancellation_autopilot_enabled (047) - refunds are
-- financially sensitive and are gated independently. Defaults to false
-- (fail-closed): every refund request continues through the existing
-- Copilot (human-approval) flow exactly as before, both before this
-- migration is applied and after, until a merchant explicitly opts in
-- through the dedicated, server-verified activation endpoint
-- (POST /api/v2/brands/{brand_id}/automation/refund/enable - never a
-- generic settings update, never a raw column write from the frontend).
--
-- Deliberately a single additive boolean - no backfill, no NOT NULL, no
-- change to any existing column. Application code
-- (src/services/return_actions_integration.py, src/api/routes/v2_brands.py)
-- reads this column via supabase_client.supabase_select()'s plain
-- `SELECT *`-style REST calls, which return the column as missing/None
-- (falsy) rather than erroring when the underlying column doesn't exist
-- yet - so referencing `refund_autopilot_enabled` in code is safe even
-- before this migration has been applied; it just always evaluates to
-- "not enabled" until then, which is the correct fail-safe default.
--
-- NOT applied to production by this change - create only, per task
-- instructions (STOP CONDITIONS: "applying another production migration
-- without first reporting it"). Apply with the project's usual
-- migration-apply step (e.g. supabase db push, or the Supabase MCP
-- apply_migration tool) only when explicitly instructed.

ALTER TABLE brands ADD COLUMN IF NOT EXISTS refund_autopilot_enabled BOOLEAN DEFAULT false;

COMMENT ON COLUMN brands.refund_autopilot_enabled IS
  'Merchant-controlled kill switch for Refund Autopilot (this feature '
  'only - cancellations use their own independent flag, '
  'cancellation_autopilot_enabled; exchanges/address changes remain '
  'human-approved regardless of either flag). Only ever set true by the '
  'enable endpoint after re-verifying readiness/entitlement/ownership '
  'server-side. Autopilot only ever executes a full, whole-order refund '
  'for a Shopify-computed amount (never a partial amount proposed by the '
  'model or a customer-stated figure) - see '
  '_maybe_autopilot_refund() in return_actions_integration.py.';
