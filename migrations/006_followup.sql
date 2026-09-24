-- FR-11 (P2): anonymous follow-up to measure impact. 3 hours after a reply
-- with at least one open result, we queue an optional "Did you make it?
-- Y/N" text (app/followup.py decides eligibility and jobs/followup_scheduler.py
-- sends it). Off by default for alert subscribers, capped at one scheduled
-- per phone number per trailing 7 days.
--
-- Delivering the text requires a real number, so — same as
-- alert_subscription.phone_e164_encrypted — we keep one encrypted copy
-- alongside the hash. app/followup.py.send_due nulls it out the moment the
-- text goes out (success or permanent expiry), so it's never retained longer
-- than the ~3-hour scheduling window plus one scheduler tick.

CREATE TABLE followup_queue (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    phone_hash bytea NOT NULL,
    phone_e164_encrypted bytea,  -- required until sent_at is set, then purged (see CHECK below)
    service_name text NOT NULL,
    lang text NOT NULL DEFAULT 'en' CHECK (lang IN ('en', 'es', 'vi')),
    scheduled_at timestamptz NOT NULL,
    sent_at timestamptz,
    delivered boolean NOT NULL DEFAULT false,  -- false + sent_at set = expired undelivered
    response text CHECK (response IN ('yes', 'no')),
    responded_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT followup_queue_phone_present_until_resolved
        CHECK (sent_at IS NOT NULL OR phone_e164_encrypted IS NOT NULL)
);

-- Weekly-cap check in app.followup.maybe_schedule (one scheduled per phone
-- number per trailing 7 days).
CREATE INDEX followup_queue_phone_hash_created_at_idx ON followup_queue(phone_hash, created_at);

-- "What's due" query in app.followup.send_due (sent_at IS NULL AND
-- scheduled_at <= now()).
CREATE INDEX followup_queue_sent_at_scheduled_at_idx ON followup_queue(sent_at, scheduled_at);
