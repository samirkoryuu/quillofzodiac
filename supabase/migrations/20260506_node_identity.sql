-- UPDATE HEARTBEATS FOR MULTI-DEVICE SUPPORT
CREATE TABLE IF NOT EXISTS public.hive_heartbeats (
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    node_id TEXT NOT NULL,
    last_ping TIMESTAMPTZ DEFAULT NOW(),
    platform TEXT DEFAULT 'android',
    app_version TEXT DEFAULT '1.0.0',
    status TEXT DEFAULT 'online',
    current_speed FLOAT DEFAULT 0.0,
    PRIMARY KEY (user_id, node_id)
);

-- DISABLE RLS FOR HEARTBEATS TO ENSURE NO AUTH BLOCKERS DURING STEALTH MODE
ALTER TABLE public.hive_heartbeats DISABLE ROW LEVEL SECURITY;
