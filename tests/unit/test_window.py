from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from weekend_loop.models import (
    ScheduledCommand,
    ScheduledRun,
    SchedulePolicy,
    Weekday,
    WeekendWindow,
    WeeklyMoment,
)
from weekend_loop.schedule import (
    current_or_next_window,
    preparation_deadline,
    run_deadline,
    upcoming_window,
    weekend_deadline,
    window_containing,
)

WARSAW = "Europe/Warsaw"
WEEKEND = WeekendWindow(
    opens=WeeklyMoment(day_of_week=Weekday.FRIDAY, hour=18, minute=0),
    closes=WeeklyMoment(day_of_week=Weekday.SUNDAY, hour=23, minute=59),
)
FRIDAY_EVENING = datetime(2026, 9, 18, 16, 0, tzinfo=UTC)
SUNDAY_CLOSE = datetime(2026, 9, 20, 21, 59, tzinfo=UTC)


def schedule_with(runs: list[ScheduledRun]) -> SchedulePolicy:
    return SchedulePolicy(repo_key="demo", timezone=WARSAW, window=WEEKEND, runs=runs)


def test_a_moment_inside_the_weekend_belongs_to_that_window() -> None:
    bounds = window_containing(WEEKEND, WARSAW, datetime(2026, 9, 19, 12, 0, tzinfo=UTC))
    assert bounds is not None
    assert bounds.opens_at == FRIDAY_EVENING
    assert bounds.closes_at == SUNDAY_CLOSE


def test_the_window_opens_and_closes_on_the_minute() -> None:
    assert window_containing(WEEKEND, WARSAW, FRIDAY_EVENING) is not None
    assert window_containing(WEEKEND, WARSAW, FRIDAY_EVENING - timedelta(minutes=1)) is None
    assert window_containing(WEEKEND, WARSAW, SUNDAY_CLOSE) is None


def test_a_weekday_looks_ahead_to_the_coming_weekend() -> None:
    wednesday = datetime(2026, 9, 16, 9, 0, tzinfo=UTC)
    assert window_containing(WEEKEND, WARSAW, wednesday) is None
    assert current_or_next_window(WEEKEND, WARSAW, wednesday).opens_at == FRIDAY_EVENING
    assert upcoming_window(WEEKEND, WARSAW, wednesday).closes_at == SUNDAY_CLOSE


def test_the_window_follows_the_autumn_clock_change() -> None:
    bounds = window_containing(WEEKEND, WARSAW, datetime(2026, 10, 25, 12, 0, tzinfo=UTC))
    assert bounds is not None
    assert bounds.opens_at == datetime(2026, 10, 23, 16, 0, tzinfo=UTC)
    assert bounds.closes_at == datetime(2026, 10, 25, 22, 59, tzinfo=UTC)


def test_a_window_that_closes_where_it_opens_spans_the_week() -> None:
    monday = WeeklyMoment(day_of_week=Weekday.MONDAY, hour=0, minute=0)
    week = WeekendWindow(opens=monday, closes=monday)
    bounds = window_containing(week, WARSAW, datetime(2026, 9, 16, 9, 0, tzinfo=UTC))
    assert bounds is not None
    assert bounds.closes_at - bounds.opens_at == timedelta(days=7)


def test_the_deadline_is_the_earlier_of_the_close_and_the_recorded_reset() -> None:
    assert run_deadline(SUNDAY_CLOSE, None) == SUNDAY_CLOSE
    earlier = SUNDAY_CLOSE - timedelta(hours=5)
    assert run_deadline(SUNDAY_CLOSE, earlier) == earlier
    schedule = schedule_with([ScheduledRun(day_of_week=Weekday.FRIDAY, hour=21, minute=0)])
    saturday = datetime(2026, 9, 19, 8, 0, tzinfo=UTC)
    assert weekend_deadline(schedule, None, saturday) == SUNDAY_CLOSE


def test_preparation_ends_when_the_next_weekend_opens() -> None:
    schedule = schedule_with([ScheduledRun(day_of_week=Weekday.FRIDAY, hour=21, minute=0)])
    thursday = datetime(2026, 9, 17, 18, 0, tzinfo=UTC)
    assert preparation_deadline(schedule, thursday) == FRIDAY_EVENING


def test_a_weekend_run_scheduled_outside_the_window_is_rejected() -> None:
    with pytest.raises(ValidationError, match="outside schedule.window"):
        schedule_with([ScheduledRun(day_of_week=Weekday.MONDAY, hour=9, minute=0)])
    prepare = ScheduledRun(
        day_of_week=Weekday.THURSDAY, hour=20, minute=0, command=ScheduledCommand.PREPARE
    )
    assert schedule_with([prepare]).runs == [prepare]
