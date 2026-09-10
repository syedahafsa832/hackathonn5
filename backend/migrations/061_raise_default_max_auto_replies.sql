-- Raise the default max_auto_replies ceiling from 2 to 5.
--
-- 2 was too aggressive for legitimate Shopify support conversations,
-- which naturally run 4-6 turns. This only changes the COLUMN DEFAULT
-- applied to future rows that don't explicitly set the value - every
-- existing brand's already-stored max_auto_replies (whether 2, an
-- explicit override, or anything else) is left exactly as-is.
ALTER TABLE system_settings ALTER COLUMN max_auto_replies SET DEFAULT 5;
