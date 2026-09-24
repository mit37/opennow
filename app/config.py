from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"

    database_url: str = "postgresql://opennow:opennow@localhost:5432/opennow"

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

    # HMAC-SHA256 key used to hash phone numbers everywhere except alert
    # subscribers (rotated yearly; rotation invalidates old session/event
    # hashes by design, which is fine since sessions are 30-minute TTL).
    phone_hash_secret: str = "change-me-in-production"

    # Symmetric key (Fernet) used to encrypt alert_subscription.phone_e164_encrypted.
    # In production this is a KMS-backed key, not an env var.
    alert_encryption_key: str = ""

    census_geocoder_base_url: str = "https://geocoding.geo.census.gov/geocoder"

    sentry_dsn: str = ""

    # county bounding box (Santa Clara County), used to reject out-of-area geocodes
    county_min_lat: float = 36.89
    county_max_lat: float = 37.47
    county_min_lon: float = -122.20
    county_max_lon: float = -121.20

    session_ttl_minutes: int = 30
    stale_listing_days: int = 14
    max_results: int = 3
    default_search_radius_miles: float = 3.0
    min_remaining_open_minutes: int = 30

    here4you_phone: str = "(408) 385-2400"
    crisis_line_phone: str = "800-704-0900"
    suicide_lifeline: str = "988"
    # Here4You staffed hours, local time (America/Los_Angeles). Outside this
    # window SHELTER replies add the 24/7 crisis line per FR-5.
    here4you_hours_start: int = 7
    here4you_hours_end: int = 23


@lru_cache
def get_settings() -> Settings:
    return Settings()
