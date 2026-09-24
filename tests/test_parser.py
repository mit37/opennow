from __future__ import annotations

import pytest

from app.models import Category, Intent, Language
from app.parser import parse

# ---------------------------------------------------------------------------
# 1. Compliance keywords
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["STOP", "stop", "StOp", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "quit"],
)
def test_stop_family_whole_message(text: str) -> None:
    result = parse(text)
    assert result.intent == Intent.STOP
    assert result.raw_text == text


def test_stop_comma_separated_first_token() -> None:
    result = parse("STOP, please don't text me again")
    assert result.intent == Intent.STOP


def test_stop_lowercase_comma_separated() -> None:
    result = parse("cancel, thanks")
    assert result.intent == Intent.STOP


def test_stop_not_matched_when_not_whole_message_or_first_token() -> None:
    result = parse("please stop texting me")
    assert result.intent != Intent.STOP


@pytest.mark.parametrize("text", ["START", "start", "UNSTOP", "unstop", "YES", "yes", "Yes"])
def test_start_family(text: str) -> None:
    result = parse(text)
    assert result.intent == Intent.START


def test_start_comma_separated() -> None:
    result = parse("START, resume texts")
    assert result.intent == Intent.START


@pytest.mark.parametrize("text", ["HELP", "help", "INFO", "info", "Help"])
def test_help_family(text: str) -> None:
    result = parse(text)
    assert result.intent == Intent.HELP


def test_help_comma_separated() -> None:
    result = parse("HELP, what can you do")
    assert result.intent == Intent.HELP


# ---------------------------------------------------------------------------
# 2. Crisis keywords
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "I am thinking about suicide",
        "SUICIDE",
        "I want to kill myself tonight",
        "Kill Myself",
        "please help me, I want to hurt myself",
        "I want to die, I want to die",
        "sometimes I want to die and cant cope",
        "I am ready to end my life",
        "he said he wants to hurt someone at work",
        "she wants to kill someone tomorrow",
    ],
)
def test_crisis_keywords_detected_anywhere_case_insensitive(text: str) -> None:
    result = parse(text)
    assert result.intent == Intent.CRISIS


def test_crisis_takes_priority_over_location_query_content() -> None:
    result = parse("food near 95112, I want to kill myself")
    assert result.intent == Intent.CRISIS


def test_crisis_does_not_override_exact_compliance_keyword() -> None:
    # "STOP" alone is checked first per priority order.
    result = parse("STOP")
    assert result.intent == Intent.STOP


# ---------------------------------------------------------------------------
# 3. SHELTER keyword
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["SHELTER", "shelter", "I need a Shelter tonight please"])
def test_shelter_keyword(text: str) -> None:
    result = parse(text)
    assert result.intent == Intent.SHELTER


def test_shelter_not_matched_as_substring_of_other_word() -> None:
    result = parse("sheltering from the rain near 95112")
    assert result.intent != Intent.SHELTER


# ---------------------------------------------------------------------------
# 4. ALERTS opt-in
# ---------------------------------------------------------------------------


def test_alerts_with_valid_zip() -> None:
    result = parse("ALERTS 95112")
    assert result.intent == Intent.ALERTS_OPT_IN
    assert result.alert_zip == "95112"


def test_alerts_lowercase_with_valid_zip() -> None:
    result = parse("alerts 95050")
    assert result.intent == Intent.ALERTS_OPT_IN
    assert result.alert_zip == "95050"


def test_alerts_with_no_zip() -> None:
    result = parse("ALERTS")
    assert result.intent == Intent.ALERTS_OPT_IN
    assert result.alert_zip is None


def test_alerts_with_bad_zip_too_short() -> None:
    result = parse("ALERTS 951")
    assert result.intent == Intent.ALERTS_OPT_IN
    assert result.alert_zip is None


def test_alerts_with_bad_zip_non_numeric() -> None:
    result = parse("ALERTS please")
    assert result.intent == Intent.ALERTS_OPT_IN
    assert result.alert_zip is None


def test_alerts_with_too_many_digits_does_not_yield_zip() -> None:
    result = parse("ALERTS 9511234")
    assert result.intent == Intent.ALERTS_OPT_IN
    assert result.alert_zip is None


# ---------------------------------------------------------------------------
# 5. MORE
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["MORE", "more", "can I get MORE options"])
def test_more_keyword(text: str) -> None:
    result = parse(text)
    assert result.intent == Intent.MORE


# ---------------------------------------------------------------------------
# 6. Language switch keywords
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["ESPAÑOL", "espanol", "ESPANOL", "Español"])
def test_language_switch_spanish_alone(text: str) -> None:
    result = parse(text)
    assert result.language_override == Language.ES
    assert result.intent == Intent.UNKNOWN


@pytest.mark.parametrize("text", ["TIẾNG VIỆT", "TIENG VIET", "tieng viet", "VIETNAMESE", "vietnamese"])
def test_language_switch_vietnamese_alone(text: str) -> None:
    result = parse(text)
    assert result.language_override == Language.VI
    assert result.intent == Intent.UNKNOWN


def test_language_switch_english_alone() -> None:
    result = parse("ENGLISH")
    assert result.language_override == Language.EN
    assert result.intent == Intent.UNKNOWN


def test_language_switch_plus_zip_does_not_short_circuit() -> None:
    result = parse("ESPAÑOL 95112")
    assert result.intent == Intent.LOCATION_QUERY
    assert result.language_override == Language.ES
    assert result.zip_code == "95112"
    assert result.location_text is None


def test_language_switch_plus_zip_lowercase() -> None:
    result = parse("espanol 95112")
    assert result.intent == Intent.LOCATION_QUERY
    assert result.language_override == Language.ES
    assert result.zip_code == "95112"


def test_language_switch_plus_location_text() -> None:
    result = parse("VIETNAMESE 1st and Santa Clara")
    assert result.intent == Intent.LOCATION_QUERY
    assert result.language_override == Language.VI
    assert result.zip_code is None
    assert result.location_text is not None
    assert "1st" in result.location_text
    assert "Santa Clara" in result.location_text


def test_language_switch_plus_category_plus_zip() -> None:
    result = parse("ESPAÑOL FOOD 95112")
    assert result.intent == Intent.LOCATION_QUERY
    assert result.language_override == Language.ES
    assert result.zip_code == "95112"
    assert set(result.categories) == {Category.FOOD, Category.PANTRY}


# ---------------------------------------------------------------------------
# 7. Location queries: categories
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["FOOD 95112", "food 95112", "PANTRY 95112", "pantry 95112"])
def test_food_and_pantry_category(text: str) -> None:
    result = parse(text)
    assert result.intent == Intent.LOCATION_QUERY
    assert set(result.categories) == {Category.FOOD, Category.PANTRY}
    assert result.zip_code == "95112"


def test_shower_category() -> None:
    result = parse("SHOWER 95112")
    assert set(result.categories) == {Category.SHOWER}
    assert result.zip_code == "95112"


@pytest.mark.parametrize("text", ["DROPIN 95112", "DROP-IN 95112", "DROP IN 95112", "drop in 95112"])
def test_dropin_category_variants(text: str) -> None:
    result = parse(text)
    assert set(result.categories) == {Category.DROPIN}
    assert result.zip_code == "95112"


@pytest.mark.parametrize("text", ["WIFI 95112", "WI-FI 95112", "wifi 95112"])
def test_wifi_category_variants(text: str) -> None:
    result = parse(text)
    assert set(result.categories) == {Category.WIFI}
    assert result.zip_code == "95112"


def test_clothes_category() -> None:
    result = parse("CLOTHES 95112")
    assert set(result.categories) == {Category.CLOTHES}
    assert result.zip_code == "95112"


def test_multiple_categories_combined() -> None:
    result = parse("food and shower near 95112")
    assert set(result.categories) == {Category.FOOD, Category.PANTRY, Category.SHOWER}


def test_default_categories_when_none_present() -> None:
    result = parse("95112")
    assert set(result.categories) == {Category.FOOD, Category.PANTRY, Category.DROPIN}


def test_default_categories_with_location_text() -> None:
    result = parse("1st and Santa Clara")
    assert set(result.categories) == {Category.FOOD, Category.PANTRY, Category.DROPIN}
    assert result.zip_code is None
    assert result.location_text == "1st and Santa Clara"


# ---------------------------------------------------------------------------
# 8. Category keyword + zip together (explicit combined test)
# ---------------------------------------------------------------------------


def test_category_keyword_and_zip_together() -> None:
    result = parse("SHOWER 95112")
    assert result.intent == Intent.LOCATION_QUERY
    assert set(result.categories) == {Category.SHOWER}
    assert result.zip_code == "95112"
    assert result.location_text is None


# ---------------------------------------------------------------------------
# 9. ZIP code vs location text mutual exclusivity
# ---------------------------------------------------------------------------


def test_zip_code_sets_only_zip_not_location_text() -> None:
    result = parse("95112")
    assert result.zip_code == "95112"
    assert result.location_text is None


def test_location_text_sets_only_location_not_zip() -> None:
    result = parse("corner of 1st and Santa Clara")
    assert result.zip_code is None
    assert result.location_text is not None


def test_cross_street_location_query() -> None:
    result = parse("1st and Santa Clara")
    assert result.intent == Intent.LOCATION_QUERY
    assert result.zip_code is None
    assert result.location_text == "1st and Santa Clara"


# ---------------------------------------------------------------------------
# 10. Mixed case across the board
# ---------------------------------------------------------------------------


def test_mixed_case_stop() -> None:
    assert parse("StOpAlL").intent == Intent.STOP


def test_mixed_case_category_and_zip() -> None:
    result = parse("FoOd 95112")
    assert set(result.categories) == {Category.FOOD, Category.PANTRY}
    assert result.zip_code == "95112"


def test_mixed_case_shelter() -> None:
    assert parse("ShElTeR").intent == Intent.SHELTER


# ---------------------------------------------------------------------------
# 11. Gibberish / empty -> UNKNOWN
# ---------------------------------------------------------------------------


def test_empty_message_is_unknown() -> None:
    result = parse("")
    assert result.intent == Intent.UNKNOWN
    assert result.zip_code is None
    assert result.location_text is None


def test_whitespace_only_is_unknown() -> None:
    result = parse("    ")
    assert result.intent == Intent.UNKNOWN


def test_pure_punctuation_gibberish_is_unknown() -> None:
    result = parse("??? !!! ...")
    assert result.intent == Intent.UNKNOWN
    assert result.zip_code is None
    assert result.location_text is None


def test_category_keyword_alone_with_nothing_else_is_unknown() -> None:
    result = parse("FOOD")
    assert result.intent == Intent.UNKNOWN


# ---------------------------------------------------------------------------
# 12. raw_text is preserved verbatim
# ---------------------------------------------------------------------------


def test_raw_text_preserved_exactly() -> None:
    original = "  Food 95112  "
    result = parse(original)
    assert result.raw_text == original


# ---------------------------------------------------------------------------
# 13. session_lang parameter accepted (parser does not need to use it beyond
# accepting it in the signature; explicit language keywords still override).
# ---------------------------------------------------------------------------


def test_session_lang_accepted_without_error() -> None:
    result = parse("95112", session_lang=Language.ES)
    assert result.intent == Intent.LOCATION_QUERY
    assert result.zip_code == "95112"


def test_explicit_language_keyword_sets_override_even_with_session_lang() -> None:
    result = parse("ENGLISH 95112", session_lang=Language.VI)
    assert result.language_override == Language.EN
    assert result.zip_code == "95112"
