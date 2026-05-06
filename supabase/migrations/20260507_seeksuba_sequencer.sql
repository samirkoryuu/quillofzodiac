-- SEEKSUBA MIGRATION: Central Sequencer Tables
-- Run this in the SeekSuba SQL Editor

-- 1. Owner Sequences Table (permanent mapping: external_id → integer_id)
CREATE TABLE IF NOT EXISTS public.owner_sequences (
    external_id  TEXT PRIMARY KEY,          -- Webnovel profile_id, Discord user_id, etc.
    integer_id   BIGINT NOT NULL UNIQUE,    -- The assigned sequential ID
    owner_type   TEXT DEFAULT 'unknown',    -- 'writer' | 'discord_user' | 'node'
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Global Counter Table (single row, atomically incremented)
CREATE TABLE IF NOT EXISTS public.global_counter (
    id    INTEGER PRIMARY KEY DEFAULT 1,
    value BIGINT  DEFAULT 0
);

-- Insert the starting counter row
INSERT INTO public.global_counter (id, value)
VALUES (1, 0)
ON CONFLICT (id) DO NOTHING;

-- 3. Atomic Increment RPC Function
-- Called by sequencer.py to safely get the next ID without race conditions
CREATE OR REPLACE FUNCTION increment_owner_counter()
RETURNS BIGINT AS $$
DECLARE
    next_val BIGINT;
BEGIN
    UPDATE public.global_counter
    SET value = value + 1
    WHERE id = 1
    RETURNING value INTO next_val;
    RETURN next_val;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 4. Indexes for fast lookups
CREATE INDEX IF NOT EXISTS idx_owner_sequences_type
    ON public.owner_sequences(owner_type);
CREATE INDEX IF NOT EXISTS idx_owner_sequences_integer_id
    ON public.owner_sequences(integer_id);

-- 5. Disable RLS for Hub access
ALTER TABLE public.owner_sequences DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.global_counter DISABLE ROW LEVEL SECURITY;
