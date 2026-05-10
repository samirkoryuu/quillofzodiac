-- APPSUBA MIGRATION: The Supabase Realtime Task Queue
-- Run this in your AppSuba (Main Supabase) SQL Editor

-- 1. Create the Task Queue Table
CREATE TABLE IF NOT EXISTS public.task_queue (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    url           TEXT NOT NULL,
    wait_selector TEXT DEFAULT 'body',
    status        TEXT DEFAULT 'pending', -- pending, active, completed, failed
    assigned_to   UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    result        JSONB,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Enable Realtime for this table
-- This allows the FINALAPP to get instant "PUSH" notifications for new tasks
ALTER TABLE public.task_queue REPLICA IDENTITY FULL;

BEGIN;
  -- Add to the realtime publication
  -- (If the publication doesn't exist, this might fail, but usually Supabase has 'supabase_realtime')
  DROP PUBLICATION IF EXISTS supabase_realtime;
  CREATE PUBLICATION supabase_realtime FOR TABLE public.task_queue;
COMMIT;

-- 3. Security (RLS)
ALTER TABLE public.task_queue ENABLE ROW LEVEL SECURITY;

-- Allow nodes (users) to see tasks assigned specifically to them
CREATE POLICY "Nodes can view assigned tasks" 
ON public.task_queue FOR SELECT 
USING (auth.uid() = assigned_to);

-- Allow nodes to update the status and result of their assigned tasks
CREATE POLICY "Nodes can update assigned tasks" 
ON public.task_queue FOR UPDATE 
USING (auth.uid() = assigned_to);

-- Allow the Service Key (Hub/Bot) to do everything
-- (Service role bypasses RLS by default, but we can be explicit)
CREATE POLICY "Service role full access" 
ON public.task_queue FOR ALL 
USING (auth.role() = 'service_role');

-- 4. Function to auto-update updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_task_queue_updated_at
    BEFORE UPDATE ON public.task_queue
    FOR EACH ROW
    EXECUTE PROCEDURE update_updated_at_column();

-- 5. Indexes for performance
CREATE INDEX IF NOT EXISTS idx_task_queue_status ON public.task_queue(status);
CREATE INDEX IF NOT EXISTS idx_task_queue_assigned ON public.task_queue(assigned_to);
