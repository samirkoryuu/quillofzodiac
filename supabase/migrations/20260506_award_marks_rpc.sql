-- PHASE 3: CLOUD MARKS REWARD FUNCTION
CREATE OR REPLACE FUNCTION award_cloud_marks(u_id UUID, amount INTEGER)
RETURNS VOID AS $$
BEGIN
    INSERT INTO public.user_profiles (id, cloud_marks)
    VALUES (u_id, amount)
    ON CONFLICT (id) DO UPDATE SET
    cloud_marks = public.user_profiles.cloud_marks + EXCLUDED.cloud_marks;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
