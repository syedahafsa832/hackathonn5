-- Reship truthfulness fix: approve_action() no longer marks a reship
-- 'executed' the moment a merchant clicks Approve (see actions_service.py) -
-- Reship is manual and Shopify is never called, so it now sits in a new
-- 'awaiting_manual_step' status until the merchant explicitly confirms they
-- created the replacement shipment by hand.
--
-- This migration is required because migration 053's partial unique index
-- (idx_actions_dedup_active) is what actually enforces "no second active
-- action for the same tenant+order+type" at the DB level. Its WHERE clause
-- hardcodes the set of statuses considered "active" as
-- ('pending','approved','executed'). Without updating it, a reship sitting
-- in the new 'awaiting_manual_step' status would fall outside that set,
-- and a merchant (or the AI) could stage a second, duplicate reship for the
-- same order while the first one is still waiting on manual completion -
-- exactly the duplicate-prevention regression this task must not introduce.
--
-- No other status semantics change. Rejected/failed rows remain excluded,
-- unchanged from 053's original intent.
--
-- NOT applied to production by this change - run via the project's usual
-- migration-apply step.

DROP INDEX IF EXISTS idx_actions_dedup_active;

CREATE UNIQUE INDEX IF NOT EXISTS idx_actions_dedup_active
  ON actions (tenant_id, order_id, action_type)
  WHERE status IN ('pending', 'approved', 'executed', 'awaiting_manual_step');

COMMENT ON INDEX idx_actions_dedup_active IS
  'Prevents two active (pending/approved/executed/awaiting_manual_step) '
  'actions of the same type for the same order under the same tenant. '
  'Rejected/failed rows are excluded on purpose - a new attempt must still '
  'be possible after either outcome.';
