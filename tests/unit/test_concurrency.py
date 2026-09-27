from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from tests.unit.conftest import build_run_state
from weekend_loop.models import ActivityKind, LedgerEntry, RepoMode, RunState, TaskStatus
from weekend_loop.records import append_record, write_record
from weekend_loop.runs import RunProgress, create_run_directory, load_run_state, read_pulse
from weekend_loop.supervision import PULSE_SECONDS, RunSupervisor

RUN_ID: Final[str] = "20260918-210000-demo"
THREADS: Final[int] = 8
ROUNDS: Final[int] = 40


def in_threads(action: Callable[[int], None]) -> None:
    threads = [threading.Thread(target=action, args=(index,)) for index in range(THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def fresh_state(spent_usd: float) -> RunState:
    return build_run_state([], spent_usd, [], RUN_ID, "demo", RepoMode.EXECUTE)


def ledger_entry(index: int) -> LedgerEntry:
    return LedgerEntry(
        run_id=RUN_ID,
        repo_key="demo",
        issue_number=index,
        estimate_usd=6.0,
        actual_usd=1.0,
        outcome=TaskStatus.REVIEW,
        session_id=None,
        finished_at=datetime.now(UTC),
    )


def test_spending_from_many_threads_at_once_loses_nothing(tmp_path: Path) -> None:
    run_directory = create_run_directory(tmp_path, RUN_ID)
    progress = RunProgress(run_directory, fresh_state(0.0))

    def spend(index: int) -> None:
        for _ in range(ROUNDS):
            progress.spend(1.0)
            progress.note(f"thread {index}")
            progress.save()

    in_threads(spend)
    assert progress.state.spent_usd == THREADS * ROUNDS
    assert len(progress.state.notes) == THREADS * ROUNDS
    assert load_run_state(run_directory).spent_usd == THREADS * ROUNDS
    assert [path.name for path in run_directory.root.iterdir() if path.name.startswith(".")] == []


def test_records_written_from_many_threads_stay_whole(tmp_path: Path) -> None:
    state_path = tmp_path / "state" / "run.json"
    ledger_path = tmp_path / "state" / "ledger.jsonl"

    def write(index: int) -> None:
        for _ in range(ROUNDS):
            write_record(fresh_state(float(index)), state_path)
            append_record(ledger_entry(index), ledger_path)

    in_threads(write)
    assert RunState.model_validate_json(state_path.read_text()).run_id == RUN_ID
    lines = ledger_path.read_text().splitlines()
    assert len(lines) == THREADS * ROUNDS
    assert {LedgerEntry.model_validate_json(line).issue_number for line in lines} == set(
        range(THREADS)
    )
    assert not state_path.with_name(".run.json.tmp").exists()


def test_the_pulse_lists_every_task_in_flight_and_follows_the_latest(tmp_path: Path) -> None:
    run_directory = create_run_directory(tmp_path, RUN_ID)
    supervisor = RunSupervisor(run_directory, PULSE_SECONDS)
    supervisor.enter(ActivityKind.WORKING, 1, None, None)
    supervisor.enter(ActivityKind.WORKING, 2, None, None)
    pulse = read_pulse(run_directory)
    assert pulse is not None and pulse.activity is not None
    assert pulse.activity.issue_number == 2
    assert [activity.issue_number for activity in pulse.activities] == [1, 2]

    supervisor.leave(2)
    pulse = read_pulse(run_directory)
    assert pulse is not None and pulse.activity is not None
    assert pulse.activity.issue_number == 1
    assert [activity.issue_number for activity in pulse.activities] == [1]

    supervisor.leave(1)
    pulse = read_pulse(run_directory)
    assert pulse is not None and pulse.activity is not None
    assert pulse.activity.issue_number == 1
    assert pulse.activity.kind is ActivityKind.WORKING
    assert pulse.activities == []


def test_transcripts_handed_out_at_once_never_share_a_name(tmp_path: Path) -> None:
    run_directory = create_run_directory(tmp_path, RUN_ID)
    supervisor = RunSupervisor(run_directory, PULSE_SECONDS)
    handed_out: list[Path] = []
    guard = threading.Lock()

    def take(index: int) -> None:
        for _ in range(ROUNDS):
            transcript = supervisor.transcript_for("probe", None)
            with guard:
                handed_out.append(transcript)

    in_threads(take)
    assert len(set(handed_out)) == THREADS * ROUNDS
    assert all(transcript.is_file() for transcript in handed_out)
