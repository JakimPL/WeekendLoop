from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import yaml

from tests.unit.conftest import (
    delivery_payload,
    limit_result,
    rejected_event,
    worker_plan,
    worker_result,
    write_worker_plans,
)
from tests.unit.test_execute import FIXED_RECORDS, RUN_ID, execute, prepare
from weekend_loop.attempt import WorkerStep, next_worker_step, worker_cost_after
from weekend_loop.cli import EXIT_BLOCKED, main
from weekend_loop.mailbox import PAUSE_FILENAME, clear_request, request_pause, request_stop
from weekend_loop.models import (
    ClaudeOutcome,
    Policy,
    RunState,
    StopReason,
    TaskStatus,
    Workspace,
)
from weekend_loop.policy import policy_at
from weekend_loop.runs import load_run_state, open_run_directory, runs_root, save_run_state
from weekend_loop.worker import RESUME_PROMPT

FIX = {"logbook/records.py": FIXED_RECORDS}
SESSION_LIMIT = "You've hit your session limit · resets 3:45pm"
OPUS_LIMIT = "You've hit your Opus limit · resets Mon 12:00am"
WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def approved() -> dict[str, Any]:
    return delivery_payload("done", "fix(records): treat an empty speed as unknown", [])


def limited_plan(rate_limit_type: str, resets_at: datetime, text: str) -> dict[str, Any]:
    stream = [rejected_event(rate_limit_type, resets_at), limit_result(text, 0.8)]
    return worker_plan(FIX, stream, 1)


def finished_plan() -> dict[str, Any]:
    return worker_plan({}, [worker_result(approved(), 0.4, "done")], 0)


def prepared(tmp_path: Path, fake_binaries: Path) -> tuple[Policy, Path]:
    return prepare(tmp_path, fake_binaries, ["weekend:auto"], FIX, approved(), 3, [1])


def worker_calls(fake_binaries: Path) -> list[list[str]]:
    calls = [
        json.loads(line) for line in (fake_binaries / "claude-calls.jsonl").read_text().splitlines()
    ]
    return [call for call in calls if "acceptEdits" in call]


def value_after(call: list[str], flag: str) -> str:
    return call[call.index(flag) + 1]


def finished_run(policy: Policy) -> RunState:
    return load_run_state(open_run_directory(policy.state_dir, RUN_ID))


def events_of(policy: Policy) -> str:
    return open_run_directory(policy.state_dir, RUN_ID).events_path.read_text()


def set_deadline(policy: Policy, deadline: datetime) -> None:
    run_directory = open_run_directory(policy.state_dir, RUN_ID)
    state = load_run_state(run_directory)
    save_run_state(run_directory, state.model_copy(update={"deadline_at": deadline}))


@pytest.mark.parametrize(
    ("outcome", "stall_resumes", "fresh_used", "expected"),
    [
        (ClaudeOutcome.OK, 0, False, WorkerStep.FINISH),
        (ClaudeOutcome.WINDOW_LIMIT, 1, True, WorkerStep.WAIT_AND_RESUME),
        (ClaudeOutcome.STALLED, 0, False, WorkerStep.RESUME),
        (ClaudeOutcome.STALLED, 1, False, WorkerStep.FINISH),
        (ClaudeOutcome.SESSION_MISSING, 0, False, WorkerStep.START_FRESH),
        (ClaudeOutcome.SESSION_MISSING, 0, True, WorkerStep.FINISH),
        (ClaudeOutcome.WEEKLY_LIMIT, 0, False, WorkerStep.FINISH),
        (ClaudeOutcome.TIMEOUT, 0, False, WorkerStep.FINISH),
    ],
)
def test_each_way_a_worker_call_ends_leads_to_one_next_step(
    outcome: ClaudeOutcome, stall_resumes: int, fresh_used: bool, expected: WorkerStep
) -> None:
    assert next_worker_step(outcome, stall_resumes, fresh_used) is expected


def test_a_resumed_call_adds_its_own_cost_to_the_task() -> None:
    assert worker_cost_after(1.5, 0.25) == pytest.approx(1.75)


def test_a_worker_cut_off_by_the_five_hour_window_waits_and_resumes_its_session(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepared(tmp_path, fake_binaries)
    reset = datetime.now(UTC) + timedelta(seconds=2)
    write_worker_plans(
        fake_binaries, [limited_plan("five_hour", reset, SESSION_LIMIT), finished_plan()]
    )
    assert execute(policy_path) == 0
    state = finished_run(policy)
    assert state.tasks[0].status is TaskStatus.REVIEW
    assert state.tasks[0].worker_cost_usd == pytest.approx(1.2)
    first, second = worker_calls(fake_binaries)
    assert value_after(second, "--resume") == value_after(first, "--session-id")
    assert value_after(second, "-p") == RESUME_PROMPT
    events = events_of(policy)
    assert "usage_parked" in events
    assert "task_resumed" in events


def test_a_reset_after_the_deadline_leaves_an_unfinished_draft_and_ends_the_run(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepared(tmp_path, fake_binaries)
    set_deadline(policy, datetime.now(UTC) + timedelta(hours=1))
    reset = datetime.now(UTC) + timedelta(hours=3)
    write_worker_plans(fake_binaries, [limited_plan("five_hour", reset, SESSION_LIMIT)])
    assert execute(policy_path) == 0
    state = finished_run(policy)
    assert state.tasks[0].status is TaskStatus.UNFINISHED
    assert state.stop_reason is StopReason.FIVE_HOUR_LIMIT
    assert len(worker_calls(fake_binaries)) == 1


def test_a_weekly_limit_in_the_middle_of_a_task_is_not_waited_out(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepared(tmp_path, fake_binaries)
    reset = datetime.now(UTC) + timedelta(days=2)
    write_worker_plans(fake_binaries, [limited_plan("seven_day_opus", reset, OPUS_LIMIT)])
    assert execute(policy_path) == 0
    state = finished_run(policy)
    assert state.stop_reason is StopReason.ALLOWANCE
    assert state.tasks[0].status is TaskStatus.UNFINISHED
    assert len(worker_calls(fake_binaries)) == 1
    assert "usage_parked" not in events_of(policy)


def test_a_session_that_cannot_be_resumed_starts_the_task_afresh(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepared(tmp_path, fake_binaries)
    reset = datetime.now(UTC) + timedelta(seconds=2)
    forgetful = {**limited_plan("five_hour", reset, SESSION_LIMIT), "forget_sessions": True}
    write_worker_plans(fake_binaries, [forgetful, finished_plan()])
    assert execute(policy_path) == 0
    first, resumed, fresh = worker_calls(fake_binaries)
    assert value_after(resumed, "--resume") == value_after(first, "--session-id")
    assert value_after(fresh, "--session-id") != value_after(first, "--session-id")
    assert finished_run(policy).tasks[0].status is TaskStatus.REVIEW


def later(action: Callable[[], object], seconds: float) -> threading.Thread:
    def run() -> None:
        time.sleep(seconds)
        action()

    thread = threading.Thread(target=run)
    thread.start()
    return thread


def test_a_pause_holds_the_run_until_the_operator_lifts_it(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepared(tmp_path, fake_binaries)
    inbox = open_run_directory(policy.state_dir, RUN_ID).inbox
    request_pause(inbox, "hold on")
    started = time.monotonic()
    lifter = later(lambda: clear_request(inbox, PAUSE_FILENAME), 1.5)
    assert execute(policy_path) == 0
    lifter.join()
    assert time.monotonic() - started >= 1.5
    assert finished_run(policy).tasks[0].status is TaskStatus.REVIEW


def test_a_stop_during_a_pause_ends_the_run_without_work(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepared(tmp_path, fake_binaries)
    inbox = open_run_directory(policy.state_dir, RUN_ID).inbox
    request_pause(inbox, "hold on")
    stopper = later(lambda: request_stop(inbox, "enough"), 1.0)
    assert execute(policy_path) == 0
    stopper.join()
    state = finished_run(policy)
    assert state.stop_reason is StopReason.OPERATOR
    assert state.tasks[0].branch is None
    assert worker_calls(fake_binaries) == []


def window_elsewhere(policy_path: Path) -> None:
    config = Workspace(root=policy_path).config_path
    raw = yaml.safe_load(config.read_text())
    local_now = datetime.now(ZoneInfo(raw["schedule"]["timezone"]))
    day = WEEKDAY_NAMES[(local_now.weekday() + 3) % len(WEEKDAY_NAMES)]
    raw["schedule"]["window"] = {
        "opens": {"day_of_week": day, "hour": 0, "minute": 0},
        "closes": {"day_of_week": day, "hour": 1, "minute": 0},
    }
    raw["schedule"]["runs"] = [{"day_of_week": day, "hour": 0, "minute": 30, "command": "weekend"}]
    config.write_text(yaml.safe_dump(raw))


def test_a_weekend_run_outside_its_window_is_refused_before_anything_starts(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepared(tmp_path, fake_binaries)
    window_elsewhere(policy_path)
    runs_before = sorted(runs_root(policy.state_dir).iterdir())
    arguments = ["--home", str(policy_path), "weekend", "--repo-key", "demo"]
    assert main(arguments) == EXIT_BLOCKED
    assert sorted(runs_root(policy_at(policy_path).state_dir).iterdir()) == runs_before
    assert not (fake_binaries / "claude-calls.jsonl").exists()
