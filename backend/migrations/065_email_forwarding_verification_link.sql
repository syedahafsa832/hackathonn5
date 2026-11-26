-- Migration 065: Support Gmail forwarding confirmation links.
-- Some Gmail confirmation templates provide a clickable Google URL and no
-- numeric code. Store the URL so a tenant admin can open it from tResolv.
ALTER TABLE brands
  ADD COLUMN IF NOT EXISTS inbound_email_verification_link TEXT;
