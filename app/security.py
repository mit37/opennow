from __future__ import annotations

import hashlib
import hmac

from cryptography.fernet import Fernet
from twilio.request_validator import RequestValidator

from app.config import get_settings


def hash_phone(phone_e164: str) -> bytes:
    """HMAC-SHA256 of an E.164 phone number, keyed by the yearly-rotated secret.

    Used everywhere a phone number needs to be correlated (sessions, event
    logs, alert dedup) without storing the number itself.
    """
    secret = get_settings().phone_hash_secret.encode()
    return hmac.new(secret, phone_e164.encode(), hashlib.sha256).digest()


def _fernet() -> Fernet:
    key = get_settings().alert_encryption_key
    if not key:
        raise RuntimeError("ALERT_ENCRYPTION_KEY is not configured")
    return Fernet(key.encode())


def encrypt_phone(phone_e164: str) -> bytes:
    """Encrypt a phone number for alert_subscription storage.

    Alerts require a real, dialable number, so this is the one place a raw
    number is kept — encrypted at rest with a KMS-backed key in production.
    """
    return _fernet().encrypt(phone_e164.encode())


def decrypt_phone(token: bytes) -> str:
    return _fernet().decrypt(token).decode()


def validate_twilio_signature(url: str, params: dict, signature: str) -> bool:
    """Verify the X-Twilio-Signature header on an inbound webhook request."""
    settings = get_settings()
    if settings.env == "development" and not settings.twilio_auth_token:
        return True
    validator = RequestValidator(settings.twilio_auth_token)
    return validator.validate(url, params, signature)
