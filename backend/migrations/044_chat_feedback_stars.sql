-- Star rating (1-5) for the post-ticket CSAT email, alongside the existing
-- thumbs-up/down 'rating' column the chat widget's SatisfactionRating
-- component writes to. Both feed the same chat_feedback table so merchant-
-- facing feedback views (dashboard, ticket detail) see one unified source.
-- rating_stars is nullable - widget-submitted rows never set it.
--
-- Not applied to production by this change - see handoff notes.

ALTER TABLE chat_feedback ADD COLUMN IF NOT EXISTS rating_stars SMALLINT;

ALTER TABLE chat_feedback DROP CONSTRAINT IF EXISTS chat_feedback_rating_stars_check;
ALTER TABLE chat_feedback ADD CONSTRAINT chat_feedback_rating_stars_check
    CHECK (rating_stars IS NULL OR rating_stars BETWEEN 1 AND 5);
