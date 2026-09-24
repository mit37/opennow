-- FR-10: registered providers can text CLOSED TODAY to hide their own listing.

CREATE TABLE provider_registration (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    service_id uuid NOT NULL REFERENCES service(id) ON DELETE CASCADE,
    phone_hash bytea NOT NULL UNIQUE,
    registered_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE provider_closure_log (   -- "admin notified" trail for FR-10
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    service_id uuid NOT NULL REFERENCES service(id) ON DELETE CASCADE,
    closed_for date NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
