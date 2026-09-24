from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from weekend_loop.models import (
    Activity,
    ActivityKind,
    Pulse,
    Record,
    RunEvent,
    RunPhase,
    RunState,
    StopReason,
    UsageReading,
)
from weekend_loop.runs import (
    PROBES_DIRECTORY_NAME,
    TASKS_DIRECTORY_NAME,
    TRANSCRIPT_SUFFIX,
    RunDirectory,
    load_run_state,
    read_pulse,
)
from weekend_loop.supervision import current_boot_id
from weekend_loop.transcript import appended_lines, transcript_tail, usage_phrase

STATUS_TRANSCRIPT_LINES: Final[int] = 20
STATUS_EVENT_COUNT: Final[int] = 10
PROCESS_ROOT: Final[Path] = Path("/proc")
COMMAND_LINE_FILENAME: Final[str] = "cmdline"
ORCHESTRATOR_NAMES: Final[tuple[str, ...]] = ("weekend-loop", "weekend_loop")
FINISHED_PHASES: Final[frozenset[RunPhase]] = frozenset({RunPhase.FINISHED, RunPhase.ABORTED})
SECONDS_PER_MINUTE: Final[int] = 60
SECONDS_PER_HOUR: Final[int] = 3600
CLOCK_FORMAT: Final[str] = "%H:%M:%S"
MOMENT_FORMAT: Final[str] = "%Y-%m-%d %H:%M UTC"
TRANSCRIPT_GLOBS: Final[tuple[str, ...]] = (
    f"{TASKS_DIRECTORY_NAME}/*/*{TRANSCRIPT_SUFFIX}",
    f"{PROBES_DIRECTORY_NAME}/*{TRANSCRIPT_SUFFIX}",
)
ACTIVITY_VERBS: Final[dict[ActivityKind, str]] = {
    ActivityKind.PROBING: "probing the allowance",
    ActivityKind.ASSESSING: "assessing",
    ActivityKind.WORKING: "working on",
    ActivityKind.GATING: "gating",
    ActivityKind.PARKED: "parked",
    ActivityKind.PUBLISHING: "publishing",
}

type ProcessCheck = Callable[[int], bool]


class Liveness(StrEnum):
    ALIVE = "alive"
    DEAD = "dead"
    FINISHED = "finished"
    UNKNOWN = "unknown"


class RunExtras(Record):
    kind: str | None = None
    deadline_at: datetime | None = None


class TaskLine(Record):
    issue_number: int
    title: str
    status: str
    cost_usd: float


class RunStatus(Record):
    run_id: str
    repo_key: str
    kind: str | None
    phase: RunPhase
    liveness: Liveness
    pid: int | None
    pulse_age_seconds: float | None
    activity: Activity | None
    spent_usd: float
    envelope_usd: float
    usage: UsageReading | None
    deadline_at: datetime | None
    stop_reason: StopReason | None
    stop_detail: str | None
    tasks: list[TaskLine]
    events: list[RunEvent]
    transcript: Path | None
    transcript_lines: list[str]
    observed_at: datetime


def mentions_orchestrator(command_line: str) -> bool:
    return any(name in command_line for name in ORCHESTRATOR_NAMES)


def read_command_line(pid: int) -> str | None:
    try:
        raw = (PROCESS_ROOT / str(pid) / COMMAND_LINE_FILENAME).read_bytes()
    except (FileNotFoundError, ProcessLookupError):
        return None
    return raw.replace(b"\0", b" ").decode(errors="replace")


def process_alive(pid: int) -> bool:
    command_line = read_command_line(pid)
    return command_line is not None and mentions_orchestrator(command_line)


def judge_liveness(
    phase: RunPhase, pulse: Pulse | None, boot_id: str, is_alive: ProcessCheck
) -> Liveness:
    if phase in FINISHED_PHASES:
        return Liveness.FINISHED
    if pulse is None:
        return Liveness.UNKNOWN
    if pulse.boot_id == boot_id and is_alive(pulse.pid):
        return Liveness.ALIVE
    return Liveness.DEAD


def seconds_between(earlier: datetime, later: datetime) -> float:
    return max((later - earlier).total_seconds(), 0.0)


def task_lines(state: RunState) -> list[TaskLine]:
    return [
        TaskLine(
            issue_number=task.issue_number,
            title=task.title,
            status=task.status.value,
            cost_usd=task.cost_usd,
        )
        for task in state.tasks
    ]


def build_status(
    state: RunState,
    extras: RunExtras,
    pulse: Pulse | None,
    liveness: Liveness,
    events: list[RunEvent],
    transcript: Path | None,
    transcript_lines: list[str],
    now: datetime,
) -> RunStatus:
    return RunStatus(
        run_id=state.run_id,
        repo_key=state.repo_key,
        kind=extras.kind,
        phase=state.phase,
        liveness=liveness,
        pid=pulse.pid if pulse is not None else None,
        pulse_age_seconds=seconds_between(pulse.heartbeat_at, now) if pulse is not None else None,
        activity=pulse.activity if pulse is not None else None,
        spent_usd=state.spent_usd,
        envelope_usd=state.envelope_usd,
        usage=state.usage,
        deadline_at=extras.deadline_at,
        stop_reason=state.stop_reason,
        stop_detail=state.stop_detail,
        tasks=task_lines(state),
        events=events,
        transcript=transcript,
        transcript_lines=transcript_lines,
        observed_at=now,
    )


def read_run_extras(run_directory: RunDirectory) -> RunExtras:
    return RunExtras.model_validate_json(run_directory.run_state_path.read_text())


def read_run_events(run_directory: RunDirectory) -> list[RunEvent]:
    return parse_events(appended_lines(run_directory.events_path, 0)[0])


def parse_events(lines: list[str]) -> list[RunEvent]:
    return [RunEvent.model_validate_json(line) for line in lines if line.strip()]


def newest_transcript(run_directory: RunDirectory) -> Path | None:
    candidates = [path for pattern in TRANSCRIPT_GLOBS for path in run_directory.root.glob(pattern)]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def watched_transcript(pulse: Pulse | None, run_directory: RunDirectory) -> Path | None:
    if pulse is not None and pulse.activity is not None and pulse.activity.transcript is not None:
        return pulse.activity.transcript
    return newest_transcript(run_directory)


def last_events(events: list[RunEvent], count: int) -> list[RunEvent]:
    return events[-count:] if count > 0 else []


def load_status(
    run_directory: RunDirectory,
    line_count: int,
    event_count: int,
    now: datetime,
    boot_id: str,
    is_alive: ProcessCheck,
) -> RunStatus:
    state = load_run_state(run_directory)
    pulse = read_pulse(run_directory)
    transcript = watched_transcript(pulse, run_directory)
    return build_status(
        state,
        read_run_extras(run_directory),
        pulse,
        judge_liveness(state.phase, pulse, boot_id, is_alive),
        last_events(read_run_events(run_directory), event_count),
        transcript,
        transcript_tail(transcript, line_count) if transcript is not None else [],
        now,
    )


def current_status(run_directory: RunDirectory, line_count: int, event_count: int) -> RunStatus:
    return load_status(
        run_directory, line_count, event_count, datetime.now(UTC), current_boot_id(), process_alive
    )


def age_phrase(seconds: float) -> str:
    whole = int(seconds)
    if whole < SECONDS_PER_MINUTE:
        return f"{whole} s"
    if whole < SECONDS_PER_HOUR:
        return f"{whole // SECONDS_PER_MINUTE} min"
    hours, remainder = divmod(whole, SECONDS_PER_HOUR)
    return f"{hours} h {remainder // SECONDS_PER_MINUTE} min"


def clock(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(CLOCK_FORMAT)


def moment_phrase(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(MOMENT_FORMAT)


def activity_phrase(activity: Activity, now: datetime) -> str:
    verb = ACTIVITY_VERBS.get(activity.kind, activity.kind.value)
    subject = verb if activity.issue_number is None else f"{verb} #{activity.issue_number}"
    if activity.resumes_at is not None:
        remaining = age_phrase(seconds_between(now, activity.resumes_at))
        return f"{subject} until {moment_phrase(activity.resumes_at)} (in {remaining})"
    return f"{subject} for {age_phrase(seconds_between(activity.started_at, now))}"


def current_phrase(status: RunStatus) -> str | None:
    if status.activity is None:
        return None
    tense = "last" if status.liveness in (Liveness.FINISHED, Liveness.DEAD) else "now"
    return f"{tense}: {activity_phrase(status.activity, status.observed_at)}"


def liveness_phrase(status: RunStatus) -> str:
    parts = [status.liveness.value]
    if status.pid is not None:
        parts.append(f"pid {status.pid}")
    if status.pulse_age_seconds is None:
        parts.append("no pulse")
    else:
        parts.append(f"pulse {age_phrase(status.pulse_age_seconds)} ago")
    return " · ".join(parts)


def run_title(status: RunStatus) -> str:
    kind = f" ({status.kind})" if status.kind is not None else ""
    return f"run {status.run_id} on {status.repo_key}{kind} · phase {status.phase.value}"


def spend_phrase(status: RunStatus) -> str:
    parts = [f"spent ${status.spent_usd:.2f} of ${status.envelope_usd:.2f}"]
    if status.usage is not None:
        parts.append(f"allowance {usage_phrase(status.usage)}")
    if status.deadline_at is not None:
        parts.append(f"deadline {moment_phrase(status.deadline_at)}")
    return " · ".join(parts)


def stop_phrase(status: RunStatus) -> str | None:
    if status.stop_reason is None:
        return None
    detail = f" — {status.stop_detail}" if status.stop_detail else ""
    return f"stopped: {status.stop_reason.value}{detail}"


def event_line(event: RunEvent) -> str:
    issue = f" #{event.issue_number}" if event.issue_number is not None else ""
    detail = f": {event.detail}" if event.detail else ""
    return f"{clock(event.at)} {event.event.value}{issue}{detail}"


def task_line(task: TaskLine) -> str:
    cost = f"${task.cost_usd:.2f}"
    return f"#{task.issue_number:<5} {task.status:<12} {cost:>8}  {task.title}"


def indented(lines: list[str]) -> list[str]:
    return [f"  {line}" for line in lines]


def section(title: str, lines: list[str]) -> list[str]:
    return ["", title, *indented(lines)] if lines else []


def summary_lines(status: RunStatus) -> list[str]:
    optional = [
        current_phrase(status),
        f"transcript: {status.transcript}" if status.transcript is not None else None,
        spend_phrase(status),
        stop_phrase(status),
    ]
    return [
        run_title(status),
        liveness_phrase(status),
        *[line for line in optional if line is not None],
    ]


def render_status(status: RunStatus) -> str:
    lines = [
        *summary_lines(status),
        *section("tasks", [task_line(task) for task in status.tasks]),
        *section("events (UTC)", [event_line(event) for event in status.events]),
        *section("transcript", status.transcript_lines),
    ]
    return "\n".join(lines)
