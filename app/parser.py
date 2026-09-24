from __future__ import annotations

import re

from app.models import (
    DEFAULT_CATEGORIES,
    Category,
    Intent,
    Language,
    ParsedMessage,
)

_STOP_KEYWORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
_START_KEYWORDS = {"START", "UNSTOP", "YES"}
_HELP_KEYWORDS = {"HELP", "INFO"}

# Crisis phrases are matched as plain substrings (not word-boundaried) so
# minor punctuation around them ("kill myself." / "kill myself!") still hits.
_CRISIS_PHRASES = (
    "suicide",
    "kill myself",
    "hurt myself",
    "want to die",
    "end my life",
    "hurt someone",
    "kill someone",
)

_ZIP_RE = re.compile(r"(?<!\d)(\d{5})(?!\d)")
_ALERTS_RE = re.compile(r"\bALERTS\b(?:\s+(\d{5})(?!\d))?", re.IGNORECASE)
_SHELTER_RE = re.compile(r"\bSHELTER\b", re.IGNORECASE)
_MORE_RE = re.compile(r"\bMORE\b", re.IGNORECASE)

_LANG_PATTERNS: tuple[tuple[re.Pattern[str], Language], ...] = (
    (re.compile(r"\b(ESPAÑOL|ESPANOL)\b", re.IGNORECASE), Language.ES),
    (re.compile(r"\b(TIẾNG VIỆT|TIENG VIET|VIETNAMESE)\b", re.IGNORECASE), Language.VI),
    (re.compile(r"\bENGLISH\b", re.IGNORECASE), Language.EN),
)

# Order here doubles as tie-break order when several category keywords are
# present, so the resulting tuple is deterministic regardless of dict/set
# iteration order.
_CATEGORY_PATTERNS: tuple[tuple[re.Pattern[str], tuple[Category, ...]], ...] = (
    (re.compile(r"\bFOOD\b", re.IGNORECASE), (Category.FOOD, Category.PANTRY)),
    (re.compile(r"\bPANTRY\b", re.IGNORECASE), (Category.FOOD, Category.PANTRY)),
    (re.compile(r"\bSHOWER\b", re.IGNORECASE), (Category.SHOWER,)),
    (re.compile(r"\bDROP[\s-]?IN\b", re.IGNORECASE), (Category.DROPIN,)),
    (re.compile(r"\bWI[\s-]?FI\b", re.IGNORECASE), (Category.WIFI,)),
    (re.compile(r"\bCLOTHES\b", re.IGNORECASE), (Category.CLOTHES,)),
)

_DEFAULT_WITH_PANTRY: tuple[Category, ...] = tuple(
    dict.fromkeys((*DEFAULT_CATEGORIES, Category.PANTRY))
)

_ALNUM_RE = re.compile(r"[A-Za-z0-9]")


def _first_token(text: str) -> str:
    stripped = text.strip()
    if "," in stripped:
        stripped = stripped.split(",", 1)[0]
    return stripped.strip().upper()


def _strip_span(text: str, start: int, end: int) -> str:
    return text[:start] + " " + text[end:]


def parse(text: str, session_lang: Language | None = None) -> ParsedMessage:
    raw_text = text
    stripped = text.strip()
    token = _first_token(stripped)

    if token in _STOP_KEYWORDS:
        return ParsedMessage(intent=Intent.STOP, raw_text=raw_text)
    if token in _START_KEYWORDS:
        return ParsedMessage(intent=Intent.START, raw_text=raw_text)
    if token in _HELP_KEYWORDS:
        return ParsedMessage(intent=Intent.HELP, raw_text=raw_text)

    lower_text = stripped.lower()
    for phrase in _CRISIS_PHRASES:
        if phrase in lower_text:
            return ParsedMessage(intent=Intent.CRISIS, raw_text=raw_text)

    if _SHELTER_RE.search(stripped):
        return ParsedMessage(intent=Intent.SHELTER, raw_text=raw_text)

    alerts_match = _ALERTS_RE.search(stripped)
    if alerts_match:
        alert_zip = alerts_match.group(1)
        return ParsedMessage(intent=Intent.ALERTS_OPT_IN, raw_text=raw_text, alert_zip=alert_zip)

    if _MORE_RE.search(stripped):
        return ParsedMessage(intent=Intent.MORE, raw_text=raw_text)

    remainder = stripped
    language_override: Language | None = None
    for pattern, lang in _LANG_PATTERNS:
        match = pattern.search(remainder)
        if match:
            language_override = lang
            remainder = _strip_span(remainder, match.start(), match.end())
            break

    matched_categories: list[Category] = []
    for pattern, cats in _CATEGORY_PATTERNS:
        match = pattern.search(remainder)
        if match:
            for category in cats:
                if category not in matched_categories:
                    matched_categories.append(category)
            remainder = _strip_span(remainder, match.start(), match.end())

    resolved_categories = tuple(matched_categories) if matched_categories else _DEFAULT_WITH_PANTRY

    zip_match = _ZIP_RE.search(remainder)
    zip_code: str | None = None
    if zip_match:
        zip_code = zip_match.group(1)
        remainder = _strip_span(remainder, zip_match.start(), zip_match.end())

    leftover = re.sub(r"\s+", " ", remainder).strip(" ,.")
    has_usable_text = bool(_ALNUM_RE.search(leftover))
    location_text = leftover if (zip_code is None and has_usable_text) else None

    if zip_code is None and location_text is None:
        return ParsedMessage(
            intent=Intent.UNKNOWN,
            raw_text=raw_text,
            language_override=language_override,
        )

    return ParsedMessage(
        intent=Intent.LOCATION_QUERY,
        raw_text=raw_text,
        language_override=language_override,
        categories=resolved_categories,
        zip_code=zip_code,
        location_text=location_text,
    )
