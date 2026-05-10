-- MOBILE APP SYNC SCHEMA
-- Ensures Supabase has the tables needed to display Discord-side stats

-- 1. Writer Stats (Webnovel focused)
CREATE TABLE IF NOT EXISTS public.writer_stats (
    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    current_book_name TEXT DEFAULT 'Untitled Grimoire',
    latest_chapter_count INTEGER DEFAULT 0,
    latest_word_count INTEGER DEFAULT 0,
    books_count INTEGER DEFAULT 0,
    tasks_completed INTEGER DEFAULT 0,
    last_synced_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Writer Economy (Discord/Bot focused)
CREATE TABLE IF NOT EXISTS public.writer_economy (
    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    honey_count INTEGER DEFAULT 0,
    cloud_marks INTEGER DEFAULT 0,
    current_level INTEGER DEFAULT 0,
    current_xp INTEGER DEFAULT 0,
    last_synced_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. Profile Enhancements (Titles/Roles)
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS writer_title TEXT DEFAULT 'INITIATE';
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS official_role TEXT DEFAULT 'NODE';
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS nickname_color TEXT DEFAULT '#FFFFFF';

-- 4. RPC Helpers for Hub Rewards
CREATE OR REPLACE FUNCTION increment_tasks_completed(user_id_input UUID)
RETURNS VOID AS $$
BEGIN
    UPDATE public.writer_stats
    SET tasks_completed = tasks_completed + 1
    WHERE user_id = user_id_input;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE OR REPLACE FUNCTION add_cloud_marks(user_id_input UUID, amount INTEGER)
RETURNS VOID AS $$
BEGIN
    UPDATE public.writer_economy
    SET cloud_marks = cloud_marks + amount
    WHERE user_id = user_id_input;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 5. RLS (Enable read for all, update for auth)
ALTER TABLE public.writer_stats ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.writer_economy ENABLE ROW LEVEL SECURITY;

CREATE POLICY "stats_viewable" ON public.writer_stats FOR SELECT USING (true);
CREATE POLICY "economy_viewable" ON public.writer_economy FOR SELECT USING (true);

-- Ensure Hub can update these (Hub uses Service Key, so RLS usually doesn't apply, but good to have)
