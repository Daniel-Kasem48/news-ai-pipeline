-- probe: posts.id
-- The schema as it first shipped. Later columns are added by later migrations
-- rather than folded in here, so a fresh database replays the same history an
-- existing one went through and the ALTERs are exercised on every create.
--
-- Every table is utf8mb4_bin, not the server default: the default collation
-- ignores diacritics and half of these keys are Arabic. Widths come from the
-- corpus with room to spare — TEXT cannot be indexed in MySQL, so every key
-- had to be given one.

CREATE TABLE IF NOT EXISTS runs (
  run_id        VARCHAR(64) PRIMARY KEY,
  started_at    VARCHAR(40),
  model_a       VARCHAR(128),
  model_b       VARCHAR(128),
  prompt_a_hash VARCHAR(64),
  prompt_b_hash VARCHAR(64),
  note          TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS posts (
  id          VARCHAR(64) PRIMARY KEY,
  channel     VARCHAR(64),
  seq         INT,
  raw         TEXT,
  text        TEXT,
  text_key    TEXT,
  key_hash    VARCHAR(64),
  location    VARCHAR(255),
  source      VARCHAR(255),
  notes       TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS classifications (
  post_id       VARCHAR(64),
  run_id        VARCHAR(64),
  class         VARCHAR(64),
  reason        TEXT,
  actor         VARCHAR(255),
  target        VARCHAR(255),
  action        VARCHAR(255),
  location      VARCHAR(255),
  location_parent VARCHAR(255),
  aliases       TEXT,
  novelty_cue   VARCHAR(255),
  update_cue    VARCHAR(255),
  lifecycle_cue VARCHAR(255),
  stale_date    INT,
  tokens_in     INT,
  tokens_out    INT,
  tokens_cached INT,
  model         VARCHAR(128),
  PRIMARY KEY (post_id, run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS stories (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  run_id        VARCHAR(64),
  actor_norm    VARCHAR(255),
  target_norm   VARCHAR(255),
  location_norm VARCHAR(255),
  aliases       TEXT,
  title         TEXT,
  first_seen_at VARCHAR(40),
  last_seen_at  VARCHAR(40),
  closed_at     VARCHAR(40),
  INDEX ix_stories_open (run_id, closed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS developments (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  story_id   INT,
  run_id     VARCHAR(64),
  summary    TEXT,
  action     VARCHAR(255),
  kind       VARCHAR(64),
  INDEX ix_dev_story (story_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS confirmations (
  id             INT AUTO_INCREMENT PRIMARY KEY,
  development_id INT,
  run_id         VARCHAR(64),
  post_id        VARCHAR(64),
  decision_confidence DOUBLE,
  INDEX ix_conf_dev (development_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS decisions (
  post_id        VARCHAR(64),
  run_id         VARCHAR(64),
  decision       VARCHAR(64),
  story_id       INT,
  development_id INT,
  confidence     DOUBLE,
  reason         TEXT,
  candidates     TEXT,
  tokens_in      INT,
  tokens_out     INT,
  tokens_cached  INT,
  model          VARCHAR(128),
  PRIMARY KEY (post_id, run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- How far each channel has been read. This is what "unvisited messages" means:
-- an exact message id, not a wall-clock guess, so nothing is processed twice
-- and nothing is skipped when a run is late or fails.
CREATE TABLE IF NOT EXISTS channel_cursor (
  channel     VARCHAR(64) PRIMARY KEY,
  handle      VARCHAR(128),
  last_msg_id BIGINT,
  updated_at  VARCHAR(40)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- child+parent is the primary key at 191 characters each, which is 1528 bytes
-- at 4 bytes per character — inside InnoDB's 3072-byte index limit.
CREATE TABLE IF NOT EXISTS place_proposals (
  child   VARCHAR(191),
  parent  VARCHAR(191),
  run_id  VARCHAR(64),
  post_id VARCHAR(64),
  seen    INT DEFAULT 1,
  status  VARCHAR(32) DEFAULT 'pending',
  PRIMARY KEY (child, parent)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS labels (
  post_id VARCHAR(64) PRIMARY KEY,
  expect  TEXT,
  why     TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
