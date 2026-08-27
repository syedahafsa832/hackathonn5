-- Defense-in-depth for the reported "duplicate approvals for one request"
-- bug. Every dedup check in this codebase today (return_actions_
-- integration.py's _find_active_action, actions_service.py's
-- detect_and_create) is a check-then-insert done entirely in application
-- code — there is no DB-level guarantee against two concurrent requests
-- (a genuine race, a retried webhook, overlapping email polls) both
-- passing the "no existing action" check before either has committed its
-- insert, and both creating a real, competing pending action for the same
-- order.
--
-- This adds the missing guarantee at the one place it can be enforced
-- atomically: the database itself. A partial unique index (not a full
-- table constraint) so it only ever blocks a genuine live duplicate — an
-- order can still get a new action of the same type later, once the
-- earlier one has been rejected or has failed (neither status is covered
-- by this index), exactly matching the existing app-level dedup semantics
-- (_find_active_action treats pending/approved/executed as "still active",
-- never rejected/failed).
--
-- supabase_insert() (src/lib/supabase_client.py) already has a dedicated
-- 409-Conflict log-level branch anticipating exactly this kind of
-- constraint — actions_service.create_action() now catches that 409 and
-- returns the existing row as a duplicate_skipped result instead of a raw
-- failure (see that function's docstring).
--
-- NOT applied to production by this change — run via the project's usual
-- migration-apply step. Verified against live data before writing this: 0
-- existing (tenant_id, order_id, action_type) groups with more than one
-- pending/approved/executed row, so this applies cleanly with no cleanup
-- needed first.

CREATE UNIQUE INDEX IF NOT EXISTS idx_actions_dedup_active
  ON actions (tenant_id, order_id, action_type)
  WHERE status IN ('pending', 'approved', 'executed');

COMMENT ON INDEX idx_actions_dedup_active IS
  'Prevents two active (pending/approved/executed) actions of the same '
  'type for the same order under the same tenant. Rejected/failed rows are '
  'excluded on purpose — a new attempt must still be possible after either '
  'outcome. Enforced atomically at the DB level as a backstop for the '
  'existing application-level dedup checks under concurrent requests.';
