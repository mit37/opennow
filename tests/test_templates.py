from __future__ import annotations

import re
from datetime import date, time, timedelta

import pytest

from app.models import Category, Language, ServiceResult
from app.templates import (
    MAX_RESULTS_BY_LANG,
    SEGMENT_CHAR_BUDGET,
    format_12h,
    render_alerts_confirm_prompt,
    render_alerts_confirmed,
    render_ambiguous_location,
    render_crisis_prefix,
    render_generic_error,
    render_help,
    render_no_results,
    render_results,
    render_shelter,
    render_start_ack,
    render_stop_ack,
)

ALL_LANGS = list(Language)

# A ~40-char service name and ~35-char address, the longest realistic inputs
# the PRD's worst case calls for.
LONG_NAME = "Sacred Heart Community Service Center"  # 38 chars
assert 35 <= len(LONG_NAME) <= 42
LONG_ADDR = "1381 South First Street, San Jose, CA"  # 38 chars
assert 30 <= len(LONG_ADDR) <= 40

BARE_24H_RE = re.compile(r"\b(1[3-9]|2[0-3]):[0-5]\d")

_SOLICITATION_SNIPPETS = [
    "your name",
    "what is your name",
    "how old are you",
    "your age",
    "tell us your story",
    "your story",
    "what's your name",
]


def _realistic_results(n: int) -> list[ServiceResult]:
    base = [
        ServiceResult(
            service_id="1",
            name=LONG_NAME,
            category=Category.PANTRY,
            address=LONG_ADDR,
            distance_miles=2.34,
            closes_at=time(21, 30),
        ),
        ServiceResult(
            service_id="2",
            name="Bill Wilson Center",
            category=Category.SHOWER,
            address="3490 The Alameda",
            distance_miles=1.1,
            closes_at=time(16, 0),
        ),
        ServiceResult(
            service_id="3",
            name="Sunday Friends Foundation",
            category=Category.DROPIN,
            address="1922 The Alameda St",
            distance_miles=3.05,
            closes_at=time(17, 15),
        ),
        ServiceResult(
            service_id="4",
            name="Overflow Extra Service Name Co",
            category=Category.FOOD,
            address="100 Overflow Ave, San Jose, CA",
            distance_miles=4.4,
            closes_at=time(20, 0),
        ),
    ]
    return base[:n]


def _assert_budget(lang: Language, body: str) -> None:
    assert isinstance(body, str)
    assert len(body) > 0
    assert len(body) <= SEGMENT_CHAR_BUDGET[lang], (
        f"{lang}: {len(body)} chars > budget {SEGMENT_CHAR_BUDGET[lang]}\n{body!r}"
    )


def _assert_no_bare_24h(body: str) -> None:
    match = BARE_24H_RE.search(body)
    assert match is None, f"found bare 24h time {match.group() if match else ''} in {body!r}"


class TestConstants:
    def test_max_results_by_lang(self):
        assert MAX_RESULTS_BY_LANG == {Language.EN: 3, Language.ES: 2, Language.VI: 2}

    def test_segment_char_budget(self):
        assert SEGMENT_CHAR_BUDGET == {Language.EN: 306, Language.ES: 134, Language.VI: 134}


class TestFormat12h:
    @pytest.mark.parametrize(
        "t,expected",
        [
            (time(16, 0), "4pm"),
            (time(9, 30), "9:30am"),
            (time(0, 0), "12am"),
            (time(12, 0), "12pm"),
            (time(12, 30), "12:30pm"),
            (time(23, 15), "11:15pm"),
            (time(1, 5), "1:05am"),
        ],
    )
    def test_known_values(self, t, expected):
        assert format_12h(t) == expected

    def test_never_emits_bare_24h(self):
        for hour in range(24):
            for minute in (0, 15, 30, 45):
                out = format_12h(time(hour, minute))
                assert BARE_24H_RE.search(out) is None
                assert out.endswith("am") or out.endswith("pm")


class TestRenderResults:
    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_fits_budget_with_max_results(self, lang):
        results = _realistic_results(MAX_RESULTS_BY_LANG[lang])
        body = render_results(results, lang, "Winchester & Stevens Creek")
        _assert_budget(lang, body)

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_fits_budget_with_short_zip_label(self, lang):
        results = _realistic_results(MAX_RESULTS_BY_LANG[lang])
        body = render_results(results, lang, "95112")
        _assert_budget(lang, body)

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_defensively_truncates_extra_results(self, lang):
        results = _realistic_results(4)
        body = render_results(results, lang, "95112")
        _assert_budget(lang, body)
        numbered_lines = [line for line in body.splitlines() if re.match(r"^\d+\.", line)]
        assert len(numbered_lines) == MAX_RESULTS_BY_LANG[lang]

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_no_bare_24h_times(self, lang):
        results = _realistic_results(MAX_RESULTS_BY_LANG[lang])
        body = render_results(results, lang, "Winchester & Stevens Creek")
        _assert_no_bare_24h(body)

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_never_solicits_name_age_or_story(self, lang):
        results = _realistic_results(MAX_RESULTS_BY_LANG[lang])
        body = render_results(results, lang, "95112").lower()
        for snippet in _SOLICITATION_SNIPPETS:
            assert snippet not in body

    def test_english_offers_reply_options(self):
        results = _realistic_results(3)
        body = render_results(results, Language.EN, "95112")
        assert "Reply MORE, SHOWER, or SHELTER." in body

    def test_english_header_and_line_shape(self):
        results = _realistic_results(1)
        body = render_results(results, Language.EN, "95112")
        lines = body.splitlines()
        assert lines[0] == "Open now near 95112:"
        assert lines[1].startswith("1. ")
        assert ", closes " in lines[1]
        assert lines[1].rstrip().endswith("mi")

    def test_single_result_fits(self):
        for lang in ALL_LANGS:
            body = render_results(_realistic_results(1), lang, "95112")
            _assert_budget(lang, body)


class TestRenderNoResults:
    def _openings(self, today: date | None = None):
        today = today or date.today()
        return [
            (LONG_NAME, today, time(9, 30)),
            ("Bill Wilson Center", today + timedelta(days=1), time(16, 0)),
        ]

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_fits_budget(self, lang):
        body = render_no_results(self._openings(), lang, "Winchester & Stevens Creek")
        _assert_budget(lang, body)

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_fits_budget_with_short_zip(self, lang):
        body = render_no_results(self._openings(), lang, "95112")
        _assert_budget(lang, body)

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_no_bare_24h_times(self, lang):
        body = render_no_results(self._openings(), lang, "95112")
        _assert_no_bare_24h(body)

    def test_uses_today_tomorrow_labels(self):
        body = render_no_results(self._openings(), Language.EN, "95112")
        assert "today" in body
        assert "tomorrow" in body

    def test_weekday_label_further_out(self):
        today = date.today()
        far = today + timedelta(days=3)
        body = render_no_results([(LONG_NAME, far, time(9, 0))], Language.EN, "95112")
        weekday_names = (
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        )
        assert any(name in body for name in weekday_names)

    def test_caps_at_two_openings(self):
        today = date.today()
        openings = [
            ("A Service", today, time(9, 0)),
            ("B Service", today, time(10, 0)),
            ("C Service", today, time(11, 0)),
        ]
        body = render_no_results(openings, Language.EN, "95112")
        assert body.count("Next:") == 2

    def test_mentions_shelter_keyword(self):
        for lang in ALL_LANGS:
            body = render_no_results(self._openings(), lang, "95112")
            assert "SHELTER" in body


class TestRenderShelter:
    @pytest.mark.parametrize("lang", ALL_LANGS)
    @pytest.mark.parametrize("in_hours", [True, False])
    def test_fits_budget(self, lang, in_hours):
        body = render_shelter(in_hours, lang)
        _assert_budget(lang, body)

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_always_includes_here4you_number(self, lang):
        for in_hours in (True, False):
            body = render_shelter(in_hours, lang)
            assert "385-2400" in body

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_off_hours_adds_crisis_line(self, lang):
        on_hours_body = render_shelter(True, lang)
        off_hours_body = render_shelter(False, lang)
        assert "704-0900" in off_hours_body
        assert "704-0900" not in on_hours_body


class TestSimpleTemplates:
    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_render_help(self, lang):
        body = render_help(lang)
        _assert_budget(lang, body)
        assert "SHELTER" in body
        assert "ALERTS" in body
        assert "STOP" in body

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_render_stop_ack(self, lang):
        body = render_stop_ack(lang)
        _assert_budget(lang, body)
        assert "START" in body

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_render_start_ack(self, lang):
        body = render_start_ack(lang)
        _assert_budget(lang, body)
        assert "STOP" in body

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_render_alerts_confirm_prompt(self, lang):
        body = render_alerts_confirm_prompt("95112", lang)
        _assert_budget(lang, body)
        assert "95112" in body
        assert "STOP" in body

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_render_alerts_confirmed(self, lang):
        body = render_alerts_confirmed(lang)
        _assert_budget(lang, body)
        assert "STOP" in body

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_render_ambiguous_location(self, lang):
        body = render_ambiguous_location(lang)
        _assert_budget(lang, body)

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_render_crisis_prefix(self, lang):
        body = render_crisis_prefix(lang)
        _assert_budget(lang, body)
        assert "988" in body
        assert "704-0900" in body

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_render_generic_error(self, lang):
        body = render_generic_error(lang)
        _assert_budget(lang, body)


class TestNoSolicitationAcrossAllTemplates:
    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_no_personal_questions_anywhere(self, lang):
        bodies = [
            render_results(_realistic_results(MAX_RESULTS_BY_LANG[lang]), lang, "95112"),
            render_no_results(
                [(LONG_NAME, date.today(), time(9, 0))], lang, "95112"
            ),
            render_shelter(True, lang),
            render_shelter(False, lang),
            render_help(lang),
            render_stop_ack(lang),
            render_start_ack(lang),
            render_alerts_confirm_prompt("95112", lang),
            render_alerts_confirmed(lang),
            render_ambiguous_location(lang),
            render_crisis_prefix(lang),
            render_generic_error(lang),
        ]
        for body in bodies:
            lowered = body.lower()
            for snippet in _SOLICITATION_SNIPPETS:
                assert snippet not in lowered, f"{snippet!r} found in {body!r}"
