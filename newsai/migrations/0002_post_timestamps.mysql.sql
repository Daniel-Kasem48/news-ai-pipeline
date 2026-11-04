-- probe: posts.ts
-- Message time, message id and deep link. msg_id is what channel_cursor
-- compares against, so ingest stops guessing from wall-clock time.

ALTER TABLE posts ADD COLUMN ts VARCHAR(40);   -- ISO-8601, NULL when the source gives no time
ALTER TABLE posts ADD COLUMN msg_id BIGINT;    -- id within its channel; NULL for pasted corpora
ALTER TABLE posts ADD COLUMN url VARCHAR(512); -- deep link back to the original post, when known

ALTER TABLE posts ADD INDEX ix_posts_ts (ts);
