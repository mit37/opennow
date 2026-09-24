import { createClient } from '@supabase/supabase-js';

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL!;
const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY!;

// Uses the service-role key, which bypasses row-level security entirely.
// Only call this from server actions or route handlers — never from a
// client component, and never send this client's key to the browser.
// Primary use case: writing audit_log rows on every edit, since the acting
// admin's own row-level policy (their own organization's rows) should not
// also gate whether the edit gets logged.
export function supabaseServiceRole() {
  return createClient(supabaseUrl, serviceRoleKey, {
    auth: { autoRefreshToken: false, persistSession: false },
  });
}
