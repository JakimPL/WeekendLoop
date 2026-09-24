from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from weekend_loop.claude_cli import build_command
from weekend_loop.limits import (
    FALLBACK_PARK_SECONDS,
    NO_READING_REASON,
    OVERAGE_REASON,
    RESET_MARGIN_SECONDS,
    UsageAction,
    decide,
    probe_invocation,
)
from weekend_loop.models import (
    LimitRejection,
    Policy,
    StopReason,
    UsagePolicy,
    UsageReading,
    UsageWindow,
)

NOW = datetime(2026, 9, 18, 21, 0, tzinfo=UTC)
FIVE_HOUR_RESET = datetime(2026, 9, 18, 23, 30, tzinfo=UTC)
SEVEN_DAY_RESET = datetime(2026, 9, 20, 17, 0, tzinfo=UTC)


DEADLINE = datetime(2026, 9, 20, 21, 59, tzinfo=UTC)


def build_usage_policy(
    seven_day_ceiling: float, seven_day_reserve: float, five_hour_ceiling: float
) -> UsagePolicy:
    return UsagePolicy(
        seven_day_ceiling=seven_day_ceiling,
        seven_day_reserve=seven_day_reserve,
        five_hour_ceiling=five_hour_ceiling,
        probe_usd=0.05,
        probe_timeout_seconds=120,
    )


def build_reading(
    five_hour_utilization: float,
    seven_day_utilization: float,
    five_hour_reset: datetime,
    seven_day_reset: datetime,
) -> UsageReading:
    return UsageReading(
        five_hour=UsageWindow(utilization=five_hour_utilization, resets_at=five_hour_reset),
        seven_day=UsageWindow(utilization=seven_day_utilization, resets_at=seven_day_reset),
        status="allowed",
        is_using_overage=False,
        observed_at=NOW,
    )


DEFAULT_POLICY_USAGE = build_usage_policy(0.95, 0.08, 0.95)


def test_a_window_with_room_lets_the_next_task_start() -> None:
    reading = build_reading(0.12, 0.40, FIVE_HOUR_RESET, SEVEN_DAY_RESET)
    decision = decide(reading, None, DEFAULT_POLICY_USAGE, NOW, DEADLINE, [])
    assert decision.action is UsageAction.PROCEED
    assert decision.resume_at is None


def test_no_reading_lets_the_next_task_start() -> None:
    decision = decide(None, None, DEFAULT_POLICY_USAGE, NOW, DEADLINE, [])
    assert decision.action is UsageAction.PROCEED
    assert decision.reason == NO_READING_REASON


def test_an_account_drawing_on_usage_credits_ends_the_phase() -> None:
    covered = build_reading(0.12, 0.40, FIVE_HOUR_RESET, SEVEN_DAY_RESET)
    billed = covered.model_copy(update={"is_using_overage": True})
    decision = decide(billed, None, DEFAULT_POLICY_USAGE, NOW, DEADLINE, [])
    assert decision.action is UsageAction.STOP
    assert decision.reason == OVERAGE_REASON
    assert decision.resume_at is None


def test_a_seven_day_window_without_room_for_one_more_task_ends_the_phase() -> None:
    reading = build_reading(0.10, 0.90, FIVE_HOUR_RESET, SEVEN_DAY_RESET)
    decision = decide(reading, None, DEFAULT_POLICY_USAGE, NOW, DEADLINE, [])
    assert decision.action is UsageAction.STOP
    assert "seven-day" in decision.reason


def test_the_reserve_is_only_a_floor_and_measurement_raises_it() -> None:
    reading = build_reading(0.10, 0.80, FIVE_HOUR_RESET, SEVEN_DAY_RESET)
    unmeasured = decide(reading, None, DEFAULT_POLICY_USAGE, NOW, DEADLINE, [])
    measured = decide(reading, None, DEFAULT_POLICY_USAGE, NOW, DEADLINE, [0.04, 0.20])
    assert unmeasured.action is UsageAction.PROCEED
    assert measured.action is UsageAction.STOP


def test_a_spent_five_hour_window_waits_for_its_reset_however_long() -> None:
    reset = NOW + timedelta(hours=4, minutes=50)
    reading = build_reading(0.97, 0.20, reset, SEVEN_DAY_RESET)
    decision = decide(reading, None, DEFAULT_POLICY_USAGE, NOW, DEADLINE, [])
    assert decision.action is UsageAction.PARK
    assert decision.resume_at == reset + timedelta(seconds=RESET_MARGIN_SECONDS)


def test_a_five_hour_reset_after_the_deadline_ends_the_phase() -> None:
    reset = DEADLINE + timedelta(minutes=5)
    reading = build_reading(0.97, 0.20, reset, SEVEN_DAY_RESET)
    decision = decide(
        reading, None, DEFAULT_POLICY_USAGE, DEADLINE - timedelta(hours=2), DEADLINE, []
    )
    assert decision.action is UsageAction.STOP
    assert decision.stop_reason is StopReason.FIVE_HOUR_LIMIT
    assert "five-hour" in decision.reason


def test_a_rejected_five_hour_call_parks_until_its_reset() -> None:
    reset = NOW + timedelta(minutes=30)
    rejection = LimitRejection(rate_limit_type="five_hour", resets_at=reset)
    decision = decide(None, rejection, DEFAULT_POLICY_USAGE, NOW, DEADLINE, [])
    assert decision.action is UsageAction.PARK
    assert decision.resume_at == reset + timedelta(seconds=RESET_MARGIN_SECONDS)


def test_a_rejection_without_a_reset_parks_briefly_and_asks_again() -> None:
    rejection = LimitRejection(rate_limit_type=None, resets_at=None)
    decision = decide(None, rejection, DEFAULT_POLICY_USAGE, NOW, DEADLINE, [])
    assert decision.action is UsageAction.PARK
    assert decision.resume_at == NOW + timedelta(seconds=FALLBACK_PARK_SECONDS)


def test_a_rejected_weekly_call_ends_the_phase() -> None:
    rejection = LimitRejection(rate_limit_type="seven_day_opus", resets_at=None)
    reading = build_reading(0.10, 0.20, FIVE_HOUR_RESET, SEVEN_DAY_RESET)
    decision = decide(reading, rejection, DEFAULT_POLICY_USAGE, NOW, DEADLINE, [])
    assert decision.action is UsageAction.STOP
    assert decision.stop_reason is StopReason.ALLOWANCE


def test_a_window_that_already_reset_is_unknown_rather_than_empty() -> None:
    spent_but_stale = build_reading(0.99, 0.99, NOW - timedelta(minutes=1), NOW - timedelta(days=1))
    decision = decide(spent_but_stale, None, DEFAULT_POLICY_USAGE, NOW, DEADLINE, [])
    assert decision.action is UsageAction.PROCEED


def test_a_seven_day_stop_outranks_a_five_hour_wait() -> None:
    reading = build_reading(0.99, 0.94, NOW + timedelta(minutes=10), SEVEN_DAY_RESET)
    decision = decide(reading, None, DEFAULT_POLICY_USAGE, NOW, DEADLINE, [])
    assert decision.action is UsageAction.STOP
    assert "seven-day" in decision.reason


def test_the_probe_asks_for_nothing_it_does_not_need(workspace_policy: Policy) -> None:
    policy = workspace_policy
    command = build_command(probe_invocation(policy, policy.agent_home, Path("probe.jsonl")))
    pairs = list(zip(command, command[1:], strict=False))
    assert ("--tools", "") in pairs
    assert "--json-schema" not in command
    assert "--restricted" in command
    assert ("--permission-mode", "plan") in pairs
    assert ("--max-budget-usd", str(policy.usage.probe_usd)) in pairs
    assert "--no-session-persistence" in command
