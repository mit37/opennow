-- FR-9: admin auth roles, row-level security, and automatic audit logging
-- for the Next.js admin app. Runs against the same Postgres instance as the
-- Supabase project (staff sign in via Supabase Auth), so admin_user.id can
-- reference auth.users(id) and policies can key off auth.uid().
--
-- ── Local/dev compatibility shim ────────────────────────────────────────────
-- `docker compose up -d db` (see docker-compose.yml, README) runs this file
-- against a plain postgis/postgis image that has no Supabase `auth` schema
-- and no anon/authenticated/service_role roles. Everything below is written
-- to also work there: this block creates minimal stand-ins ONLY if they are
-- missing, so it is a no-op on a real Supabase project (which already has
-- all of this) and keeps `docker compose up -d db` / pytest working locally.
-- The FastAPI backend connects as the `opennow` role, which owns every table
-- here (it ran 001-004 via docker-entrypoint-initdb.d), so table ownership
-- already exempts it from RLS regardless of this shim.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'auth') THEN
        CREATE SCHEMA auth;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS auth.users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid()
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'auth' AND p.proname = 'uid'
    ) THEN
        EXECUTE $ddl$
            CREATE FUNCTION auth.uid() RETURNS uuid
            LANGUAGE sql STABLE
            AS 'SELECT NULLIF(current_setting(''request.jwt.claim.sub'', true), '''')::uuid'
        $ddl$;
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        CREATE ROLE anon NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        CREATE ROLE service_role NOLOGIN;
    END IF;
END
$$;

-- ── admin_user: who can sign in to the admin app, and their scope ──────────

CREATE TABLE admin_user (
    id uuid PRIMARY KEY REFERENCES auth.users(id),
    organization_id uuid NULL REFERENCES organization(id),
    role text NOT NULL CHECK (role IN ('staff', 'provider')),
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Never exposed to PostgREST directly (no policy would help a client that
-- lacks table privileges in the first place) — only readable through the
-- SECURITY DEFINER helper functions below, so a signed-in provider can never
-- enumerate other admins or organizations by querying this table.
REVOKE ALL ON admin_user FROM PUBLIC;
REVOKE ALL ON admin_user FROM anon;
REVOKE ALL ON admin_user FROM authenticated;

-- ── audit_log adjustments ────────────────────────────────────────────────
-- admin_id becomes nullable: writes to service/schedule/schedule_exception
-- also happen outside the admin app (e.g. app/provider.py's FR-10 "CLOSED
-- TODAY" SMS flow, run over a plain asyncpg connection with no Supabase
-- session), so auth.uid() is NULL for those and the row is logged with a
-- NULL admin_id, meaning "system-initiated". organization_id is added so a
-- provider's SELECT policy can scope audit_log without re-joining back to a
-- row that an AFTER DELETE trigger has already removed.

ALTER TABLE audit_log ALTER COLUMN admin_id DROP NOT NULL;
ALTER TABLE audit_log ADD COLUMN organization_id uuid NULL REFERENCES organization(id);

-- ── Role helpers, keyed off auth.uid() ──────────────────────────────────────

CREATE OR REPLACE FUNCTION is_staff_admin() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (
        SELECT 1 FROM admin_user
        WHERE id = auth.uid() AND role = 'staff' AND organization_id IS NULL
    );
$$;

CREATE OR REPLACE FUNCTION admin_org_id() RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT organization_id FROM admin_user WHERE id = auth.uid();
$$;

-- ── Row-level security ───────────────────────────────────────────────────

ALTER TABLE organization ENABLE ROW LEVEL SECURITY;
ALTER TABLE location ENABLE ROW LEVEL SECURITY;
ALTER TABLE service ENABLE ROW LEVEL SECURITY;
ALTER TABLE schedule ENABLE ROW LEVEL SECURITY;
ALTER TABLE schedule_exception ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY organization_staff_all ON organization FOR ALL
    USING (is_staff_admin()) WITH CHECK (is_staff_admin());

CREATE POLICY organization_provider_own ON organization FOR ALL
    USING (id = admin_org_id()) WITH CHECK (id = admin_org_id());

CREATE POLICY location_staff_all ON location FOR ALL
    USING (is_staff_admin()) WITH CHECK (is_staff_admin());

CREATE POLICY location_provider_own ON location FOR ALL
    USING (organization_id = admin_org_id()) WITH CHECK (organization_id = admin_org_id());

CREATE POLICY service_staff_all ON service FOR ALL
    USING (is_staff_admin()) WITH CHECK (is_staff_admin());

CREATE POLICY service_provider_own ON service FOR ALL
    USING (
        EXISTS (
            SELECT 1 FROM location l
            WHERE l.id = service.location_id AND l.organization_id = admin_org_id()
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM location l
            WHERE l.id = service.location_id AND l.organization_id = admin_org_id()
        )
    );

CREATE POLICY schedule_staff_all ON schedule FOR ALL
    USING (is_staff_admin()) WITH CHECK (is_staff_admin());

CREATE POLICY schedule_provider_own ON schedule FOR ALL
    USING (
        EXISTS (
            SELECT 1 FROM service s JOIN location l ON l.id = s.location_id
            WHERE s.id = schedule.service_id AND l.organization_id = admin_org_id()
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM service s JOIN location l ON l.id = s.location_id
            WHERE s.id = schedule.service_id AND l.organization_id = admin_org_id()
        )
    );

CREATE POLICY schedule_exception_staff_all ON schedule_exception FOR ALL
    USING (is_staff_admin()) WITH CHECK (is_staff_admin());

CREATE POLICY schedule_exception_provider_own ON schedule_exception FOR ALL
    USING (
        EXISTS (
            SELECT 1 FROM service s JOIN location l ON l.id = s.location_id
            WHERE s.id = schedule_exception.service_id AND l.organization_id = admin_org_id()
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM service s JOIN location l ON l.id = s.location_id
            WHERE s.id = schedule_exception.service_id AND l.organization_id = admin_org_id()
        )
    );

-- SELECT-only: nothing but the SECURITY DEFINER trigger below may write here.
CREATE POLICY audit_log_staff_select ON audit_log FOR SELECT
    USING (is_staff_admin());

CREATE POLICY audit_log_provider_select ON audit_log FOR SELECT
    USING (organization_id = admin_org_id());

-- ── Automatic audit logging ──────────────────────────────────────────────
-- SECURITY DEFINER so the INSERT below always succeeds regardless of the
-- calling role's own RLS grants on audit_log (there are none for INSERT).

CREATE OR REPLACE FUNCTION fn_write_audit_log() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    v_row_id uuid;
    v_org_id uuid;
    v_diff jsonb;
BEGIN
    v_row_id := COALESCE(NEW.id, OLD.id);
    v_diff := jsonb_build_object(
        'old', CASE WHEN OLD IS NULL THEN NULL ELSE to_jsonb(OLD) END,
        'new', CASE WHEN NEW IS NULL THEN NULL ELSE to_jsonb(NEW) END
    );

    IF TG_TABLE_NAME = 'service' THEN
        SELECT l.organization_id INTO v_org_id
        FROM location l
        WHERE l.id = COALESCE(NEW.location_id, OLD.location_id);
    ELSIF TG_TABLE_NAME IN ('schedule', 'schedule_exception') THEN
        SELECT l.organization_id INTO v_org_id
        FROM service s JOIN location l ON l.id = s.location_id
        WHERE s.id = COALESCE(NEW.service_id, OLD.service_id);
    END IF;

    INSERT INTO audit_log (admin_id, table_name, row_id, diff, organization_id)
    VALUES (auth.uid(), TG_TABLE_NAME, v_row_id, v_diff, v_org_id);

    RETURN COALESCE(NEW, OLD);
END;
$$;

CREATE TRIGGER service_audit
    AFTER INSERT OR UPDATE OR DELETE ON service
    FOR EACH ROW EXECUTE FUNCTION fn_write_audit_log();

CREATE TRIGGER schedule_audit
    AFTER INSERT OR UPDATE OR DELETE ON schedule
    FOR EACH ROW EXECUTE FUNCTION fn_write_audit_log();

CREATE TRIGGER schedule_exception_audit
    AFTER INSERT OR UPDATE OR DELETE ON schedule_exception
    FOR EACH ROW EXECUTE FUNCTION fn_write_audit_log();

-- ── Table privileges for the admin app's Supabase roles ─────────────────
-- RLS above still governs which rows are visible; these grants are what
-- let PostgREST attempt the query as `authenticated` at all.

GRANT SELECT, INSERT, UPDATE, DELETE ON organization, location, service, schedule, schedule_exception TO authenticated;
GRANT SELECT ON audit_log TO authenticated;
