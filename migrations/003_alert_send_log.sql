-- Records each weekly food alert sent, so jobs/alert_scheduler.py can cap
-- sends at 3 per subscriber per trailing 7 days (FR-8).

CREATE TABLE alert_send_log (
    id bigserial PRIMARY KEY,
    phone_hash bytea NOT NULL,
    sent_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX alert_send_log_phone_hash_sent_at_idx ON alert_send_log(phone_hash, sent_at);
