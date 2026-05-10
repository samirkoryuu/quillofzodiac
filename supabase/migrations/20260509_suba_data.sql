-- f:\DISCO\hiveworks\supabase\migrations\20260509_suba_data.sql
-- Persistent Profile Metadata Table

CREATE TABLE IF NOT EXISTS suba_data (
    discord_id      TEXT PRIMARY KEY,
    nickname        TEXT,
    nickname_color  TEXT DEFAULT '#FFFFFF',
    official_role   TEXT DEFAULT 'Unverified',
    special_emojis  TEXT,
    avatar_url      TEXT,
    xp              INTEGER DEFAULT 0,
    level           INTEGER DEFAULT 0,
    honey           INTEGER DEFAULT 0,
    cloud_marks     INTEGER DEFAULT 0,
    writer_title    TEXT DEFAULT 'Initiate',
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Ensure this exists in both Supabase and CockroachDB
