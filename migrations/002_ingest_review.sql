-- Human-review queue for the nightly ingest diff job. Ingest never writes
-- directly to service/location (see jobs/ingest.py) — new or changed
-- candidates land here for an admin to approve via the admin app.

CREATE TABLE ingest_review (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    kind text NOT NULL CHECK (kind IN ('new', 'changed')),
    payload jsonb NOT NULL,
    reviewed boolean NOT NULL DEFAULT false
);

CREATE INDEX ingest_review_reviewed_idx ON ingest_review(reviewed);
