import json
from datetime import UTC, datetime
from pathlib import Path

from weekend_loop.models import LedgerEntry, RepoMode, RunPhase, RunState, TaskStatus
from weekend_loop.records import append_record, read_record, write_record
from weekend_loop.runs import create_run_directory, new_run_id


def sample_run_state() -> RunState:
    now = datetime(2026, 9, 18, 20, 0, tzinfo=UTC)
    return RunState(
        run_id="2026-09-18T20-00-demo",
        repo_key="demo",
        mode=RepoMode.EXECUTE,
        phase=RunPhase.TRIAGE,
        started_at=now,
        updated_at=now,
        heartbeat_at=now,
        envelope_usd=15.0,
        spent_usd=0.0,
        usage=None,
        tasks=[],
        notes=[],
    )


def test_run_state_round_trips_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    path = tmp_path / "runs" / "one" / "run.json"
    state = sample_run_state()
    write_record(state, path)
    assert read_record(RunState, path) == state
    assert sorted(entry.name for entry in path.parent.iterdir()) == ["run.json"]


def test_a_run_state_written_before_stop_reasons_still_loads() -> None:
    written = sample_run_state().model_dump(mode="json")
    del written["stop_reason"]
    del written["stop_detail"]
    written["usage_stop"] = "usage ceiling: the seven-day window is 94%"
    loaded = RunState.model_validate_json(json.dumps(written))
    assert loaded.stop_reason is None
    assert loaded.stop_detail is None


def test_ledger_appends_one_line_per_entry(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    entry = LedgerEntry(
        run_id="run",
        repo_key="demo",
        issue_number=7,
        estimate_usd=6.0,
        actual_usd=2.5,
        outcome=TaskStatus.REVIEW,
        session_id=None,
        finished_at=datetime(2026, 9, 19, 1, 0, tzinfo=UTC),
    )
    append_record(entry, ledger)
    append_record(entry, ledger)
    lines = ledger.read_text().splitlines()
    assert len(lines) == 2
    assert LedgerEntry.model_validate_json(lines[0]) == entry


def test_two_runs_in_the_same_second_take_separate_directories(tmp_path: Path) -> None:
    now = datetime(2026, 9, 18, 21, 0, tzinfo=UTC)
    first = new_run_id(tmp_path, "demo", now)
    create_run_directory(tmp_path, first)
    second = new_run_id(tmp_path, "demo", now)
    assert second != first
    assert second.startswith(first)
