-- Adds edit-tracking to the actions table so a readiness computation can
-- eventually distinguish "approved exactly as AI-proposed" from "human
-- corrected it before approving" - a signal the existing schema has no way
-- to express today. extracted_data already holds the AI's original
-- proposal and is left untouched by this change (no duplication of
-- existing data); these two columns capture only what's genuinely new:
-- what was actually approved-with, and whether that differs from the
-- proposal.
--
-- Deliberately minimal and additive - no backfill, no NOT NULL, no
-- destructive change to any existing column. was_edited defaults to false
-- (accurate for every historical row: none of them had an edit surface at
-- all before this).
--
-- Written to match actions_service.py's only current edit surface: the
-- human-entered refund amount override at approval time
-- (ApproveActionRequest.amount / override_amount in approve_action()).
-- No other action type (cancel_order, change_address, reship, exchange,
-- restore_order) has any editable field in the approval UI today, so
-- approved_extracted_data is only ever populated for refunds until a
-- future change adds an edit surface elsewhere - see approve_action()'s
-- _record_edit_tracking() call for exactly where.
--
-- NOT applied to production by this change. See
-- specs/007-autopilot-automation/pre-autopilot-safety-fixes.md for the
-- exact command to run when the team is ready:
--   supabase db push   (or the project's usual migration-apply step)

ALTER TABLE actions ADD COLUMN IF NOT EXISTS was_edited BOOLEAN DEFAULT false;
ALTER TABLE actions ADD COLUMN IF NOT EXISTS approved_extracted_data JSONB;

COMMENT ON COLUMN actions.was_edited IS
  'true if the values actually used at approval/execution time differ from '
  'extracted_data (the AI''s original proposal) - e.g. a human-entered '
  'partial refund amount overriding the proposed amount. false for every '
  'action type with no current edit surface (cancel_order, change_address, '
  'reship, exchange, restore_order) - not because they were verified '
  'unedited, but because nothing in the approval UI lets them be edited yet.';

COMMENT ON COLUMN actions.approved_extracted_data IS
  'Snapshot of the values actually approved/executed with, only populated '
  'when they differ from extracted_data (i.e. when was_edited=true). NULL '
  'means "nothing to compare" (no edit surface for this action type today, '
  'or no edit was made) - never treat NULL as "we checked and it matched".';
