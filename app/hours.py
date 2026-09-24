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


def is_open_now(
    schedules: list[ScheduleWindow],
    exceptions: list[dict],
    at: datetime,
    min_remaining_minutes: int = 30,
) -> bool:
    windows = _windows_for_date(schedules, exceptions, at.date(), at.weekday())
    at_time = at.time()
    for opens, closes in windows:
        if opens <= at_time < closes:
            closes_dt = datetime.combine(at.date(), closes, tzinfo=at.tzinfo)
            if closes_dt - at > timedelta(minutes=min_remaining_minutes):
                return True
    return False


def closes_at(
    schedules: list[ScheduleWindow],
    exceptions: list[dict],
    at: datetime,
) -> time | None:
    windows = _windows_for_date(schedules, exceptions, at.date(), at.weekday())
    at_time = at.time()
    for opens, closes in windows:
        if opens <= at_time < closes:
            return closes
    return None


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
