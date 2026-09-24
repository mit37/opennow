# OpenNow Admin

Staff web admin for editing service listings, hours and closures (PRD FR-9).
Run it with `npm install && npm run dev` from this directory (Node 18+),
after copying `.env.local.example` to `.env.local` and filling in
`NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, and
`SUPABASE_SERVICE_ROLE_KEY` (server-only — never expose this one to the
browser) from the Supabase project settings.

## Access control

`migrations/005_admin_rls.sql` adds:

- **`admin_user`**: one row per person allowed into this app, `id` referencing
  Supabase's `auth.users(id)`. `organization_id IS NULL` means staff/
  superadmin (sees and edits every organization's listings);
  `organization_id` set to a specific org scopes that person to a provider
  role that can only see and edit that organization's own `location`,
  `service`, `schedule`, and `schedule_exception` rows and their own
  `audit_log` entries. There is no self-serve signup — an `admin_user` row
  must be inserted manually (Supabase dashboard's SQL editor, or `psql`)
  after the person's Supabase Auth account exists, e.g.:

  ```sql
  insert into admin_user (id, organization_id, role)
  values ('<auth.users.id>', null, 'staff');
  ```

- **Row-level security** on `organization`, `location`, `service`,
  `schedule`, `schedule_exception`, and `audit_log`, keyed off `auth.uid()`
  via two helper functions (`is_staff_admin()`, `admin_org_id()`). The admin
  app's Supabase client calls (`supabase.from(...)`) run as the signed-in
  user, so these policies are what actually decide who can read or write
  which rows — the app code does not re-implement that scoping.

- **Automatic audit logging**: triggers on `service`, `schedule`, and
  `schedule_exception` write a row to `audit_log` on every insert, update,
  and delete, with `diff` holding `{"old": ..., "new": ...}` (via
  `to_jsonb`). This happens in Postgres regardless of which admin UI path
  made the change, so the app never calls `INSERT INTO audit_log` itself —
  the edit and closures pages just write to the underlying tables and the
  log entry appears on its own. `admin_id` is nullable: writes that happen
  outside a Supabase session (currently just `app/provider.py`'s FR-10
  "CLOSED TODAY" SMS flow, which updates `schedule_exception` over a direct
  Postgres connection) are logged with a `NULL` admin_id, shown as "System"
  in the audit log page.

- Migration 005 also creates a minimal `auth` schema/`auth.users`
  table/`auth.uid()` function and the `anon`/`authenticated`/`service_role`
  roles **only if they don't already exist**, so `docker compose up -d db`
  (a plain Postgres/PostGIS image, not Supabase) can still apply it for
  local development and `pytest`. On a real Supabase project these already
  exist and the migration is a no-op there.

## Auth flow

- `app/login/page.tsx` is a client component calling
  `supabase.auth.signInWithPassword`, redirecting to `/listings` on success.
- `lib/supabaseClient.ts` uses `createClientComponentClient` from
  `@supabase/auth-helpers-nextjs` (not a plain `createClient`), so the
  session is written to cookies and not just to `localStorage` — that's
  what lets `middleware.ts` and the server components below see it.
- `middleware.ts` refreshes the session cookie on every request and
  redirects unauthenticated requests to `/listings`, `/listings/*`,
  `/closures`, and `/audit` to `/login`.
- Listings, the edit form, closures, and the audit log all read and write
  through the signed-in user's own Supabase client, so row-level security
  applies uniformly no matter which page is used.

## Known gaps

- No self-serve `admin_user` provisioning UI — see above.
- No password reset / invite flow; use Supabase Auth's dashboard for that
  until FR-9 grows one.
- The edit form replaces a service's `schedule` rows with delete-then-insert
  on save rather than a diffed update; simple and correct for this table's
  size, but not a single atomic transaction from the client's point of view
  (each Supabase call is checked for errors in order and reported).
