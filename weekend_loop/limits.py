from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Final

from weekend_loop.claude_cli import CallWatch, ClaudeInvocation, run_claude
from weekend_loop.models import (
    ClaudeResult,
    LimitRejection,
    Policy,
    Record,
    StopReason,
    UsagePolicy,
    UsageReading,
    UsageWindow,
)

SECONDS_PER_MINUTE: Final[int] = 60
FALLBACK_PARK_SECONDS: Final[int] = 60
RESET_MARGIN_SECONDS: Final[int] = 1
FULL_UTILIZATION: Final[float] = 1.0
FIVE_HOUR_WINDOW: Final[str] = "five_hour"
PROBE_MODEL: Final[str] = "haiku"
PROBE_EFFORT: Final[str] = "low"
PROBE_PROMPT: Final[str] = "Reply with the single word: ok"
PROBE_SYSTEM_PROMPT: Final[str] = "Answer in one word and use no tool."
PROBE_TOOLS: Final[list[str]] = []
PROBE_PERMISSION_MODE: Final[str] = "plan"
PROBE_OUTPUT_FORMAT: Final[str] = "stream-json"
PERCENT: Final[int] = 100
NO_READING_REASON: Final[str] = "no allowance reading; the envelope is the only cap"
HEADROOM_REASON: Final[str] = "the allowance has room for another task"
OVERAGE_REASON: Final[str] = (
    "usage ceiling: the allowance is spent and further work bills usage credits"
)
SEVEN_DAY_REASON: Final[str] = (
    "usage ceiling: the seven-day window is {spent} and one more task needs {estimate}, "
    "which passes {ceiling}"
)
PARK_REASON: Final[str] = "the five-hour window is {spent}; waiting {minutes} minutes for its reset"
FIVE_HOUR_REASON: Final[str] = (
    "usage ceiling: the five-hour window is {spent} and it resets at {resume_at:%a %H:%M} UTC, "
    "after the run's deadline"
)
WEEKLY_REJECTION_REASON: Final[str] = (
    "usage ceiling: the CLI refused the call on its {window} limit"
)


def percentage(fraction: float) -> str:
    return f"{round(fraction * PERCENT)}%"


class UsageAction(StrEnum):
    PROCEED = "proceed"
    PARK = "park"
    STOP = "stop"


class UsageDecision(Record):
    action: UsageAction
    reason: str
    resume_at: datetime | None
    stop_reason: StopReason | None


def live_window(window: UsageWindow, now: datetime) -> UsageWindow | None:
    return window if now < window.resets_at else None


def overage_decision(reading: UsageReading) -> UsageDecision | None:
    if not reading.is_using_overage:
        return None
    return UsageDecision(
        action=UsageAction.STOP,
        reason=OVERAGE_REASON,
        resume_at=None,
        stop_reason=StopReason.ALLOWANCE,
    )


def task_estimate(observed_costs: list[float], reserve: float) -> float:
    return max([reserve, *observed_costs])


def seven_day_decision(
    window: UsageWindow, usage: UsagePolicy, observed_costs: list[float]
) -> UsageDecision | None:
    estimate = task_estimate(observed_costs, usage.seven_day_reserve)
    if window.utilization + estimate <= usage.seven_day_ceiling:
        return None
    return UsageDecision(
        action=UsageAction.STOP,
        reason=SEVEN_DAY_REASON.format(
            spent=percentage(window.utilization),
            estimate=percentage(estimate),
            ceiling=percentage(usage.seven_day_ceiling),
        ),
        resume_at=None,
        stop_reason=StopReason.ALLOWANCE,
    )


def resume_moment(resets_at: datetime | None, now: datetime) -> datetime:
    if resets_at is None or resets_at <= now:
        return now + timedelta(seconds=FALLBACK_PARK_SECONDS)
    return resets_at + timedelta(seconds=RESET_MARGIN_SECONDS)


def wait_or_stop(
    utilization: float, resets_at: datetime | None, now: datetime, deadline: datetime
) -> UsageDecision:
    resume_at = resume_moment(resets_at, now)
    if resume_at < deadline:
        minutes = (resume_at - now).total_seconds() / SECONDS_PER_MINUTE
        return UsageDecision(
            action=UsageAction.PARK,
            reason=PARK_REASON.format(spent=percentage(utilization), minutes=round(minutes)),
            resume_at=resume_at,
            stop_reason=None,
        )
    return UsageDecision(
        action=UsageAction.STOP,
        reason=FIVE_HOUR_REASON.format(spent=percentage(utilization), resume_at=resume_at),
        resume_at=None,
        stop_reason=StopReason.FIVE_HOUR_LIMIT,
    )


def five_hour_decision(
    window: UsageWindow, usage: UsagePolicy, now: datetime, deadline: datetime
) -> UsageDecision | None:
    if window.utilization < usage.five_hour_ceiling:
        return None
    return wait_or_stop(window.utilization, window.resets_at, now, deadline)


def rejection_decision(
    rejection: LimitRejection, now: datetime, deadline: datetime
) -> UsageDecision:
    if rejection.rate_limit_type not in (None, FIVE_HOUR_WINDOW):
        return UsageDecision(
            action=UsageAction.STOP,
            reason=WEEKLY_REJECTION_REASON.format(window=rejection.rate_limit_type),
            resume_at=None,
            stop_reason=StopReason.ALLOWANCE,
        )
    return wait_or_stop(FULL_UTILIZATION, rejection.resets_at, now, deadline)


def decide(
    reading: UsageReading | None,
    rejection: LimitRejection | None,
    usage: UsagePolicy,
    now: datetime,
    deadline: datetime,
    observed_costs: list[float],
) -> UsageDecision:
    if rejection is not None:
        return rejection_decision(rejection, now, deadline)
    if reading is None:
        return UsageDecision(
            action=UsageAction.PROCEED, reason=NO_READING_REASON, resume_at=None, stop_reason=None
        )
    billed = overage_decision(reading)
    if billed is not None:
        return billed
    seven_day = live_window(reading.seven_day, now)
    if seven_day is not None:
        stopped = seven_day_decision(seven_day, usage, observed_costs)
        if stopped is not None:
            return stopped
    five_hour = live_window(reading.five_hour, now)
    if five_hour is not None:
        parked = five_hour_decision(five_hour, usage, now, deadline)
        if parked is not None:
            return parked
    return UsageDecision(
        action=UsageAction.PROCEED, reason=HEADROOM_REASON, resume_at=None, stop_reason=None
    )


def probe_invocation(policy: Policy, workbench: Path, transcript: Path) -> ClaudeInvocation:
    return ClaudeInvocation(
        prompt=PROBE_PROMPT,
        system_prompt=PROBE_SYSTEM_PROMPT,
        model=PROBE_MODEL,
        effort=PROBE_EFFORT,
        tools=PROBE_TOOLS,
        permission_mode=PROBE_PERMISSION_MODE,
        restricted=True,
        setting_sources=None,
        settings_file=policy.settings.assessor,
        json_schema=None,
        output_format=PROBE_OUTPUT_FORMAT,
        max_budget_usd=policy.usage.probe_usd,
        timeout_seconds=policy.usage.probe_timeout_seconds,
        working_directory=workbench,
        session_id=None,
        resume=False,
        persist_session=False,
        transcript_path=transcript,
        idle_seconds=policy.worker.idle_minutes * SECONDS_PER_MINUTE,
    )


def read_usage(
    policy: Policy,
    workbench: Path,
    environment: dict[str, str],
    watch: CallWatch,
    transcript: Path,
) -> ClaudeResult:
    return run_claude(probe_invocation(policy, workbench, transcript), environment, watch)


def render_usage(reading: UsageReading | None) -> str:
    if reading is None:
        return "not read"
    return (
        f"five-hour {percentage(reading.five_hour.utilization)}, "
        f"seven-day {percentage(reading.seven_day.utilization)} "
        f"(resets {reading.seven_day.resets_at:%a %H:%M} UTC)"
    )
