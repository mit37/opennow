from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import get_settings
from tests.conftest import DATABASE_URL, requires_db


@pytest.fixture(autouse=True)
def _settings_for_app(monkeypatch):
    # app.main's lifespan opens a pool against DATABASE_URL (app.config), which
    # is the dev/docker-compose database, not TEST_DATABASE_URL used elsewhere.
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("ALERT_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@requires_db
def test_healthz_smoke(db_pool):
    # app.main's lifespan opens a DB pool at startup regardless of the route
    # hit, so even this endpoint needs Postgres reachable to boot the app.
    from app.main import app

    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@requires_db
def test_sms_webhook_round_trip_returns_twiml(db_pool):
    from app.main import app

    with TestClient(app) as client:
        response = client.post("/sms", data={"From": "+14085551212", "Body": "HELP"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert "<Message>" in response.text
    assert "ZIP" in response.text


@requires_db
def test_sms_webhook_rejects_bad_signature(monkeypatch, db_pool):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "a-real-auth-token")
    get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as client:
        response = client.post(
            "/sms",
            data={"From": "+14085551212", "Body": "HELP"},
            headers={"X-Twilio-Signature": "not-a-valid-signature"},
        )
    assert response.status_code == 403
