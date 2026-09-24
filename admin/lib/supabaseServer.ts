import { createClient } from '@supabase/supabase-js';

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL!;
const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY!;

// Uses the service-role key, which bypasses row-level security entirely.
// Only call this from server actions or route handlers — never from a
// client component, and never send this client's key to the browser.
// Not used for audit logging: every edit to service/schedule/schedule_exception
// is logged automatically by a Postgres trigger (see migrations/005_admin_rls.sql),
// regardless of which client made the write. Reserved for elevated
// server-side operations that RLS should not gate at all, e.g. an
// out-of-band script provisioning admin_user rows.
export function supabaseServiceRole() {
  return createClient(supabaseUrl, serviceRoleKey, {
    auth: { autoRefreshToken: false, persistSession: false },
  });
}
