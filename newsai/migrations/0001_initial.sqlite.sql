-- probe: posts.id
-- The schema as it first shipped. Later columns are added by later migrations
-- rather than folded in here, so a fresh database replays the same history an
-- existing one went through and the ALTERs are exercised on every create.

CREATE TABLE IF NOT EXISTS runs (
  run_id      TEXT PRIMARY KEY,
  started_at  TEXT,
  model_a     TEXT,
  model_b     TEXT,
  prompt_a_hash TEXT,
  prompt_b_hash TEXT,
  note        TEXT
);

CREATE TABLE IF NOT EXISTS posts (
  id          TEXT PRIMARY KEY,
  channel     TEXT,
  seq         INTEGER,
  raw         TEXT,
  text        TEXT,
  text_key    TEXT,
  key_hash    TEXT,
  location    TEXT,
  source      TEXT,
  notes       TEXT
);

CREATE TABLE IF NOT EXISTS classifications (
  post_id     TEXT,
  run_id      TEXT,
  class       TEXT,
  reason      TEXT,
  actor       TEXT,
  target      TEXT,
  action      TEXT,
  location    TEXT,
  location_parent TEXT,
  aliases     TEXT,
  novelty_cue TEXT,
  update_cue  TEXT,
  lifecycle_cue TEXT,
  stale_date  INTEGER,
  tokens_in   INTEGER,
  tokens_out  INTEGER,
  tokens_cached INTEGER,
  model       TEXT,
  PRIMARY KEY (post_id, run_id)
);

CREATE TABLE IF NOT EXISTS stories (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id      TEXT,
  actor_norm  TEXT,
  target_norm TEXT,
  location_norm TEXT,
  aliases     TEXT,
  title       TEXT,
  first_seen_at TEXT,
  last_seen_at  TEXT,
  closed_at   TEXT
);

CREATE TABLE IF NOT EXISTS developments (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  story_id    INTEGER,
  run_id      TEXT,
  summary     TEXT,
  action      TEXT,
  kind        TEXT
);

CREATE TABLE IF NOT EXISTS confirmations (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  development_id INTEGER,
  run_id      TEXT,
  post_id     TEXT,
  decision_confidence REAL
);

CREATE TABLE IF NOT EXISTS decisions (
  post_id     TEXT,
  run_id      TEXT,
  decision    TEXT,
  story_id    INTEGER,
  development_id INTEGER,
  confidence  REAL,
  reason      TEXT,
  candidates  TEXT,
  tokens_in   INTEGER,
  tokens_out  INTEGER,
  tokens_cached INTEGER,
  model       TEXT,
  PRIMARY KEY (post_id, run_id)
);

-- How far each channel has been read. This is what "unvisited messages" means:
-- an exact message id, not a wall-clock guess, so nothing is processed twice
-- and nothing is skipped when a run is late or fails.
CREATE TABLE IF NOT EXISTS channel_cursor (
  channel     TEXT PRIMARY KEY,
  handle      TEXT,
  last_msg_id INTEGER,
  updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS place_proposals (
  child       TEXT,
  parent      TEXT,
  run_id      TEXT,
  post_id     TEXT,
  seen        INTEGER DEFAULT 1,
  status      TEXT DEFAULT 'pending',
  PRIMARY KEY (child, parent)
);

CREATE TABLE IF NOT EXISTS labels (
  post_id     TEXT PRIMARY KEY,
  expect      TEXT,
  why         TEXT
);

CREATE INDEX IF NOT EXISTS ix_dev_story    ON developments (story_id);
CREATE INDEX IF NOT EXISTS ix_conf_dev     ON confirmations (development_id);
CREATE INDEX IF NOT EXISTS ix_stories_open ON stories (run_id, closed_at);
