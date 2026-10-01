from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

from weekend_loop.models import (
    WEEKDAYS,
    Record,
    ScheduledRun,
    SchedulePolicy,
    WeekendWindow,
    WeeklyMoment,
    Workspace,
)

DAYS_PER_WEEK: Final[int] = 7
CRON_SHELL: Final[str] = "/bin/bash"
REPO_ENVIRONMENT_VARIABLE: Final[str] = "WEEKEND_LOOP_REPO"
EVERY_VALUE: Final[str] = "*"
FLOCK_BINARY: Final[str] = "flock"
LOCK_FILENAME: Final[str] = "cron.lock"
LOGS_DIRECTORY_NAME: Final[str] = "logs"


def cron_expression(run: ScheduledRun) -> str:
    return f"{run.minute} {run.hour} {EVERY_VALUE} {EVERY_VALUE} {run.day_of_week.value}"


def crontab_header(schedule: SchedulePolicy, repo_key: str) -> list[str]:
    return [
        f"SHELL={CRON_SHELL}",
        'MAILTO=""',
        f"CRON_TZ={schedule.timezone}",
        f"TZ={schedule.timezone}",
        f"{REPO_ENVIRONMENT_VARIABLE}={repo_key}",
    ]


def crontab_entry(run: ScheduledRun, repo_key: str, workspace: Workspace, cli_binary: Path) -> str:
    lock = workspace.state_dir / LOCK_FILENAME
    log = workspace.state_dir / LOGS_DIRECTORY_NAME / f"{repo_key}-{run.command.value}.log"
    command = (
        f"{FLOCK_BINARY} --nonblock {lock} {cli_binary} --home {workspace.root} "
        f"{run.command.value} --repo-key {repo_key}"
    )
    return f"{cron_expression(run)} {command} >> {log} 2>&1"


def render_crontab(
    schedule: SchedulePolicy, repo_key: str, workspace: Workspace, cli_binary: Path
) -> str:
    lines = [
        *crontab_header(schedule, repo_key),
        "",
        *(crontab_entry(run, repo_key, workspace, cli_binary) for run in schedule.runs),
    ]
    return "\n".join(lines) + "\n"


class WindowBounds(Record):
    opens_at: datetime
    closes_at: datetime


def local_moment(
    moment: WeeklyMoment, week_start: date, zone: ZoneInfo, weeks_later: int
) -> datetime:
    day = week_start + timedelta(
        days=WEEKDAYS.index(moment.day_of_week) + DAYS_PER_WEEK * weeks_later
    )
    return datetime.combine(day, time(moment.hour, moment.minute), tzinfo=zone)


def week_start_of(local_now: datetime) -> date:
    return local_now.date() - timedelta(days=local_now.weekday())


def next_occurrence(run: ScheduledRun, timezone: str, now: datetime) -> datetime:
    zone = ZoneInfo(timezone)
    local_now = now.astimezone(zone)
    this_week = local_moment(run, week_start_of(local_now), zone, 0)
    return (
        this_week if this_week > local_now else local_moment(run, week_start_of(local_now), zone, 1)
    )


def upcoming_runs(schedule: SchedulePolicy, now: datetime) -> list[tuple[ScheduledRun, datetime]]:
    return sorted(
        ((run, next_occurrence(run, schedule.timezone, now)) for run in schedule.runs),
        key=lambda pair: pair[1],
    )


def bounds_of_week(window: WeekendWindow, week_start: date, zone: ZoneInfo) -> WindowBounds:
    closes_next_week = window.closes.minute_of_week <= window.opens.minute_of_week
    return WindowBounds(
        opens_at=local_moment(window.opens, week_start, zone, 0).astimezone(UTC),
        closes_at=local_moment(window.closes, week_start, zone, int(closes_next_week)).astimezone(
            UTC
        ),
    )


def latest_opening(window: WeekendWindow, timezone: str, now: datetime) -> WindowBounds:
    zone = ZoneInfo(timezone)
    local_now = now.astimezone(zone)
    week_start = local_now.date() - timedelta(days=local_now.weekday())
    bounds = bounds_of_week(window, week_start, zone)
    if bounds.opens_at <= now:
        return bounds
    return bounds_of_week(window, week_start - timedelta(days=DAYS_PER_WEEK), zone)


def window_containing(window: WeekendWindow, timezone: str, now: datetime) -> WindowBounds | None:
    bounds = latest_opening(window, timezone, now)
    return bounds if now < bounds.closes_at else None


def upcoming_window(window: WeekendWindow, timezone: str, now: datetime) -> WindowBounds:
    return latest_opening(window, timezone, now + timedelta(days=DAYS_PER_WEEK))


def current_or_next_window(window: WeekendWindow, timezone: str, now: datetime) -> WindowBounds:
    containing = window_containing(window, timezone, now)
    return containing if containing is not None else upcoming_window(window, timezone, now)


def run_deadline(closes_at: datetime, weekly_reset_at: datetime | None) -> datetime:
    if weekly_reset_at is None:
        return closes_at
    return min(closes_at, weekly_reset_at)


def inside_window(schedule: SchedulePolicy, now: datetime) -> bool:
    return window_containing(schedule.window, schedule.timezone, now) is not None


def weekend_deadline(
    schedule: SchedulePolicy, weekly_reset_at: datetime | None, now: datetime
) -> datetime:
    bounds = current_or_next_window(schedule.window, schedule.timezone, now)
    return run_deadline(bounds.closes_at, weekly_reset_at)


def preparation_deadline(schedule: SchedulePolicy, now: datetime) -> datetime:
    return upcoming_window(schedule.window, schedule.timezone, now).opens_at
