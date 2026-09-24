import { createClientComponentClient } from '@supabase/auth-helpers-nextjs';

// A plain `createClient` from '@supabase/supabase-js' would keep its session
// in localStorage only, which the SSR-side clients below can't see. This one
// also writes the session to cookies, so middleware.ts and server components
// (createServerComponentClient) observe the same signed-in session right
// after login.
export const supabase = createClientComponentClient();
