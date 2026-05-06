-- INTEL DB MIGRATION: Run this on BOTH MainCock AND MainButt
-- These are identical on both DBs. Each DB stores its own shard of data.

-- 1. Intel Writers (tracked Webnovel authors)
CREATE TABLE IF NOT EXISTS intel_writers (
    profile_id      TEXT PRIMARY KEY,
    penname         TEXT,
    country         TEXT,
    last_scan_at    BIGINT DEFAULT 0,
    first_seen_at   BIGINT DEFAULT extract(epoch FROM now()),
    integer_id      BIGINT  -- The sequencer ID (odd on DB1, even on DB2)
);

-- 2. Intel Books (the permanent archive of scraped book data)
CREATE TABLE IF NOT EXISTS intel_books (
    book_id         TEXT NOT NULL,
    profile_id      TEXT NOT NULL,
    title           TEXT,
    genre           TEXT,
    chapter_count   INTEGER DEFAULT 0,
    collections     INTEGER DEFAULT 0,
    views           TEXT,
    power_ranking   INTEGER,    -- NULL if not in Top 200
    last_change_at  BIGINT DEFAULT extract(epoch FROM now()),
    PRIMARY KEY (book_id, profile_id)
);

-- 3. Mission History (log of every completed phone mission)
CREATE TABLE IF NOT EXISTS mission_history (
    task_id         TEXT PRIMARY KEY,
    profile_id      TEXT,
    node_id         TEXT,        -- Which phone completed it
    status          TEXT DEFAULT 'completed',
    created_at      BIGINT,
    completed_at    BIGINT,
    duration_s      INTEGER      -- How long the phone took
);

-- 4. Node Registry (which phones belong to this shard)
CREATE TABLE IF NOT EXISTS node_registry (
    node_id         TEXT PRIMARY KEY,
    last_heartbeat  BIGINT DEFAULT extract(epoch FROM now()),
    version         TEXT,
    status          TEXT DEFAULT 'online'
);

-- Indexes for sweeper and bot query performance
CREATE INDEX IF NOT EXISTS idx_intel_books_profile   ON intel_books(profile_id);
CREATE INDEX IF NOT EXISTS idx_intel_books_rank      ON intel_books(power_ranking);
CREATE INDEX IF NOT EXISTS idx_mission_history_node  ON mission_history(node_id);
CREATE INDEX IF NOT EXISTS idx_mission_history_time  ON mission_history(created_at);
