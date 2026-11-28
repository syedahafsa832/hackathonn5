-- 066: Enforce a 90-day retention period for resolved email support content.
--
-- Active/open/escalated conversations are retained while the support case is
-- active. A resolved/closed email ticket and its associated AI transcript are
-- eligible for deletion 90 days after the resolution/update timestamp.
--
-- This migration schedules the cleanup; it does not execute a purge at deploy
-- time. The first scheduled run executes at 03:00 UTC.

CREATE EXTENSION IF NOT EXISTS pg_cron WITH SCHEMA pg_catalog;

GRANT USAGE ON SCHEMA cron TO postgres;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA cron TO postgres;

ALTER TABLE public.system_settings
    ALTER COLUMN data_retention_days SET DEFAULT 90;

-- Existing values were the old 180-day default. Shorter merchant settings
-- remain shorter; no setting can extend customer email content beyond 90 days.
UPDATE public.system_settings
SET data_retention_days = 90
WHERE data_retention_days IS NULL OR data_retention_days > 90;


-- Support the bounded daily candidate selection and log cleanup scans.
CREATE INDEX IF NOT EXISTS idx_email_tickets_retention_candidate
    ON public.tickets (
        (COALESCE(resolved_at, updated_at, last_customer_message_at, created_at)),
        id
    )
    WHERE channel = 'email'
      AND lower(COALESCE(status, '')) IN ('resolved', 'auto_resolved', 'closed');

CREATE INDEX IF NOT EXISTS idx_email_filter_log_created_brand
    ON public.email_filter_log (created_at, brand_id);

CREATE INDEX IF NOT EXISTS idx_email_quarantine_created_brand
    ON public.email_quarantine (created_at, brand_id);


CREATE OR REPLACE FUNCTION public.purge_expired_email_support_content()
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_ticket_ids uuid[];
    v_tickets_deleted integer := 0;
    v_actions_scrubbed integer := 0;
    v_brand_actions_scrubbed integer := 0;
    v_ai_events_anonymized integer := 0;
    v_automation_rows_deleted integer := 0;
    v_financial_audit_rows_scrubbed integer := 0;
    v_filter_rows_deleted integer := 0;
    v_quarantine_rows_deleted integer := 0;
BEGIN
    -- Limit each run to 500 tickets and lock candidates so parallel runs do
    -- not select the same rows. Only completed support cases are eligible.
    SELECT COALESCE(array_agg(candidate.id), ARRAY[]::uuid[])
    INTO v_ticket_ids
    FROM (
        SELECT t.id
        FROM public.tickets AS t
        LEFT JOIN public.system_settings AS settings
          ON settings.store_id = t.store_id
        WHERE t.channel = 'email'
          -- auto_resolved_review is still awaiting a human review and is
          -- intentionally excluded; only actually completed cases expire.
          AND lower(COALESCE(t.status, '')) IN (
              'resolved',
              'auto_resolved',
              'closed'
          )
          AND COALESCE(
              t.resolved_at,
              t.updated_at,
              t.last_customer_message_at,
              t.created_at
          ) < now() - make_interval(
              days => LEAST(
                  GREATEST(COALESCE(settings.data_retention_days, 90), 1),
                  90
              )
          )
          -- Never purge a case while a merchant action still needs attention.
          AND NOT EXISTS (
              SELECT 1
              FROM public.actions AS a
              WHERE a.ticket_id = t.id
                AND lower(COALESCE(a.status, '')) NOT IN (
                    'executed',
                    'failed',
                    'rejected',
                    'cancelled',
                    'expired'
                )
          )
          AND NOT EXISTS (
              SELECT 1
              FROM public.brand_actions AS ba
              WHERE ba.ticket_id = t.id
                AND lower(COALESCE(ba.status, '')) NOT IN (
                    'executed',
                    'failed',
                    'rejected',
                    'cancelled',
                    'expired',
                    'completed'
                )
          )
          -- Do not remove the ticket while a scheduled send/automation is
          -- still queued or processing.
          AND NOT EXISTS (
              SELECT 1
              FROM public.email_automation_pending AS ep
              WHERE ep.ticket_id = t.id
                AND lower(COALESCE(ep.status, '')) NOT IN (
                    'sent',
                    'failed',
                    'cancelled',
                    'rejected',
                    'completed',
                    'resolved',
                    'expired'
                )
          )
          AND NOT EXISTS (
              SELECT 1
              FROM public.send_tasks AS st
              WHERE st.ticket_id = t.id
                AND lower(COALESCE(st.status, '')) NOT IN (
                    'sent',
                    'failed',
                    'cancelled',
                    'rejected',
                    'completed',
                    'expired'
                )
          )
        ORDER BY
            COALESCE(
                t.resolved_at,
                t.updated_at,
                t.last_customer_message_at,
                t.created_at
            ),
            t.id
        LIMIT 500
        FOR UPDATE OF t SKIP LOCKED
    ) AS candidate;

    IF cardinality(v_ticket_ids) > 0 THEN
        -- Preserve the minimal operational action history, but remove
        -- customer message text, addresses, and content derived from the
        -- support email. Customer email columns are NOT NULL in these tables,
        -- so they are replaced with an empty value.
        UPDATE public.actions
        SET customer_email = '',
            customer_name = NULL,
            order_id = NULL,
            order_number = NULL,
            order_total = NULL,
            original_message = NULL,
            ai_reasoning = NULL,
            risk_factors = NULL,
            extracted_data = NULL,
            approved_extracted_data = NULL,
            execution_result = NULL,
            error_message = NULL,
            rejection_reason = NULL,
            shopify_response = NULL,
            approved_by = NULL
        WHERE ticket_id = ANY(v_ticket_ids);
        GET DIAGNOSTICS v_actions_scrubbed = ROW_COUNT;

        UPDATE public.brand_actions
        SET customer_email = '',
            customer_name = NULL,
            order_id = NULL,
            original_message = NULL,
            ai_reasoning = NULL,
            risk_factors = NULL,
            extracted_data = NULL,
            execution_result = NULL,
            rejection_reason = NULL,
            approved_by = NULL
        WHERE ticket_id = ANY(v_ticket_ids);
        GET DIAGNOSTICS v_brand_actions_scrubbed = ROW_COUNT;

        -- Keep financial audit rows for traceability, but remove result/error
        -- payloads that could contain order or customer details.
        UPDATE public.financial_action_audit_log
        SET result = NULL,
            error_detail = NULL,
            user_email = NULL,
            ip_address = NULL
        WHERE ticket_id = ANY(v_ticket_ids);
        GET DIAGNOSTICS v_financial_audit_rows_scrubbed = ROW_COUNT;

        -- action_logs are not deleted with actions (the action rows are
        -- retained as minimal audit metadata), so scrub their free-form
        -- details/errors explicitly.
        UPDATE public.action_logs AS al
        SET details = NULL,
            error_message = NULL,
            actor = 'retention_scrubbed'
        FROM public.actions AS a
        WHERE al.action_id = a.id
          AND a.ticket_id = ANY(v_ticket_ids);

        -- Retain aggregate usage events but remove customer identifiers and
        -- the link back to an individual ticket.
        UPDATE public.ai_reply_events
        SET ticket_id = NULL,
            customer_identifier = NULL
        WHERE ticket_id = ANY(v_ticket_ids);
        GET DIAGNOSTICS v_ai_events_anonymized = ROW_COUNT;

        -- Pending automation content must not remain orphaned after the
        -- source ticket is deleted.
        DELETE FROM public.email_automation_pending
        WHERE ticket_id = ANY(v_ticket_ids);
        GET DIAGNOSTICS v_automation_rows_deleted = ROW_COUNT;

        -- Cascades remove ticket message history, AI conversations/retries,
        -- send tasks/logs, and ticket events.
        DELETE FROM public.tickets
        WHERE id = ANY(v_ticket_ids);
        GET DIAGNOSTICS v_tickets_deleted = ROW_COUNT;
    END IF;

    -- Filter/quarantine logs contain sender addresses, thread identifiers,
    -- subjects or message previews. Apply the same per-brand 90-day maximum.
    DELETE FROM public.email_filter_log AS l
    WHERE l.created_at < now() - make_interval(
        days => LEAST(
            GREATEST(
                COALESCE(
                    (
                        SELECT settings.data_retention_days
                        FROM public.system_settings AS settings
                        WHERE settings.store_id = l.brand_id
                        LIMIT 1
                    ),
                    90
                ),
                1
            ),
            90
        )
    );
    GET DIAGNOSTICS v_filter_rows_deleted = ROW_COUNT;

    DELETE FROM public.email_quarantine AS q
    WHERE q.expires_at < now()
       OR q.created_at < now() - make_interval(
            days => LEAST(
                GREATEST(
                    COALESCE(
                        (
                            SELECT settings.data_retention_days
                            FROM public.system_settings AS settings
                            WHERE settings.store_id = q.brand_id
                            LIMIT 1
                        ),
                        90
                    ),
                    1
                ),
                90
            )
       );
    GET DIAGNOSTICS v_quarantine_rows_deleted = ROW_COUNT;

    RETURN jsonb_build_object(
        'email_tickets_deleted', v_tickets_deleted,
        'actions_scrubbed', v_actions_scrubbed,
        'brand_actions_scrubbed', v_brand_actions_scrubbed,
        'ai_reply_events_anonymized', v_ai_events_anonymized,
        'pending_automation_rows_deleted', v_automation_rows_deleted,
        'financial_audit_rows_scrubbed', v_financial_audit_rows_scrubbed,
        'email_filter_logs_deleted', v_filter_rows_deleted,
        'email_quarantine_rows_deleted', v_quarantine_rows_deleted
    );
END;
$$;

-- The purge function is for the scheduled postgres job only, not a public API.
REVOKE ALL ON FUNCTION public.purge_expired_email_support_content() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.purge_expired_email_support_content() FROM anon;
REVOKE ALL ON FUNCTION public.purge_expired_email_support_content() FROM authenticated;
REVOKE ALL ON FUNCTION public.purge_expired_email_support_content() FROM service_role;
GRANT EXECUTE ON FUNCTION public.purge_expired_email_support_content() TO postgres;

-- Keep a single daily job. Re-running this migration will replace the named
-- schedule rather than create duplicate cleanup jobs.
DO $$
DECLARE
    v_existing_job_id bigint;
BEGIN
    SELECT jobid
    INTO v_existing_job_id
    FROM cron.job
    WHERE jobname = 'tresolv-email-content-retention'
    LIMIT 1;

    IF v_existing_job_id IS NOT NULL THEN
        PERFORM cron.unschedule(v_existing_job_id);
    END IF;

    PERFORM cron.schedule(
        'tresolv-email-content-retention',
        '0 3 * * *',
        'SELECT public.purge_expired_email_support_content();'
    );
END;
$$;
