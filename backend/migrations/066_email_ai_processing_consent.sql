-- Record explicit merchant consent before processing forwarded email with AI.
-- This consent is versioned so future material changes can require re-consent.
ALTER TABLE public.brands
    ADD COLUMN IF NOT EXISTS email_ai_processing_consent_at timestamptz,
    ADD COLUMN IF NOT EXISTS email_ai_processing_consent_version text;

-- Permanently retire the old inbox-reading integration. Live DB audit found
-- no brands marked gmail_connected; four dormant encrypted legacy tokens
-- remained. The product now uses merchant forwarding for inbound and a
-- separate gmail.send-only token for outbound.
UPDATE public.brands
SET gmail_connected = false,
    gmail_token = NULL,
    gmail_email = NULL,
    gmail_scope_mode = 'send_only'
WHERE gmail_connected IS DISTINCT FROM false
   OR gmail_token IS NOT NULL
   OR gmail_email IS NOT NULL
   OR gmail_scope_mode IS DISTINCT FROM 'send_only';

-- Existing forwarding configurations do not constitute consent to the current
-- disclosure. Pause inbound processing until an authorised merchant admin
-- explicitly accepts the current notice in the product.
UPDATE public.brands
SET inbound_email_enabled = false,
    email_ingestion_mode = 'gmail_polling',
    updated_at = now()
WHERE inbound_email_enabled = true
  AND email_ai_processing_consent_version IS DISTINCT FROM '2026-11-28-v1';

CREATE INDEX IF NOT EXISTS idx_brands_email_ai_consent
    ON public.brands (email_ai_processing_consent_version, is_active)
    WHERE inbound_email_enabled = true;
