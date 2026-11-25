-- Migration 065: separate Gmail send-only credentials and safe forwarding activation.
-- Apply only after 064. Existing full-access Gmail credentials remain in the
-- original gmail_* columns. Earlier draft callbacks stored send-only tokens in
-- those legacy columns; migrate only rows explicitly marked send_only.
ALTER TABLE brands
  ADD COLUMN IF NOT EXISTS gmail_send_token TEXT,
  ADD COLUMN IF NOT EXISTS gmail_send_connected BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS gmail_send_email TEXT;

-- Recover send-only credentials from the old columns only when the marker
-- proves that row used the send-only callback. Any previously overwritten
-- full-access token cannot be reconstructed and must be reconnected if needed.
UPDATE brands
SET
  gmail_send_token = COALESCE(gmail_send_token, gmail_token),
  gmail_send_connected = gmail_connected,
  gmail_send_email = COALESCE(gmail_send_email, gmail_email),
  gmail_token = NULL,
  gmail_connected = false,
  gmail_email = NULL,
  gmail_scope_mode = 'full',
  updated_at = NOW()
WHERE gmail_scope_mode = 'send_only'
  AND gmail_token IS NOT NULL
  AND gmail_send_token IS NULL;
