from __future__ import annotations

from datetime import date, datetime, time, timedelta

from app.models import ScheduleWindow


def _windows_for_date(
    schedules: list[ScheduleWindow],
    exceptions: list[dict],
    d: date,
    weekday: int,
) -> list[tuple[time, time]]:
    for exc in exceptions:
        if exc["date"] != d:
            continue
        if exc.get("closed"):
            return []
        opens = exc.get("opens")
        closes = exc.get("closes")
        if opens is None or closes is None:
            return []
        return [(opens, closes)]
    return [(s.opens, s.closes) for s in schedules if s.weekday == weekday]


def _effective_close(windows: list[tuple[time, time]], at_time: time) -> time | None:
    # A day can have more than one window covering the same moment (e.g. two
    # schedule rows entered by mistake, or genuinely overlapping hours). The
    # effective closing time is the LATEST close among windows that currently
    # contain `at_time` — is_open_now and closes_at both derive from this so
    # they can never disagree about which window is "the" one in effect.
    closing_times = [closes for opens, closes in windows if opens <= at_time < closes]
    return max(closing_times) if closing_times else None


def is_open_now(
    schedules: list[ScheduleWindow],
    exceptions: list[dict],
    at: datetime,
    min_remaining_minutes: int = 30,
) -> bool:
    windows = _windows_for_date(schedules, exceptions, at.date(), at.weekday())
    closes = _effective_close(windows, at.time())
    if closes is None:
        return False
    closes_dt = datetime.combine(at.date(), closes, tzinfo=at.tzinfo)
    return closes_dt - at > timedelta(minutes=min_remaining_minutes)


def closes_at(
    schedules: list[ScheduleWindow],
    exceptions: list[dict],
    at: datetime,
) -> time | None:
    windows = _windows_for_date(schedules, exceptions, at.date(), at.weekday())
    return _effective_close(windows, at.time())


def next_opening(
    schedules: list[ScheduleWindow],
    exceptions: list[dict],
    after: datetime,
    horizon_days: int = 14,
) -> tuple[date, time] | None:
    # Inclusive of the horizon's last day: offset 0..horizon_days covers
    # "today" plus horizon_days full days ahead.
    for offset in range(horizon_days + 1):
        d = after.date() + timedelta(days=offset)
        windows = _windows_for_date(schedules, exceptions, d, d.weekday())
        for opens, _closes in sorted(windows, key=lambda w: w[0]):
            candidate = datetime.combine(d, opens, tzinfo=after.tzinfo)
            if candidate > after:
                return (d, opens)
    return None
