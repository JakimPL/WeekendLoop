from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest

from tests.unit.conftest import ELIGIBLE, build_run_state, build_task, write_test_policy
from tests.unit.live_run import (
    build_activity,
    build_pulse,
    orchestrator_stand_in,
    said,
    sleeping_process,
    thought,
    tool_call,
    write_transcript,
)
from weekend_loop.cli import main
from weekend_loop.models import (
    ActivityKind,
    EventType,
    RepoMode,
    RunPhase,
    RunState,
    StopReason,
    TaskStatus,
    UsageReading,
    UsageWindow,
)
from weekend_loop.policy import policy_at
from weekend_loop.runs import (
    RunDirectory,
    append_event,
    create_run_directory,
    save_run_state,
    write_pulse,
)
from weekend_loop.status import (
    Liveness,
    judge_liveness,
    load_status,
    mentions_orchestrator,
    process_alive,
    render_status,
)

RUN_ID: Final[str] = "20260918-210000-demo"
REPO_KEY: Final[str] = "demo"
BOOT_ID: Final[str] = "6f1d2c1e-boot"
OTHER_BOOT_ID: Final[str] = "0000aaaa-boot"
PID: Final[int] = 4242
NOW: Final[datetime] = datetime(2026, 9, 18, 21, 10, tzinfo=UTC)


def always_alive(pid: int) -> bool:
    return True


def never_alive(pid: int) -> bool:
    return False


def running_state() -> RunState:
    tasks = [
        build_task(1, "Empty speed field", TaskStatus.REVIEW, ELIGIBLE, None),
        build_task(3, "Bearing range", TaskStatus.APPROVED, ELIGIBLE, None),
    ]
    finished = build_run_state(tasks, 1.2, [], RUN_ID, REPO_KEY, RepoMode.EXECUTE)
    usage = UsageReading(
        five_hour=UsageWindow(utilization=0.11, resets_at=NOW + timedelta(hours=2)),
        seven_day=UsageWindow(utilization=0.42, resets_at=NOW + timedelta(days=3)),
        status="allowed",
        is_using_overage=False,
        observed_at=NOW,
    )
    return finished.model_copy(update={"phase": RunPhase.EXECUTE, "usage": usage})


def seed_run(state_directory: Path, state: RunState) -> RunDirectory:
    run_directory = create_run_directory(state_directory, RUN_ID)
    save_run_state(run_directory, state)
    return run_directory


def seed_working_run(state_directory: Path) -> RunDirectory:
    run_directory = seed_run(state_directory, running_state())
    transcript = write_transcript(
        run_directory.task_directory(3) / "worker-1.jsonl",
        [
            thought("The range check is off by one."),
            tool_call("Edit", {"file_path": "logbook/bearing.py"}),
            said("Fixed the bound."),
        ],
    )
    activity = build_activity(ActivityKind.WORKING, 3, transcript, NOW - timedelta(minutes=2), None)
    write_pulse(run_directory, build_pulse(PID, BOOT_ID, NOW - timedelta(seconds=12), activity))
    append_event(run_directory, EventType.TASK_STARTED, "branch weekend/3-bearing-range", 3)
    return run_directory


@pytest.mark.parametrize(
    ("phase", "has_pulse", "boot_id", "alive", "expected"),
    [
        (RunPhase.FINISHED, True, BOOT_ID, True, Liveness.FINISHED),
        (RunPhase.ABORTED, False, BOOT_ID, False, Liveness.FINISHED),
        (RunPhase.EXECUTE, False, BOOT_ID, True, Liveness.UNKNOWN),
        (RunPhase.EXECUTE, True, BOOT_ID, True, Liveness.ALIVE),
        (RunPhase.TRIAGE, True, BOOT_ID, False, Liveness.DEAD),
        (RunPhase.EXECUTE, True, OTHER_BOOT_ID, True, Liveness.DEAD),
        (RunPhase.PARKED, True, OTHER_BOOT_ID, False, Liveness.DEAD),
    ],
)
def test_liveness_follows_the_phase_the_boot_and_the_process(
    phase: RunPhase, has_pulse: bool, boot_id: str, alive: bool, expected: Liveness
) -> None:
    pulse = build_pulse(PID, boot_id, NOW, None) if has_pulse else None
    check = always_alive if alive else never_alive
    assert judge_liveness(phase, pulse, BOOT_ID, check) is expected


@pytest.mark.parametrize(
    ("command_line", "expected"),
    [
        (".venv/bin/python3 .venv/bin/weekend-loop weekend --repo-key demo", True),
        ("python3 -m weekend_loop.cli weekend", True),
        ("/usr/bin/python3 -m http.server", False),
        ("", False),
    ],
)
def test_the_orchestrator_is_recognised_by_its_command_line(
    command_line: str, expected: bool
) -> None:
    assert mentions_orchestrator(command_line) is expected


def test_a_process_counts_as_alive_only_while_it_runs_the_orchestrator() -> None:
    with orchestrator_stand_in() as pid:
        assert process_alive(pid)
    assert not process_alive(pid)
    with sleeping_process([]) as stranger:
        assert not process_alive(stranger)


def test_the_status_reads_the_pulse_the_run_and_the_transcript(tmp_path: Path) -> None:
    run_directory = seed_working_run(tmp_path / "state")

    status = load_status(run_directory, 2, 10, NOW, BOOT_ID, always_alive)

    assert status.liveness is Liveness.ALIVE
    assert status.pid == PID
    assert status.pulse_age_seconds == 12.0
    assert status.activity is not None and status.activity.kind is ActivityKind.WORKING
    assert status.transcript == run_directory.task_directory(3) / "worker-1.jsonl"
    assert status.transcript_lines == ["Edit: logbook/bearing.py", "says: Fixed the bound."]
    assert [event.event for event in status.events] == [EventType.TASK_STARTED]
    assert [(task.issue_number, task.status) for task in status.tasks] == [
        (1, "review"),
        (3, "approved"),
    ]


def test_the_rendered_status_says_what_the_run_is_doing(tmp_path: Path) -> None:
    run_directory = seed_working_run(tmp_path / "state")

    rendered = render_status(load_status(run_directory, 5, 10, NOW, BOOT_ID, always_alive))

    assert f"run {RUN_ID} on {REPO_KEY} · phase execute" in rendered
    assert f"alive · pid {PID} · pulse 12 s ago" in rendered
    assert "now: working on #3 for 2 min" in rendered
    assert "spent $1.20 of $15.00 · allowance five-hour 11%, seven-day 42% (allowed)" in rendered
    assert "task_started #3: branch weekend/3-bearing-range" in rendered
    assert "thinking: The range check is off by one." in rendered
    assert "#1     review          $0.00  Empty speed field" in rendered


def test_a_run_whose_process_is_gone_reads_as_dead(tmp_path: Path) -> None:
    run_directory = seed_working_run(tmp_path / "state")

    status = load_status(run_directory, 0, 0, NOW, BOOT_ID, never_alive)

    assert status.liveness is Liveness.DEAD
    assert status.transcript_lines == []
    assert "last: working on #3" in render_status(status)


def test_between_calls_the_status_shows_the_newest_transcript(tmp_path: Path) -> None:
    run_directory = seed_run(tmp_path / "state", running_state())
    older = write_transcript(run_directory.task_directory(1) / "worker-1.jsonl", [said("older")])
    newer = write_transcript(run_directory.root / "probes" / "probe-1.jsonl", [said("newer")])
    os.utime(older, (1_000_000, 1_000_000))
    gating = build_activity(ActivityKind.GATING, 1, None, NOW, None)
    write_pulse(run_directory, build_pulse(PID, BOOT_ID, NOW, gating))

    status = load_status(run_directory, 5, 0, NOW, BOOT_ID, always_alive)

    assert status.transcript == newer
    assert status.transcript_lines == ["says: newer"]


def test_a_parked_run_says_when_it_resumes(tmp_path: Path) -> None:
    run_directory = seed_run(tmp_path / "state", running_state())
    resumes_at = NOW + timedelta(minutes=25)
    parked = build_activity(ActivityKind.PARKED, None, None, NOW, resumes_at)
    write_pulse(run_directory, build_pulse(PID, BOOT_ID, NOW, parked))

    rendered = render_status(load_status(run_directory, 5, 0, NOW, BOOT_ID, always_alive))

    assert "now: parked until 2026-09-18 21:35 UTC (in 25 min)" in rendered


def test_a_stopped_run_names_its_reason(tmp_path: Path) -> None:
    stopped = running_state().model_copy(
        update={
            "phase": RunPhase.FINISHED,
            "stop_reason": StopReason.ALLOWANCE,
            "stop_detail": "seven-day window at 91%",
        }
    )
    run_directory = seed_run(tmp_path / "state", stopped)

    status = load_status(run_directory, 5, 0, NOW, BOOT_ID, always_alive)

    assert status.liveness is Liveness.FINISHED
    assert "stopped: allowance — seven-day window at 91%" in render_status(status)
    assert "finished · no pulse" in render_status(status)


def test_the_run_kind_and_deadline_show_once_the_run_records_them(tmp_path: Path) -> None:
    run_directory = seed_run(tmp_path / "state", running_state())
    raw = json.loads(run_directory.run_state_path.read_text())
    raw.update({"kind": "weekend", "deadline_at": "2026-09-19T03:00:00Z"})
    run_directory.run_state_path.write_text(json.dumps(raw))

    status = load_status(run_directory, 0, 0, NOW, BOOT_ID, always_alive)

    assert status.kind == "weekend"
    assert f"run {RUN_ID} on {REPO_KEY} (weekend)" in render_status(status)
    assert "deadline 2026-09-19 03:00 UTC" in render_status(status)


def test_the_status_command_prints_the_latest_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy_path = write_test_policy(tmp_path, None)
    seed_working_run(policy_at(policy_path).state_dir)

    assert main(["--home", str(policy_path), "status", "--lines", "1"]) == 0

    printed = capsys.readouterr().out
    assert f"run {RUN_ID} on {REPO_KEY}" in printed
    assert "dead · pid 4242" in printed
    assert "says: Fixed the bound." in printed
    assert "thinking:" not in printed


def test_the_status_lists_every_task_in_flight(tmp_path: Path) -> None:
    run_directory = seed_run(tmp_path / "state", running_state())
    earlier = build_activity(ActivityKind.WORKING, 1, None, NOW - timedelta(minutes=5), None)
    latest = build_activity(ActivityKind.WORKING, 3, None, NOW - timedelta(minutes=2), None)
    pulse = build_pulse(PID, BOOT_ID, NOW, latest).model_copy(
        update={"activities": [earlier, latest]}
    )
    write_pulse(run_directory, pulse)

    rendered = render_status(load_status(run_directory, 0, 10, NOW, BOOT_ID, always_alive))

    assert "now: working on #3 for 2 min" in rendered
    assert "also: working on #1 for 5 min" in rendered
    assert rendered.count("also:") == 1
