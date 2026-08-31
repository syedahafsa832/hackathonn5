-- Persists a customer message's AI response as retryable work when every
-- configured AI provider (all Mistral keys, all Groq fallback keys) failed
-- for that request — instead of the previous behavior of immediately and
-- permanently marking the ticket "escalated". Free-tier provider
-- quota/rate-limit exhaustion is expected to recover on its own; a merchant
-- should not have to notice and manually retry every such message.
--
-- One row per pending retry attempt for a ticket. Only the backend
-- (service_role, via provider_retry_worker.py) reads/writes this table.
CREATE TABLE IF NOT EXISTS ai_response_retries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_id UUID NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    brand_id UUID REFERENCES brands(id) ON DELETE CASCADE,
    -- pending: waiting for next_retry_at. processing: claimed by a worker
    -- (single-process deployment — same "fine for this deployment" tradeoff
    -- already made by shopify_import_service.py's in-memory _import_status).
    -- succeeded: response generated and sent/staged. cancelled: a stop
    -- condition fired (human took over, human replied, or a newer customer
    -- message superseded this one). exhausted: retry_count hit max_retries
    -- with no success — falls back to a real human escalation.
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'succeeded', 'cancelled', 'exhausted')),
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 6,
    -- 'retryable' (rate limit/quota/timeout/5xx — reused from
    -- ai_provider_manager._describe()) gets the full bounded schedule
    -- (spans hours, matching "quota resets on its own"). 'fast_fail'
    -- (everything else — e.g. an invalid key/model) gets a short schedule
    -- so a genuine misconfiguration still reaches a human quickly instead
    -- of sitting unescalated for hours.
    outage_tier TEXT NOT NULL DEFAULT 'retryable'
        CHECK (outage_tier IN ('retryable', 'fast_fail')),
    next_retry_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One active (pending/processing) retry per ticket — a second provider
-- outage on the same ticket before the first retry resolves must not queue
-- a duplicate job that could regenerate/send a second response.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_response_retries_one_active_per_ticket
    ON ai_response_retries(ticket_id) WHERE status IN ('pending', 'processing');

CREATE INDEX IF NOT EXISTS idx_ai_response_retries_due
    ON ai_response_retries(status, next_retry_at);

ALTER TABLE ai_response_retries ENABLE ROW LEVEL SECURITY;
REVOKE SELECT, INSERT, UPDATE, DELETE ON ai_response_retries FROM anon;
REVOKE SELECT, INSERT, UPDATE, DELETE ON ai_response_retries FROM authenticated;
