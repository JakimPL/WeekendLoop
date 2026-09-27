from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from weekend_loop.limits import FIVE_HOUR_WINDOW, UsageAction, UsageDecision, decide, read_usage
from weekend_loop.models import (
    ActivityKind,
    ClaudeResult,
    EventType,
    LimitRejection,
    Policy,
    Record,
    StopReason,
    UsageReading,
)
from weekend_loop.runs import append_event
from weekend_loop.supervision import STOP_DETAIL, RunSupervisor

PROBE_TRANSCRIPT_ROLE: Final[str] = "probe"
UNTIMED_FIVE_HOUR_LIMIT: Final[LimitRejection] = LimitRejection(
    rate_limit_type=FIVE_HOUR_WINDOW, resets_at=None
)


class AllowanceVerdict(Record):
    proceed: bool
    stop_reason: StopReason | None
    detail: str
    reading: UsageReading | None
    spent_usd: float


def seven_day_cost(before: UsageReading | None, after: UsageReading | None) -> float | None:
    if before is None or after is None:
        return None
    if before.seven_day.resets_at != after.seven_day.resets_at:
        return None
    return max(after.seven_day.utilization - before.seven_day.utilization, 0.0)


def probe(
    policy: Policy, supervisor: RunSupervisor, workbench: Path, environment: dict[str, str]
) -> ClaudeResult:
    transcript = supervisor.transcript_for(PROBE_TRANSCRIPT_ROLE, None)
    supervisor.enter(ActivityKind.PROBING, None, transcript, None)
    result = read_usage(policy, workbench, environment, supervisor, transcript)
    supervisor.leave(None)
    return result


def await_allowance(
    policy: Policy,
    supervisor: RunSupervisor,
    workbench: Path,
    environment: dict[str, str],
    deadline: datetime,
    previous: UsageReading | None,
    pending: LimitRejection | None,
    observed_costs: list[float],
) -> AllowanceVerdict:
    reading = previous
    spent = 0.0
    rejection = pending
    while True:
        latest: UsageReading | None = None
        if rejection is None:
            result = probe(policy, supervisor, workbench, environment)
            spent += result.cost_usd
            measured = seven_day_cost(reading, result.usage)
            if measured is not None:
                observed_costs.append(measured)
            latest, rejection = result.usage, result.rejection
            reading = latest if latest is not None else reading
        decision = decide(
            latest, rejection, policy.usage, datetime.now(UTC), deadline, observed_costs
        )
        if decision.action is not UsageAction.PARK:
            return verdict_of(decision, reading, spent)
        append_event(supervisor.run_directory, EventType.USAGE_PARKED, decision.reason, None)
        if not supervisor.park(resume_moment_of(decision)):
            return AllowanceVerdict(
                proceed=False,
                stop_reason=StopReason.OPERATOR,
                detail=STOP_DETAIL,
                reading=reading,
                spent_usd=spent,
            )
        rejection = None


def resume_moment_of(decision: UsageDecision) -> datetime:
    if decision.resume_at is None:
        raise ValueError("a parking decision names the moment it resumes")
    return decision.resume_at


def verdict_of(
    decision: UsageDecision, reading: UsageReading | None, spent_usd: float
) -> AllowanceVerdict:
    proceed = decision.action is UsageAction.PROCEED
    stop_reason = None if proceed else (decision.stop_reason or StopReason.ALLOWANCE)
    return AllowanceVerdict(
        proceed=proceed,
        stop_reason=stop_reason,
        detail=decision.reason,
        reading=reading,
        spent_usd=spent_usd,
    )
