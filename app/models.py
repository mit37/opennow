from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from enum import Enum


class Language(str, Enum):
    EN = "en"
    ES = "es"
    VI = "vi"


class Category(str, Enum):
    FOOD = "food"
    PANTRY = "pantry"
    SHOWER = "shower"
    DROPIN = "dropin"
    WIFI = "wifi"
    CLOTHES = "clothes"


# Categories a user can ask for via keyword (FR-3). Default is FOOD + DROPIN.
DEFAULT_CATEGORIES = (Category.FOOD, Category.DROPIN)


class Intent(str, Enum):
    """What the router decides an inbound message is, in priority order
    (compliance keywords are checked first, then commands, then location)."""

    STOP = "stop"
    START = "start"
    HELP = "help"
    SHELTER = "shelter"
    ALERTS_OPT_IN = "alerts_opt_in"
    MORE = "more"
    LOCATION_QUERY = "location_query"
    CRISIS = "crisis"
    UNKNOWN = "unknown"


@dataclass
class ParsedMessage:
    """Output of app.parser.parse() — everything the router needs to act."""

    intent: Intent
    raw_text: str
    language_override: Language | None = None
    categories: tuple[Category, ...] = DEFAULT_CATEGORIES
    zip_code: str | None = None
    location_text: str | None = None  # cross-street / landmark text to geocode
    alert_zip: str | None = None  # for ALERTS <zip>


@dataclass
class GeocodeResult:
    lat: float
    lon: float
    ambiguous: bool = False
    in_county: bool = True


@dataclass
class ScheduleWindow:
    weekday: int  # 0 = Monday .. 6 = Sunday
    opens: time
    closes: time


@dataclass
class ServiceResult:
    service_id: str
    name: str
    category: Category
    address: str
    distance_miles: float
    closes_at: time | None  # None if not currently open (see next_opens_at)
    next_opens_at: tuple[str, time] | None = None  # (human day label, time)
    eligibility_note: str | None = None


@dataclass
class SessionState:
    phone_hash: bytes
    lang: Language
    last_query: dict = field(default_factory=dict)
