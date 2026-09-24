from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest

from tests.unit.conftest import ELIGIBLE, build_run_state, build_task, write_test_policy
from tests.unit.live_run import (
    append_transcript,
    build_activity,
    build_pulse,
    said,
    thought,
    tool_call,
    write_transcript,
)
from weekend_loop import cli
from weekend_loop.models import ActivityKind, EventType, RepoMode, RunPhase, TaskStatus
from weekend_loop.policy import policy_at
from weekend_loop.runs import (
    RunDirectory,
    append_event,
    create_run_directory,
    load_run_state,
    save_run_state,
    write_pulse,
)
from weekend_loop.watch import WatchTick, starting_position, watch_tick

RUN_ID: Final[str] = "20260918-210000-demo"
BOOT_ID: Final[str] = "6f1d2c1e-boot"
PID: Final[int] = 4242
NOW: Final[datetime] = datetime(2026, 9, 18, 21, 10, tzinfo=UTC)


def always_alive(pid: int) -> bool:
    return True


def never_alive(pid: int) -> bool:
    return False


def seed_working_run(state_directory: Path) -> RunDirectory:
    run_directory = create_run_directory(state_directory, RUN_ID)
    tasks = [build_task(3, "Bearing range", TaskStatus.APPROVED, ELIGIBLE, None)]
    state = build_run_state(tasks, 0.4, [], RUN_ID, "demo", RepoMode.EXECUTE)
    save_run_state(run_directory, state.model_copy(update={"phase": RunPhase.EXECUTE}))
    transcript = write_transcript(
        run_directory.task_directory(3) / "worker-1.jsonl",
        [thought("The range check is off by one."), said("Fixing the bound.")],
    )
    enter(run_directory, ActivityKind.WORKING, 3, transcript)
    append_event(run_directory, EventType.TASK_STARTED, "branch weekend/3-bearing-range", 3)
    return run_directory


def enter(
    run_directory: RunDirectory, kind: ActivityKind, issue_number: int | None, transcript: Path
) -> None:
    activity = build_activity(kind, issue_number, transcript, NOW - timedelta(seconds=30), None)
    write_pulse(run_directory, build_pulse(PID, BOOT_ID, NOW, activity))


def tick(run_directory: RunDirectory, previous: WatchTick | None) -> WatchTick:
    position = previous.position if previous is not None else starting_position()
    return watch_tick(run_directory, position, NOW, BOOT_ID, always_alive)


def test_the_first_look_prints_the_status_and_the_transcript_so_far(tmp_path: Path) -> None:
    run_directory = seed_working_run(tmp_path / "state")

    first = tick(run_directory, None)

    assert not first.finished
    assert first.output[0] == f"run {RUN_ID} on demo · phase execute"
    assert "now: working on #3 for 30 s" in first.output
    assert first.output[-3:] == [
        "-- transcript tasks/3/worker-1.jsonl",
        "  thinking: The range check is off by one.",
        "  says: Fixing the bound.",
    ]


def test_later_looks_print_only_what_was_appended(tmp_path: Path) -> None:
    run_directory = seed_working_run(tmp_path / "state")
    first = tick(run_directory, None)

    assert tick(run_directory, first).output == []

    append_transcript(
        run_directory.task_directory(3) / "worker-1.jsonl",
        [tool_call("Bash", {"command": "uv run pytest"})],
    )
    append_event(run_directory, EventType.WORKER_FINISHED, "done for $0.40", 3)
    second = tick(run_directory, first)

    assert second.output[0] == "  Bash: uv run pytest"
    assert second.output[1].startswith("-- ")
    assert second.output[1].endswith(" worker_finished #3: done for $0.40")
    assert len(second.output) == 2


def test_a_new_activity_flushes_the_old_transcript_and_follows_the_new_one(
    tmp_path: Path,
) -> None:
    run_directory = seed_working_run(tmp_path / "state")
    first = tick(run_directory, None)
    append_transcript(run_directory.task_directory(3) / "worker-1.jsonl", [said("Done.")])
    probe = write_transcript(run_directory.root / "probes" / "probe-1.jsonl", [said("ok")])
    enter(run_directory, ActivityKind.PROBING, None, probe)

    second = tick(run_directory, first)

    assert second.output == [
        "  says: Done.",
        "== 21:10:00 UTC · execute · alive · now: probing the allowance for 30 s",
        "-- transcript probes/probe-1.jsonl",
        "  says: ok",
    ]


def test_the_watch_ends_when_the_run_finishes(tmp_path: Path) -> None:
    run_directory = seed_working_run(tmp_path / "state")
    first = tick(run_directory, None)
    state = load_run_state(run_directory)
    save_run_state(run_directory, state.model_copy(update={"phase": RunPhase.FINISHED}))

    last = tick(run_directory, first)

    assert last.finished
    assert last.output[-2:] == [
        "== 21:10:00 UTC · finished · last: working on #3 for 30 s",
        "== the run has finished",
    ]


def test_the_watch_ends_when_the_process_behind_the_run_is_gone(tmp_path: Path) -> None:
    run_directory = seed_working_run(tmp_path / "state")

    only = watch_tick(run_directory, starting_position(), NOW, BOOT_ID, never_alive)

    assert only.finished
    assert only.output[-1] == "== the run's process is gone; its pulse stopped"


def test_the_watch_command_returns_once_the_run_is_over(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy_path = write_test_policy(tmp_path, None)
    seed_working_run(policy_at(policy_path).state_dir)

    assert cli.main(["--home", str(policy_path), "watch"]) == 0

    printed = capsys.readouterr().out
    assert f"run {RUN_ID} on demo" in printed
    assert "  says: Fixing the bound." in printed
    assert "== the run's process is gone; its pulse stopped" in printed


def test_an_interrupted_watch_exits_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy_path = write_test_policy(tmp_path, None)
    seed_working_run(policy_at(policy_path).state_dir)

    def interrupted(
        run_directory: RunDirectory, poll_seconds: float, emit: Callable[[str], None]
    ) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "follow_run", interrupted)

    assert cli.main(["--home", str(policy_path), "watch", "--run-id", RUN_ID]) == 0
