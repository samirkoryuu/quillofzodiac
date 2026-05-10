-- ============================================================
-- COCKROACHDB MASTER SCHEMA
-- Run this on: MainCock (DB1) AND MainButt (DB2)
-- Each DB stores its OWN shard of users by integer_id parity
-- ============================================================

-- 1. ECONOMY TABLE — the master wallet of every Discord member
--    Source of truth for ALL monetary and cosmetic data.
--    Written to by: Bot (direct) + HiveHub bulk_push
--    Read by:       Bot commands + HiveHub /sync-stats endpoint
CREATE TABLE IF NOT EXISTS economy (
    discord_id      BIGINT      NOT NULL,
    guild_id        BIGINT      NOT NULL,
    honey           INTEGER     DEFAULT 0,           -- Primary currency
    xp              INTEGER     DEFAULT 0,           -- Experience points
    level           INTEGER     DEFAULT 0,           -- Current level
    title           TEXT,                            -- e.g. "Wordsmith", "Inkling"
    nickname_color  TEXT        DEFAULT '#FFFFFF',   -- Hex color for mobile profile header
    official_role   TEXT        DEFAULT 'NODE',      -- e.g. "SERVER", "MODERATOR", "NODE"
    special_emojis  TEXT,                            -- Comma-sep custom badge emojis
    avatar_url      TEXT,                            -- Synced from Discord via bot
    cloud_marks     INTEGER     DEFAULT 0,           -- Earned by completing phone missions
    PRIMARY KEY (discord_id, guild_id)
);

-- 2. WRITER_STATS — Detailed stats for authors
--    Written to by: HiveHub /sync-stats + archive_sweeper
--    Read by:       Bot + FINALAPP (via sync)
CREATE TABLE IF NOT EXISTS writers (
    discord_id              BIGINT      PRIMARY KEY,
    penname                 TEXT,
    webnovel_user_id        TEXT,
    webnovel_user_country   TEXT,
    
    -- Main Book
    main_book_url           TEXT,
    main_book_name          TEXT,
    main_book_chapter_count INTEGER     DEFAULT 0,

    -- Totals
    book_count              INTEGER     DEFAULT 0,
    
    -- Highest (Rank Basis)
    highest_chapter_count   INTEGER     DEFAULT 0,
    highest_chapter_book    TEXT,
    highest_chapter_genre   TEXT,
    highest_chapter_tags    TEXT[],
    highest_word_count      BIGINT      DEFAULT 0,
    
    -- Latest
    latest_chapter_count    INTEGER     DEFAULT 0,
    latest_chapter_book     TEXT,
    latest_chapter_genre    TEXT,
    latest_chapter_tags     TEXT[],
    latest_word_count       BIGINT      DEFAULT 0,
    latest_book_updated_at  TIMESTAMPTZ,
    
    individual_word_count   BIGINT      DEFAULT 0,
    webnovel_link           TEXT,
    last_updated            BIGINT      DEFAULT extract(epoch FROM now()),
    device_id               TEXT
);

-- 3. INTEL_WRITERS — scraped Webnovel profile metadata (from phone missions)
CREATE TABLE IF NOT EXISTS intel_writers (
    profile_id      TEXT        PRIMARY KEY,
    penname         TEXT,
    country         TEXT,
    last_scan_at    BIGINT      DEFAULT 0,
    first_seen_at   BIGINT      DEFAULT extract(epoch FROM now()),
    integer_id      BIGINT
);

-- 4. INTEL_BOOKS — permanent archive of scraped book stats per mission
--    Written to by: archive_sweeper (HiveHub hourly batch)
--    Read by:       Bot scheduler for milestone detection
CREATE TABLE IF NOT EXISTS intel_books (
    book_id         TEXT        NOT NULL,
    profile_id      TEXT        NOT NULL,
    title           TEXT,
    genre           TEXT,
    chapter_count   INTEGER     DEFAULT 0,
    collections     INTEGER     DEFAULT 0,
    views           TEXT,
    power_ranking   INTEGER,                         -- NULL if not in Top 200
    last_change_at  BIGINT      DEFAULT extract(epoch FROM now()),
    PRIMARY KEY (book_id, profile_id)
);

-- 5. MEMBER_WORD_COUNTS — log of every OCR screenshot upload
--    Written to by: inkstone_ocr.py (Bot cog)
--    Read by:       AutoRankStore for rank evaluation
CREATE TABLE IF NOT EXISTS member_word_counts (
    id              BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    discord_id      BIGINT      NOT NULL,
    guild_id        BIGINT      NOT NULL,
    book_title      TEXT,
    chapter_count   INTEGER     DEFAULT 0,
    word_count      INTEGER     DEFAULT 0,
    source          TEXT,                            -- 'ocr', 'webnovel', 'manual'
    raw_payload     TEXT,
    ts              BIGINT      NOT NULL
);

-- 6. RANK_AUDIT — immutable log of every promotion/demotion
--    Written to by: AutoRankStore after rank change
--    Read by:       Admin audit commands
CREATE TABLE IF NOT EXISTS rank_audit (
    id              BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    discord_id      BIGINT      NOT NULL,
    guild_id        BIGINT      NOT NULL,
    old_rank        TEXT,
    new_rank        TEXT,
    total_words     BIGINT,
    total_chapters  INTEGER,
    basis           TEXT,                            -- 'ocr', 'webnovel', 'admin'
    ts              BIGINT      NOT NULL
);

-- 7. MEMBER_INTROS — verification desk answers + AI summary
--    Written to by: verification.py (Bot cog) + AIScribeCog
--    Read by:       AIScribeCog for NULL summary sweep
CREATE TABLE IF NOT EXISTS member_intros (
    user_id         BIGINT      NOT NULL,
    guild_id        BIGINT      NOT NULL,
    kind            TEXT,                            -- 'writer', 'reader', 'bot'
    answers_json    TEXT,
    summary         TEXT,                            -- NULL until AI generates it
    created_at      TEXT,
    PRIMARY KEY (user_id, guild_id, kind)
);

-- 8. BADGES — cosmetic badges earned by members
--    Written to by: /givebadge admin command
--    Read by:       Economy cog for display + sync
CREATE TABLE IF NOT EXISTS badges (
    discord_id      BIGINT      NOT NULL,
    guild_id        BIGINT      NOT NULL,
    badge_id        TEXT        NOT NULL,
    PRIMARY KEY (discord_id, guild_id, badge_id)
);

-- 9. MISSION_HISTORY — log of every phone node mission
--    Written to by: HiveHub task watcher on archive
CREATE TABLE IF NOT EXISTS mission_history (
    task_id         TEXT        PRIMARY KEY,
    discord_id      BIGINT,                          -- Who got rewarded
    node_id         TEXT,                            -- Which phone completed it
    url             TEXT,
    status          TEXT        DEFAULT 'completed',
    cloud_marks_awarded INTEGER DEFAULT 10,
    created_at      BIGINT,
    completed_at    BIGINT
);

-- 10. STREAKS — daily writing streaks per member
--     Written to by: /daily command (Bot cog)
CREATE TABLE IF NOT EXISTS streaks (
    discord_id      BIGINT      NOT NULL,
    guild_id        BIGINT      NOT NULL,
    current         INTEGER     DEFAULT 0,
    best            INTEGER     DEFAULT 0,
    last_date       TEXT,
    PRIMARY KEY (discord_id, guild_id)
);

-- 11. RANK_CHECK_REQUESTS — cooldown tracking for rank checks
CREATE TABLE IF NOT EXISTS rank_check_requests (
    discord_id      BIGINT      PRIMARY KEY,
    last_check_ts   BIGINT      DEFAULT 0
);

-- 12. EXTRA_RULES — AI-managed dynamic directives
CREATE TABLE IF NOT EXISTS extra_rules (
    id              SERIAL      PRIMARY KEY,
    rule_category   TEXT        DEFAULT 'general',
    content         TEXT        NOT NULL,
    raw_prompt      TEXT,
    created_by      BIGINT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    is_active       BOOLEAN     DEFAULT TRUE
);

-- ============================================================
-- INDEXES for performance
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_economy_discord       ON economy(discord_id);
CREATE INDEX IF NOT EXISTS idx_writers_penname       ON writers(pen_name);
CREATE INDEX IF NOT EXISTS idx_intel_books_profile   ON intel_books(profile_id);
CREATE INDEX IF NOT EXISTS idx_intel_books_rank      ON intel_books(power_ranking);
CREATE INDEX IF NOT EXISTS idx_mwc_member            ON member_word_counts(discord_id, guild_id, ts);
CREATE INDEX IF NOT EXISTS idx_rank_audit_member     ON rank_audit(discord_id, ts);
CREATE INDEX IF NOT EXISTS idx_mission_discord       ON mission_history(discord_id);
