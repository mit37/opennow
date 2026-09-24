# OpenNow

A free SMS line for Santa Clara County: text a ZIP code or cross street, get
back the 3 nearest open food, shower, and drop-in services. No app, no
account, no data plan needed. See [`OpenNow — PRD & Tech Spec`](.) for the
full product spec this implements.

Shelter placement is not handled here — SHELTER replies hand off to the
county's Here4You hotline by design.

## Status

All functional requirements in the PRD (P0, P1, and P2's FR-11) are
implemented and passing tests against a real Postgres/PostGIS instance:
parsing, geocoding, open-now/hours logic, nearest-service query,
session-based MORE paging, double opt-in food alerts, provider self-service
CLOSED TODAY texts (FR-10), an anonymous post-visit follow-up (FR-11), SMS
templates in English/Spanish/Vietnamese, and the Twilio webhook. The admin
app (FR-9) has real Supabase Auth login, row-level security, edit forms, and
an automatic audit log — see `admin/README.md`. See **Known gaps** below
before treating this as pilot-ready.

## Quick start

```bash
cp .env.example .env
# generate a Fernet key and put it in .env as ALERT_ENCRYPTION_KEY:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

docker compose up -d db          # Postgres 16 + PostGIS, auto-runs migrations/*.sql
python -m venv .venv && .venv/Scripts/activate   # or source .venv/bin/activate
pip install -e ".[dev]"

uvicorn app.main:app --reload    # serves the Twilio webhook at POST /sms
```

Point a Twilio toll-free number's messaging webhook at
`https://<your-host>/sms` (POST). In development, signature validation is
skipped automatically when `TWILIO_AUTH_TOKEN` is unset.

## Tests

```bash
docker compose up -d db          # tests needing Postgres are skipped otherwise
pytest -q
ruff check .
```

Pure-logic modules (`parser`, `hours`, `templates`) are fully tested without a
database. DB-backed tests are marked `@pytest.mark.requires_db` and connect to
`TEST_DATABASE_URL` (defaults to a local `opennow_test` database — create it
once with `createdb opennow_test` or via `psql`).

## Layout

```
app/            FastAPI webhook, router, and all query/session/geocoding logic
  main.py       Twilio webhook (POST /sms), signature validation, TwiML reply
  router.py     Orchestrates parser -> geocoder -> query_engine -> templates
  parser.py     Inbound SMS -> ParsedMessage (intents, categories, ZIP/location)
  geocoder.py   ZIP centroid + U.S. Census cross-street geocoding, with cache
  hours.py      Pure open-now / next-opening logic (weekly schedule + exceptions)
  query_engine.py  PostGIS nearest-service query + hours filtering
  session_store.py  30-minute session TTL for MORE paging and mid-session language
  templates.py  All outbound SMS copy (EN/ES/VI), segment-length budgeted
  security.py   Phone hashing, alert/follow-up number encryption, Twilio signature check
  provider.py   FR-10: registered providers texting CLOSED TODAY hide their own listing
  followup.py   FR-11: anonymous "did you make it?" follow-up, 3h after an open result
  models.py     Shared types (Language, Category, Intent, ParsedMessage, ...)
migrations/     Postgres schema (HSDS-aligned services, sessions, audit log, RLS)
jobs/           Nightly ingest diff, weekly alerts, follow-up sender (GitHub Actions)
admin/          Next.js admin app (Supabase Auth, RLS, edit/closures forms, audit log)
tests/          pytest suite (313 tests; Hypothesis property tests for hours.py)
```

## Known gaps / design notes

- **Crisis keyword handling** short-circuits to just the 988/crisis-line
  message (`app/router.py`), rather than the PRD's "at the top of the reply"
  phrasing, which implies combining it with whatever else the message asked
  for (e.g. a location query in the same text). Revisit if that combined
  behavior matters for the pilot.
- **SHELTER**'s live "nearest drop-in that opens first" only fires when the
  caller's last search location is still in-session (30 min TTL); otherwise
  it prompts the user to text their location, per FR-5's fallback.
- **FR-11 follow-up privacy tradeoff**: unlike the rest of the app,
  `followup_queue` briefly stores an *encrypted* phone number (same Fernet
  scheme as `alert_subscription`) — delivering a text 3 hours later requires
  a real number, and `phone_hash` alone can't be reversed. `app/followup.py`
  purges it the moment the text is sent (or expires after 2 days
  undelivered), so it's never retained past that single delivery attempt.
  Worth a second look if "anonymous" in the PRD is meant more strictly.
- **Ingest job** (`jobs/ingest.py`) uses a checked-in manual JSON seed
  (`jobs/seed_data/manual_listings.json`) standing in for the real Second
  Harvest data-sharing feed, which isn't in place yet per the PRD's open
  questions.
- **Admin app** (`admin/`): see `admin/README.md` for how `admin_user` rows
  are provisioned (no self-serve signup yet) and how the automatic audit-log
  triggers work.
- **Twilio client** in `jobs/alert_scheduler.py` and
  `jobs/followup_scheduler.py` must be constructed and injected by the caller
  (real `twilio.rest.Client` in production, a fake in tests) — see each
  file's `__main__` block.
- Geocoding uses the free U.S. Census onelineaddress API; the PRD's ~$210/mo
  cost estimate doesn't include any geocoding cost, but the local
  `geocode_cache` table minimizes repeat lookups either way.
