from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from hypothesis import given, settings
from hypothesis import strategies as st

from app.hours import closes_at, is_open_now, next_opening
from app.models import ScheduleWindow

LA = ZoneInfo("America/Los_Angeles")


def _dt(y: int, m: int, d: int, h: int, mi: int = 0, s: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, s, tzinfo=LA)


MON_FRI_9_5 = [ScheduleWindow(weekday=w, opens=time(9, 0), closes=time(17, 0)) for w in range(5)]


def test_open_during_weekday_business_hours():
    at = _dt(2026, 1, 5, 12, 0)  # Monday noon
    assert is_open_now(MON_FRI_9_5, [], at) is True
    assert closes_at(MON_FRI_9_5, [], at) == time(17, 0)


def test_closed_before_opening():
    at = _dt(2026, 1, 5, 8, 59)  # Monday, one minute before opening
    assert is_open_now(MON_FRI_9_5, [], at) is False
    assert closes_at(MON_FRI_9_5, [], at) is None


def test_open_exactly_at_opening_time():
    at = _dt(2026, 1, 5, 9, 0)
    assert is_open_now(MON_FRI_9_5, [], at) is True
    assert closes_at(MON_FRI_9_5, [], at) == time(17, 0)


def test_closed_exactly_at_closing_time():
    at = _dt(2026, 1, 5, 17, 0)
    assert is_open_now(MON_FRI_9_5, [], at) is False
    assert closes_at(MON_FRI_9_5, [], at) is None


def test_closed_on_weekend():
    at = _dt(2026, 1, 3, 12, 0)  # Saturday
    assert is_open_now(MON_FRI_9_5, [], at) is False
    assert closes_at(MON_FRI_9_5, [], at) is None


def test_boundary_exactly_30_minutes_remaining_is_closed():
    at = _dt(2026, 1, 5, 16, 30)  # 30 minutes before 17:00 close
    assert is_open_now(MON_FRI_9_5, [], at, min_remaining_minutes=30) is False
    assert closes_at(MON_FRI_9_5, [], at) == time(17, 0)


def test_boundary_31_minutes_remaining_is_open():
    at = _dt(2026, 1, 5, 16, 29)  # 31 minutes before 17:00 close
    assert is_open_now(MON_FRI_9_5, [], at, min_remaining_minutes=30) is True


def test_exception_closes_a_normally_open_day():
    exceptions = [
        {"date": date(2026, 1, 5), "closed": True, "opens": None, "closes": None, "reason": "Holiday"}
    ]
    at = _dt(2026, 1, 5, 12, 0)
    assert is_open_now(MON_FRI_9_5, exceptions, at) is False
    assert closes_at(MON_FRI_9_5, exceptions, at) is None


def test_exception_opens_a_normally_closed_day_with_custom_hours():
    exceptions = [
        {
            "date": date(2026, 1, 3),  # Saturday, normally closed
            "closed": False,
            "opens": time(10, 0),
            "closes": time(14, 0),
            "reason": "Special Saturday hours",
        }
    ]
    before = _dt(2026, 1, 3, 9, 59)
    during = _dt(2026, 1, 3, 12, 0)
    after_close = _dt(2026, 1, 3, 14, 0)
    assert is_open_now(MON_FRI_9_5, exceptions, before) is False
    assert is_open_now(MON_FRI_9_5, exceptions, during) is True
    assert closes_at(MON_FRI_9_5, exceptions, during) == time(14, 0)
    assert is_open_now(MON_FRI_9_5, exceptions, after_close) is False


def test_exception_only_applies_to_its_own_date():
    exceptions = [
        {"date": date(2026, 1, 5), "closed": True, "opens": None, "closes": None, "reason": "Holiday"}
    ]
    at = _dt(2026, 1, 6, 12, 0)  # Tuesday, unaffected
    assert is_open_now(MON_FRI_9_5, exceptions, at) is True


def test_next_opening_same_day():
    at = _dt(2026, 1, 5, 7, 0)
    assert next_opening(MON_FRI_9_5, [], at) == (date(2026, 1, 5), time(9, 0))


def test_next_opening_crosses_weekend():
    at = _dt(2026, 1, 2, 18, 0)  # Friday evening, after close
    assert next_opening(MON_FRI_9_5, [], at) == (date(2026, 1, 5), time(9, 0))  # next Monday


def test_next_opening_skips_closed_exception_day():
    exceptions = [
        {"date": date(2026, 1, 5), "closed": True, "opens": None, "closes": None, "reason": "Holiday"}
    ]
    at = _dt(2026, 1, 2, 18, 0)  # Friday evening
    assert next_opening(MON_FRI_9_5, exceptions, at) == (date(2026, 1, 6), time(9, 0))  # Tuesday


def test_next_opening_uses_exception_override_hours():
    exceptions = [
        {
            "date": date(2026, 1, 3),
            "closed": False,
            "opens": time(10, 0),
            "closes": time(14, 0),
            "reason": "Special Saturday hours",
        }
    ]
    at = _dt(2026, 1, 2, 18, 0)  # Friday evening
    assert next_opening(MON_FRI_9_5, exceptions, at) == (date(2026, 1, 3), time(10, 0))


def test_next_opening_horizon_exhausted_returns_none():
    at = _dt(2026, 1, 1, 0, 0)
    exceptions = [
        {
            "date": date(2026, 1, 25),  # 24 days out, beyond a 14-day horizon
            "closed": False,
            "opens": time(9, 0),
            "closes": time(17, 0),
            "reason": "One-off opening",
        }
    ]
    assert next_opening([], exceptions, at, horizon_days=14) is None
    assert next_opening([], exceptions, at, horizon_days=24) == (date(2026, 1, 25), time(9, 0))


def test_next_opening_no_hours_at_all_returns_none():
    at = _dt(2026, 1, 1, 0, 0)
    assert next_opening([], [], at, horizon_days=14) is None


def test_dst_spring_forward_wall_clock_hours_on_transition_day():
    # 2026-03-08 is the America/Los_Angeles spring-forward date (02:00 -> 03:00 PDT).
    sunday_schedule = [ScheduleWindow(weekday=6, opens=time(9, 0), closes=time(17, 0))]
    before_open = _dt(2026, 3, 8, 1, 0)
    during = _dt(2026, 3, 8, 10, 0)
    near_close = _dt(2026, 3, 8, 16, 30)
    after_close = _dt(2026, 3, 8, 17, 0)
    assert is_open_now(sunday_schedule, [], before_open) is False
    assert is_open_now(sunday_schedule, [], during) is True
    assert closes_at(sunday_schedule, [], during) == time(17, 0)
    assert is_open_now(sunday_schedule, [], near_close, min_remaining_minutes=30) is False
    assert is_open_now(sunday_schedule, [], after_close) is False
    assert next_opening(sunday_schedule, [], before_open) == (date(2026, 3, 8), time(9, 0))


def test_dst_spring_forward_next_opening_crosses_transition():
    at = _dt(2026, 3, 6, 18, 0)  # Friday evening, before the Sunday DST switch
    assert next_opening(MON_FRI_9_5, [], at) == (date(2026, 3, 9), time(9, 0))  # Monday after DST


def test_dst_fall_back_wall_clock_hours_on_transition_day():
    # 2026-11-01 is the America/Los_Angeles fall-back date (02:00 PDT -> 01:00 PST).
    sunday_schedule = [ScheduleWindow(weekday=6, opens=time(9, 0), closes=time(17, 0))]
    before_open = _dt(2026, 11, 1, 1, 0)
    during = _dt(2026, 11, 1, 10, 0)
    near_close = _dt(2026, 11, 1, 16, 30)
    after_close = _dt(2026, 11, 1, 17, 0)
    assert is_open_now(sunday_schedule, [], before_open) is False
    assert is_open_now(sunday_schedule, [], during) is True
    assert closes_at(sunday_schedule, [], during) == time(17, 0)
    assert is_open_now(sunday_schedule, [], near_close, min_remaining_minutes=30) is False
    assert is_open_now(sunday_schedule, [], after_close) is False
    assert next_opening(sunday_schedule, [], before_open) == (date(2026, 11, 1), time(9, 0))


def test_dst_fall_back_next_opening_crosses_transition():
    at = _dt(2026, 10, 30, 18, 0)  # Friday evening, before the Sunday DST switch
    assert next_opening(MON_FRI_9_5, [], at) == (date(2026, 11, 2), time(9, 0))  # Monday after DST


@st.composite
def schedule_windows(draw, max_count: int = 5) -> list[ScheduleWindow]:
    n = draw(st.integers(min_value=0, max_value=max_count))
    windows: list[ScheduleWindow] = []
    for _ in range(n):
        weekday = draw(st.integers(min_value=0, max_value=6))
        a = draw(st.integers(min_value=0, max_value=1439))
        b = draw(st.integers(min_value=0, max_value=1439))
        if a == b:
            continue
        lo, hi = sorted((a, b))
        windows.append(
            ScheduleWindow(
                weekday=weekday,
                opens=time(lo // 60, lo % 60),
                closes=time(hi // 60, hi % 60),
            )
        )
    return windows


@st.composite
def la_datetimes(draw, start_year: int = 2024, end_year: int = 2027) -> datetime:
    year = draw(st.integers(min_value=start_year, max_value=end_year))
    month = draw(st.integers(min_value=1, max_value=12))
    max_day = calendar.monthrange(year, month)[1]
    day = draw(st.integers(min_value=1, max_value=max_day))
    hour = draw(st.integers(min_value=0, max_value=23))
    minute = draw(st.integers(min_value=0, max_value=59))
    second = draw(st.integers(min_value=0, max_value=59))
    return datetime(year, month, day, hour, minute, second, tzinfo=LA)


@given(schedules=schedule_windows(), at=la_datetimes())
@settings(max_examples=200)
def test_property_open_now_and_next_opening_invariants(schedules, at):
    exceptions: list[dict] = []
    min_remaining = 30

    open_now = is_open_now(schedules, exceptions, at, min_remaining_minutes=min_remaining)
    ca = closes_at(schedules, exceptions, at)

    if open_now:
        assert ca is not None
        closes_dt = datetime.combine(at.date(), ca, tzinfo=at.tzinfo)
        assert closes_dt - at >= timedelta(minutes=min_remaining)
    elif schedules:
        nxt = next_opening(schedules, exceptions, at, horizon_days=14)
        assert nxt is not None
        nxt_dt = datetime.combine(nxt[0], nxt[1], tzinfo=at.tzinfo)
        assert nxt_dt > at
