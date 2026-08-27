-- Merchant opt-in for whether a customer-facing placeholder reply is sent
-- during a temporary AI-provider outage (all configured Mistral/Groq keys
-- failing at once). Defaults to false: without this explicit opt-in, a
-- provider outage produces no customer-facing reply at all - the message
-- is still saved and the ticket still escalates for human review, but
-- nothing goes out claiming a human is on it unless the merchant has
-- turned this on. When enabled, customer_success_agent.py sends the fixed,
-- deliberately generic placeholder text (see _get_provider_failure_response
-- / _PROVIDER_OUTAGE_CUSTOMER_MESSAGE) - never Luna's own generated wording,
-- since none exists for this request.
--
-- Same additive, no-backfill-needed pattern as the existing per-brand
-- automation toggles (cancellation_autopilot_enabled, refund_autopilot_enabled).
--
-- NOT applied to production by this change - run via the project's usual
-- migration-apply step.

ALTER TABLE brands ADD COLUMN IF NOT EXISTS provider_outage_fallback_enabled BOOLEAN DEFAULT false;

COMMENT ON COLUMN brands.provider_outage_fallback_enabled IS
  'When true, a temporary AI-provider outage (all configured providers '
  'failing on one request) sends a fixed, generic customer-facing '
  'placeholder message instead of no reply at all. The ticket always '
  'escalates for human review either way - this only controls whether the '
  'customer sees an immediate acknowledgement in the meantime. Default '
  'false: no customer-facing text is sent during an outage unless the '
  'merchant explicitly turns this on.';
