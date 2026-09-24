-- OpenNow initial schema
-- Postgres 16 + PostGIS. HSDS-aligned service data; minimal, hashed user-side state.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── Services and where they happen (HSDS-aligned) ──────────────────────────

CREATE TABLE organization (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    phone text,
    website text
);

CREATE TABLE location (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid REFERENCES organization(id) ON DELETE CASCADE,
    name text,
    address text NOT NULL,
    geom geography(Point, 4326) NOT NULL
);

CREATE INDEX location_geom_idx ON location USING GIST (geom);

CREATE TABLE service (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    location_id uuid REFERENCES location(id) ON DELETE CASCADE,
    name text NOT NULL,
    category text NOT NULL CHECK (category IN ('food', 'pantry', 'shower', 'dropin', 'wifi', 'clothes')),
    eligibility_note text,          -- e.g. 'families only', 'ID not required'
    languages text[] NOT NULL DEFAULT ARRAY['en'],
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'hidden', 'retired')),
    last_verified_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX service_category_idx ON service(category);
CREATE INDEX service_status_idx ON service(status);
CREATE INDEX service_last_verified_idx ON service(last_verified_at);

CREATE TABLE schedule (             -- recurring weekly hours
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    service_id uuid NOT NULL REFERENCES service(id) ON DELETE CASCADE,
    weekday smallint NOT NULL CHECK (weekday BETWEEN 0 AND 6),  -- 0 = Monday
    opens time NOT NULL,
    closes time NOT NULL
);

CREATE INDEX schedule_service_weekday_idx ON schedule(service_id, weekday);

CREATE TABLE schedule_exception (   -- holidays, one-day closures, pop-ups
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    service_id uuid NOT NULL REFERENCES service(id) ON DELETE CASCADE,
    date date NOT NULL,
    closed boolean NOT NULL,
    opens time,
    closes time,
    reason text
);

CREATE UNIQUE INDEX schedule_exception_service_date_idx ON schedule_exception(service_id, date);

-- ── Geocoding support ───────────────────────────────────────────────────────

CREATE TABLE zip_centroid (          -- U.S. Census ZCTA centroids, one-time/yearly load
    zip char(5) PRIMARY KEY,
    geom geography(Point, 4326) NOT NULL
);

CREATE TABLE geocode_cache (         -- cross-street/landmark -> lat/lng, avoids re-hitting Census API
    query_text text PRIMARY KEY,     -- normalized (lowercased, trimmed) input text
    geom geography(Point, 4326),     -- null if lookup was ambiguous/failed
    resolved_at timestamptz NOT NULL DEFAULT now()
);

-- ── Minimal user-side state ─────────────────────────────────────────────────

CREATE TABLE session (               -- 30-minute TTL, then deleted
    phone_hash bytea PRIMARY KEY,
    lang text NOT NULL DEFAULT 'en' CHECK (lang IN ('en', 'es', 'vi')),
    last_query jsonb,
    expires_at timestamptz NOT NULL
);

CREATE INDEX session_expires_idx ON session(expires_at);

CREATE TABLE alert_subscription (    -- only stored with double opt-in
    phone_e164_encrypted bytea NOT NULL,
    phone_hash bytea PRIMARY KEY,
    zip char(5) NOT NULL,
    lang text NOT NULL DEFAULT 'en' CHECK (lang IN ('en', 'es', 'vi')),
    opted_in_at timestamptz NOT NULL DEFAULT now(),
    opted_out_at timestamptz
);

CREATE INDEX alert_subscription_zip_idx ON alert_subscription(zip) WHERE opted_out_at IS NULL;

CREATE TABLE event_log (             -- analytics only; no raw numbers or message text
    id bigserial PRIMARY KEY,
    ts timestamptz NOT NULL DEFAULT now(),
    phone_hash bytea NOT NULL,
    command text,
    zip char(5),
    results_count smallint,
    latency_ms int
);

CREATE INDEX event_log_ts_idx ON event_log(ts);

CREATE TABLE audit_log (             -- every admin edit
    id bigserial PRIMARY KEY,
    ts timestamptz NOT NULL DEFAULT now(),
    admin_id uuid NOT NULL,
    table_name text NOT NULL,
    row_id uuid NOT NULL,
    diff jsonb NOT NULL
);
