-- PHASE 3: RECENT FINDINGS (LIVE BUFFER)
CREATE TABLE IF NOT EXISTS public.recent_findings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    penname TEXT NOT NULL,
    country TEXT,
    book TEXT NOT NULL,
    book_id TEXT,
    genre TEXT,
    collections INTEGER DEFAULT 0,
    chapters INTEGER DEFAULT 0,
    views TEXT, -- Webnovel sometimes uses '1.2k' format
    power_ranking INTEGER, -- Only stored if Top 200
    task_id TEXT UNIQUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- DISABLE RLS FOR HIGH-SPEED HUB ACCESS
ALTER TABLE public.recent_findings DISABLE ROW LEVEL SECURITY;

-- INDEX FOR SWEEPER PERFORMANCE
CREATE INDEX IF NOT EXISTS idx_recent_findings_created_at ON public.recent_findings(created_at);
