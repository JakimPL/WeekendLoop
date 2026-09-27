from __future__ import annotations

import threading
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict

from weekend_loop.models import (
    EventType,
    Pulse,
    RunEvent,
    RunKind,
    RunPhase,
    RunState,
    StopReason,
    Task,
    UsageReading,
)
from weekend_loop.records import append_record, write_record

RUNS_DIRECTORY_NAME: Final[str] = "runs"
RUN_STATE_FILENAME: Final[str] = "run.json"
EVENTS_FILENAME: Final[str] = "events.jsonl"
PLAN_FILENAME: Final[str] = "plan.md"
DIGEST_FILENAME: Final[str] = "digest.md"
LEDGER_FILENAME: Final[str] = "ledger.jsonl"
TASKS_DIRECTORY_NAME: Final[str] = "tasks"
INBOX_DIRECTORY_NAME: Final[str] = "inbox"
OUTBOX_DIRECTORY_NAME: Final[str] = "outbox"
PULSE_FILENAME: Final[str] = "pulse.json"
PROBES_DIRECTORY_NAME: Final[str] = "probes"
TRANSCRIPT_SUFFIX: Final[str] = ".jsonl"
RUN_ID_TIMESTAMP_FORMAT: Final[str] = "%Y%m%d-%H%M%S"
OPEN_PHASES: Final[tuple[RunPhase, ...]] = (
    RunPhase.TRIAGE,
    RunPhase.EXECUTE,
    RunPhase.PARKED,
    RunPhase.PUBLISH,
)


def new_run_id(state_directory: Path, repo_key: str, now: datetime) -> str:
    stamped = f"{now.strftime(RUN_ID_TIMESTAMP_FORMAT)}-{repo_key}"
    root = runs_root(state_directory)
    if not (root / stamped).exists():
        return stamped
    ordinals = count(2)
    return next(
        candidate
        for candidate in (f"{stamped}-{ordinal}" for ordinal in ordinals)
        if not (root / candidate).exists()
    )


class RunDirectory(BaseModel):
    model_config = ConfigDict(frozen=True)

    root: Path

    @property
    def run_state_path(self) -> Path:
        return self.root / RUN_STATE_FILENAME

    @property
    def events_path(self) -> Path:
        return self.root / EVENTS_FILENAME

    @property
    def inbox(self) -> Path:
        return self.root / INBOX_DIRECTORY_NAME

    @property
    def outbox(self) -> Path:
        return self.root / OUTBOX_DIRECTORY_NAME

    @property
    def plan_path(self) -> Path:
        return self.outbox / PLAN_FILENAME

    @property
    def digest_path(self) -> Path:
        return self.outbox / DIGEST_FILENAME

    @property
    def pulse_path(self) -> Path:
        return self.root / PULSE_FILENAME

    def task_directory(self, issue_number: int) -> Path:
        return self.root / TASKS_DIRECTORY_NAME / str(issue_number)

    def next_transcript(self, role: str, issue_number: int | None) -> Path:
        directory = (
            self.root / PROBES_DIRECTORY_NAME
            if issue_number is None
            else self.task_directory(issue_number)
        )
        taken = len(list(directory.glob(f"{role}-*{TRANSCRIPT_SUFFIX}")))
        return directory / f"{role}-{taken + 1}{TRANSCRIPT_SUFFIX}"


def runs_root(state_directory: Path) -> Path:
    return state_directory / RUNS_DIRECTORY_NAME


def create_run_directory(state_directory: Path, run_id: str) -> RunDirectory:
    run_directory = RunDirectory(root=runs_root(state_directory) / run_id)
    for path in (run_directory.root, run_directory.inbox, run_directory.outbox):
        path.mkdir(parents=True, exist_ok=True)
    return run_directory


def open_run_directory(state_directory: Path, run_id: str) -> RunDirectory:
    run_directory = RunDirectory(root=runs_root(state_directory) / run_id)
    if not run_directory.run_state_path.is_file():
        raise FileNotFoundError(f"run {run_id} has no {RUN_STATE_FILENAME}")
    return run_directory


def list_run_ids(state_directory: Path) -> list[str]:
    root = runs_root(state_directory)
    if not root.is_dir():
        return []
    return sorted(path.name for path in root.glob("*") if (path / RUN_STATE_FILENAME).is_file())


def latest_run_id(state_directory: Path) -> str:
    candidates = list_run_ids(state_directory)
    if not candidates:
        root = runs_root(state_directory)
        raise FileNotFoundError(f"no run with a {RUN_STATE_FILENAME} under {root}")
    return candidates[-1]


def resumable(state: RunState, repo_key: str, repo_slug: str) -> bool:
    return (
        state.repo_key == repo_key
        and state.repo_slug == repo_slug
        and state.kind is RunKind.WEEKEND
        and state.deadline_at is not None
        and state.phase in OPEN_PHASES
    )


def open_runs(state_directory: Path, repo_key: str, repo_slug: str) -> list[str]:
    root = runs_root(state_directory)
    return [
        run_id
        for run_id in list_run_ids(state_directory)
        if resumable(load_run_state(RunDirectory(root=root / run_id)), repo_key, repo_slug)
    ]


def append_event(
    run_directory: RunDirectory, event: EventType, detail: str, issue_number: int | None
) -> RunEvent:
    record = RunEvent(at=datetime.now(UTC), event=event, issue_number=issue_number, detail=detail)
    append_record(record, run_directory.events_path)
    return record


def save_run_state(run_directory: RunDirectory, state: RunState) -> RunState:
    now = datetime.now(UTC)
    refreshed = state.model_copy(update={"updated_at": now, "heartbeat_at": now})
    write_record(refreshed, run_directory.run_state_path)
    return refreshed


def write_pulse(run_directory: RunDirectory, pulse: Pulse) -> None:
    write_record(pulse, run_directory.pulse_path)


def read_pulse(run_directory: RunDirectory) -> Pulse | None:
    if not run_directory.pulse_path.is_file():
        return None
    return Pulse.model_validate_json(run_directory.pulse_path.read_text())


def load_run_state(run_directory: RunDirectory) -> RunState:
    return RunState.model_validate_json(run_directory.run_state_path.read_text())


def ledger_path(state_directory: Path) -> Path:
    return state_directory / LEDGER_FILENAME


class RunProgress:
    def __init__(self, run_directory: RunDirectory, state: RunState) -> None:
        self.run_directory = run_directory
        self.state = state
        self.lock = threading.Lock()

    def put_task(self, task: Task) -> None:
        with self.lock:
            tasks = [
                task if kept.issue_number == task.issue_number else kept
                for kept in self.state.tasks
            ]
            self.state = self.state.model_copy(update={"tasks": tasks})

    def task(self, issue_number: int) -> Task:
        with self.lock:
            for task in self.state.tasks:
                if task.issue_number == issue_number:
                    return task
        raise KeyError(f"run {self.state.run_id} has no task for issue #{issue_number}")

    def spend(self, usd: float) -> None:
        with self.lock:
            self.state = self.state.model_copy(update={"spent_usd": self.state.spent_usd + usd})

    def observe(self, reading: UsageReading | None) -> None:
        if reading is None:
            return
        with self.lock:
            self.state = self.state.model_copy(update={"usage": reading})

    def note(self, text: str) -> None:
        with self.lock:
            self.state = self.state.model_copy(update={"notes": [*self.state.notes, text]})

    def stop(self, reason: StopReason, detail: str) -> None:
        with self.lock:
            self.state = self.state.model_copy(
                update={"stop_reason": reason, "stop_detail": detail}
            )

    def enter_phase(self, phase: RunPhase) -> None:
        with self.lock:
            self.state = self.state.model_copy(update={"phase": phase})

    def save(self) -> RunState:
        with self.lock:
            self.state = save_run_state(self.run_directory, self.state)
            return self.state
