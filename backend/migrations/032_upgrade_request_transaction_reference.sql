-- Item 10 of the bug-fix/polish pass: customers pay by manual bank transfer,
-- then optionally paste their transaction reference/proof of payment when
-- submitting an upgrade request, so the admin can match payments to requests
-- before activating. Idempotent — safe to run against an existing table.

ALTER TABLE upgrade_requests
    ADD COLUMN IF NOT EXISTS transaction_reference TEXT;
