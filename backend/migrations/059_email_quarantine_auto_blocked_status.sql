-- email_guardian_service.evaluate() re-classified the SAME gmail_message_id
-- via a fresh AI call on every ~15s poll cycle for as long as it stayed
-- within Gmail's date-level `after:` search window - the existing
-- quarantine-row dedup in _create_quarantine_record only ever prevented a
-- duplicate DATABASE ROW; it ran after the AI classification had already
-- happened, and an outright-"blocked" decision (as opposed to a genuine
-- low-confidence "quarantined" one) never persisted anything at all.
--
-- Fix (email_guardian_service.py): look up a persisted decision for this
-- gmail_message_id BEFORE calling the classifier, and persist outright-
-- blocked decisions too - not just quarantined ones - under a new status
-- that never surfaces in the merchant's quarantine review queue.
ALTER TABLE email_quarantine DROP CONSTRAINT IF EXISTS email_quarantine_status_check;
ALTER TABLE email_quarantine ADD CONSTRAINT email_quarantine_status_check
    CHECK (status IN ('pending', 'promoted', 'discarded', 'expired', 'auto_blocked'));
