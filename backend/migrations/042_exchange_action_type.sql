-- Full return/exchange automation: exchange execution (actions_service.py's
-- EXCHANGE branch, calling ShopifyClient.create_exchange_draft_order) is a
-- real financial/inventory mutation exactly like refund and cancel_order,
-- so it needs the same idempotency-key replay + audit trail those two
-- already get via financial_action_audit_log — otherwise a retried/
-- double-clicked exchange approval could create two Shopify draft orders
-- for the same customer request.
--
-- actions.action_type itself needs no migration — it's a free VARCHAR(50)
-- with no CHECK constraint (migration 006's schema comment already
-- anticipated "exchange" as a value: "-- refund, cancel_order,
-- change_address, discount, exchange").

ALTER TABLE financial_action_audit_log
    DROP CONSTRAINT IF EXISTS financial_action_audit_log_action_type_check;

ALTER TABLE financial_action_audit_log
    ADD CONSTRAINT financial_action_audit_log_action_type_check
    CHECK (action_type IN ('cancel_order', 'refund', 'exchange'));
