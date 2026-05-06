-- BOTSUBA MIGRATION: Bot Write Buffer
-- Run this in the BotSuba (Gmail 2) SQL Editor

-- Temporary write buffer for all bot economy/stats writes
CREATE TABLE IF NOT EXISTS public.bot_writes (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id    TEXT NOT NULL,        -- Discord user_id or profile_id
    owner_type  TEXT DEFAULT 'discord_user',
    guild_id    BIGINT,
    discord_id  BIGINT,
    honey       INTEGER DEFAULT 0,   -- Honey delta (+/-)
    xp          INTEGER DEFAULT 0,   -- XP delta (+/-)
    action      TEXT,                -- e.g. 'give_honey', 'add_xp', 'purchase'
    metadata    JSONB,               -- Any extra data (item bought, reason, etc.)
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Index for bulk push worker performance
CREATE INDEX IF NOT EXISTS idx_bot_writes_owner    ON public.bot_writes(owner_id);
CREATE INDEX IF NOT EXISTS idx_bot_writes_created  ON public.bot_writes(created_at);

-- Open access for bot writes
ALTER TABLE public.bot_writes DISABLE ROW LEVEL SECURITY;
