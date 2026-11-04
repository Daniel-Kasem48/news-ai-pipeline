-- probe: developments.cited_source
-- Where a claim came from: the feed that carried it, and the outlet the post
-- itself credits. Both are needed to tell one source repeated from two sources
-- agreeing, which is the whole basis of a confirmation.

ALTER TABLE classifications ADD COLUMN cited_source VARCHAR(255);

ALTER TABLE developments ADD COLUMN post_id VARCHAR(64);        -- the post that created it
ALTER TABLE developments ADD COLUMN channel VARCHAR(64);        -- which feed it came from
ALTER TABLE developments ADD COLUMN cited_source VARCHAR(255);  -- outlet the post credits, or NULL
