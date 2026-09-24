from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.unit.conftest import (
    assessment_payload,
    claude_response,
    delivery_payload,
    error_result,
    limit_result,
    rejected_event,
    usage_event,
    write_claude_responses,
    write_worker_plan,
    write_worker_stream,
)
from weekend_loop.assessor import ASSESSOR_PERMISSION_MODE
from weekend_loop.claude_cli import (
    UNWATCHED,
    CallEnd,
    ClaudeInvocation,
    Ending,
    agent_environment,
    build_command,
    classify_outcome,
    extract_structured_output,
    last_result_message,
    parse_rejection,
    parse_usage_event,
    run_claude,
    stderr_path,
    usage_from_stream,
)
from weekend_loop.models import ActivityKind, ClaudeOutcome, LimitRejection
from weekend_loop.runs import create_run_directory, read_pulse
from weekend_loop.supervision import RunSupervisor
from weekend_loop.worker import WORKER_PERMISSION_MODE

ASSESSOR_TIMEOUT_SECONDS = 480
CALL_TIMEOUT_SECONDS = 2700
IDLE_SECONDS = 900
NOW = datetime(2026, 9, 18, 21, 0, tzinfo=UTC)
FIVE_HOUR_RESET = datetime(2026, 9, 18, 23, 30, tzinfo=UTC)
SEVEN_DAY_RESET = datetime(2026, 9, 20, 17, 0, tzinfo=UTC)


def build_event(info: dict[str, Any]) -> dict[str, Any]:
    return {"type": "rate_limit_event", "rate_limit_info": info}


def full_info() -> dict[str, Any]:
    return {
        "status": "allowed_warning",
        "resetsAt": int(SEVEN_DAY_RESET.timestamp()),
        "rateLimitType": "seven_day",
        "utilization": 0.89,
        "isUsingOverage": False,
        "surpassedThreshold": 0.75,
        "unifiedWindows": {
            "five_hour": {
                "utilization": 0.12,
                "resetsAt": int(FIVE_HOUR_RESET.timestamp()),
            },
            "seven_day": {
                "utilization": 0.89,
                "resetsAt": int(SEVEN_DAY_RESET.timestamp()),
            },
        },
    }


def test_an_allowance_report_becomes_a_reading_of_both_windows() -> None:
    reading = parse_usage_event(build_event(full_info()), NOW)
    assert reading is not None
    assert reading.five_hour.utilization == 0.12
    assert reading.five_hour.resets_at == FIVE_HOUR_RESET
    assert reading.seven_day.utilization == 0.89
    assert reading.seven_day.resets_at == SEVEN_DAY_RESET
    assert reading.status == "allowed_warning"
    assert reading.is_using_overage is False
    assert reading.observed_at == NOW


def test_the_reset_instants_arrive_as_epoch_seconds_and_land_aware() -> None:
    reading = parse_usage_event(build_event(full_info()), NOW)
    assert reading is not None
    assert reading.five_hour.resets_at.tzinfo is not None
    assert reading.seven_day.resets_at.tzinfo is not None


def test_overage_is_carried_and_utilization_may_pass_one() -> None:
    info = full_info()
    info["isUsingOverage"] = True
    info["unifiedWindows"]["seven_day"]["utilization"] = 1.04
    reading = parse_usage_event(build_event(info), NOW)
    assert reading is not None
    assert reading.is_using_overage is True
    assert reading.seven_day.utilization == 1.04


def test_a_message_that_is_not_an_allowance_report_reads_as_nothing() -> None:
    assert parse_usage_event({"type": "result", "total_cost_usd": 0.1}, NOW) is None


def test_an_allowance_report_missing_its_windows_reads_as_nothing() -> None:
    missing: list[dict[str, Any]] = [
        {},
        {"status": "allowed"},
        {"status": "allowed", "unifiedWindows": {}},
        {"status": "allowed", "unifiedWindows": {"five_hour": {"utilization": 0.1}}},
        {
            "status": "allowed",
            "unifiedWindows": {
                "five_hour": {"utilization": 0.1, "resetsAt": 1789731000},
                "seven_day": {"utilization": 0.9},
            },
        },
    ]
    for info in missing:
        assert parse_usage_event(build_event(info), NOW) is None, info


def test_an_unnamed_status_does_not_lose_the_reading() -> None:
    info = full_info()
    del info["status"]
    reading = parse_usage_event(build_event(info), NOW)
    assert reading is not None
    assert reading.status == "unknown"


def build_invocation(
    tmp_path: Path, output_format: str, session_id: str | None
) -> ClaudeInvocation:
    return ClaudeInvocation(
        prompt="assess issue #1",
        system_prompt="be careful",
        model="sonnet",
        effort="low",
        tools=["Read", "Grep", "Glob"],
        permission_mode=ASSESSOR_PERMISSION_MODE,
        restricted=True,
        setting_sources=None,
        settings_file=tmp_path / "settings.json",
        json_schema='{"type": "object"}',
        output_format=output_format,
        max_budget_usd=0.5,
        timeout_seconds=ASSESSOR_TIMEOUT_SECONDS,
        working_directory=tmp_path,
        session_id=session_id,
        resume=False,
        persist_session=False,
        transcript_path=tmp_path / "transcripts" / "call-1.jsonl",
        idle_seconds=IDLE_SECONDS,
    )


def worker_call(tmp_path: Path, idle_seconds: int) -> ClaudeInvocation:
    return build_invocation(tmp_path, "stream-json", None).model_copy(
        update={"permission_mode": WORKER_PERMISSION_MODE, "idle_seconds": idle_seconds}
    )


def test_command_wraps_claude_in_timeout_and_carries_the_fence(tmp_path: Path) -> None:
    command = build_command(build_invocation(tmp_path, "json", None))
    assert command[:3] == ["setpriv", "--pdeathsig", "TERM"]
    assert command[3:6] == ["timeout", "--kill-after=30s", f"{ASSESSOR_TIMEOUT_SECONDS}s"]
    assert command[6:9] == ["claude", "-p", "assess issue #1"]
    pairs = list(zip(command, command[1:], strict=False))
    assert ("--permission-prompts", "none") in pairs
    assert ("--permission-mode", "plan") in pairs
    assert ("--tools", "Read,Grep,Glob") in pairs
    assert ("--max-budget-usd", "0.5") in pairs
    assert "--restricted" in command
    assert "--no-session-persistence" in command
    assert "--disable-slash-commands" in command
    assert "--strict-mcp-config" in command


def test_stream_json_adds_verbose_and_the_session_identifier(tmp_path: Path) -> None:
    command = build_command(build_invocation(tmp_path, "stream-json", "abc"))
    assert "--verbose" in command
    assert list(zip(command, command[1:], strict=False)).count(("--session-id", "abc")) == 1


def test_environment_holds_the_agent_home_and_hides_operator_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_TOKEN", "operator-token")
    environment = agent_environment(tmp_path / "agent_home", "oauth", {"UV_NO_SYNC": "1"})
    assert environment["HOME"] == str(tmp_path / "agent_home")
    assert environment["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth"
    assert environment["UV_NO_SYNC"] == "1"
    assert "GH_TOKEN" not in environment


def test_a_successful_run_reports_cost_session_and_structured_output(
    tmp_path: Path, fake_binaries: Path
) -> None:
    payload = assessment_payload("execute", "XS", "tests", [], [])
    write_claude_responses(fake_binaries, {"default": claude_response(payload, 0.03)})
    result = run_claude(
        build_invocation(tmp_path, "json", None),
        agent_environment(tmp_path, "oauth", {}),
        UNWATCHED,
    )
    assert result.outcome is ClaudeOutcome.OK
    assert result.cost_usd == 0.03
    assert result.structured_output == payload
    assert result.session_id is not None
    assert result.error_message is None
    calls = [
        json.loads(line) for line in (fake_binaries / "claude-calls.jsonl").read_text().splitlines()
    ]
    assert "--restricted" in calls[0]


def test_a_streamed_run_carries_the_allowance_into_its_result(
    tmp_path: Path, fake_binaries: Path
) -> None:
    five_hour_reset = datetime(2026, 9, 18, 23, 30, tzinfo=UTC)
    seven_day_reset = datetime(2026, 9, 20, 17, 0, tzinfo=UTC)
    write_worker_plan(
        fake_binaries,
        {},
        delivery_payload("done", "chore: nothing", []),
        0.4,
        usage_event(0.12, 0.89, five_hour_reset, seven_day_reset),
    )
    result = run_claude(
        worker_call(tmp_path, IDLE_SECONDS), agent_environment(tmp_path, "oauth", {}), UNWATCHED
    )
    assert result.outcome is ClaudeOutcome.OK
    assert result.usage is not None
    assert result.usage.five_hour.utilization == 0.12
    assert result.usage.seven_day.resets_at == seven_day_reset


def test_a_streamed_run_without_an_allowance_report_carries_none(
    tmp_path: Path, fake_binaries: Path
) -> None:
    write_worker_plan(fake_binaries, {}, delivery_payload("done", "chore: nothing", []), 0.4, None)
    result = run_claude(
        worker_call(tmp_path, IDLE_SECONDS), agent_environment(tmp_path, "oauth", {}), UNWATCHED
    )
    assert result.usage is None


class StopOnceTheChildRuns:
    def __init__(self, marker: Path) -> None:
        self.marker = marker

    def stop_requested(self) -> bool:
        return self.marker.is_file()

    def beat(self) -> None:
        return None


def process_gone(pid: int) -> bool:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        status = Path(f"/proc/{pid}/stat")
        if not status.is_file() or status.read_text().split()[2] == "Z":
            return True
        time.sleep(0.1)
    return False


def test_the_transcript_holds_every_streamed_line_and_stdin_is_empty(
    tmp_path: Path, fake_binaries: Path
) -> None:
    write_worker_plan(fake_binaries, {}, delivery_payload("done", "chore: nothing", []), 0.4, None)
    invocation = worker_call(tmp_path, IDLE_SECONDS)
    run_claude(invocation, agent_environment(tmp_path, "oauth", {}), UNWATCHED)
    lines = invocation.transcript_path.read_text().splitlines()
    assert [json.loads(line)["type"] for line in lines] == ["assistant", "result"]
    assert stderr_path(invocation.transcript_path).is_file()
    stdin = (fake_binaries / "claude-stdin.jsonl").read_text().splitlines()
    assert [json.loads(line) for line in stdin] == ["/dev/null"]


def test_a_call_that_falls_silent_ends_as_stalled(tmp_path: Path, fake_binaries: Path) -> None:
    write_worker_stream(fake_binaries, {}, [], 0)
    plan = json.loads((fake_binaries / "claude-worker.json").read_text())
    (fake_binaries / "claude-worker.json").write_text(json.dumps({**plan, "sleep_seconds": 60}))
    started = time.monotonic()
    result = run_claude(
        worker_call(tmp_path, 2), agent_environment(tmp_path, "oauth", {}), UNWATCHED
    )
    assert result.outcome is ClaudeOutcome.STALLED
    assert time.monotonic() - started < 15


def test_a_stop_ends_the_whole_process_group(tmp_path: Path, fake_binaries: Path) -> None:
    write_worker_stream(fake_binaries, {}, [{"type": "assistant", "message": "working"}], 0)
    plan = json.loads((fake_binaries / "claude-worker.json").read_text())
    worker_plan = {**plan, "sleep_seconds": 60, "spawn_child": True}
    (fake_binaries / "claude-worker.json").write_text(json.dumps(worker_plan))
    child_marker = fake_binaries / "claude-child.pid"
    result = run_claude(
        worker_call(tmp_path, IDLE_SECONDS),
        agent_environment(tmp_path, "oauth", {}),
        StopOnceTheChildRuns(child_marker),
    )
    assert result.outcome is ClaudeOutcome.CANCELLED
    assert process_gone(int(child_marker.read_text()))


def test_the_pulse_keeps_beating_during_a_call(tmp_path: Path, fake_binaries: Path) -> None:
    write_worker_plan(fake_binaries, {}, delivery_payload("done", "chore: nothing", []), 0.4, None)
    plan = json.loads((fake_binaries / "claude-worker.json").read_text())
    (fake_binaries / "claude-worker.json").write_text(json.dumps({**plan, "sleep_seconds": 5}))
    run_directory = create_run_directory(tmp_path / "state", "20260918-210000-demo")
    supervisor = RunSupervisor(run_directory, 0.0)
    invocation = worker_call(tmp_path, IDLE_SECONDS)
    supervisor.enter(ActivityKind.WORKING, 1, invocation.transcript_path, None)
    entered = read_pulse(run_directory)
    run_claude(invocation, agent_environment(tmp_path, "oauth", {}), supervisor)
    beaten = read_pulse(run_directory)
    assert entered is not None and beaten is not None
    assert (beaten.heartbeat_at - entered.heartbeat_at).total_seconds() >= 2
    assert beaten.activity is not None and beaten.activity.kind is ActivityKind.WORKING
    assert beaten.pid == os.getpid()


def ended(exit_code: int, duration_seconds: float) -> CallEnd:
    return CallEnd(
        ending=Ending.EXITED,
        exit_code=exit_code,
        duration_seconds=duration_seconds,
        timeout_seconds=CALL_TIMEOUT_SECONDS,
    )


SUCCESS = claude_response(assessment_payload("execute", "XS", "tests", [], []), 0.1)
FIVE_HOUR_REJECTION = LimitRejection(rate_limit_type="five_hour", resets_at=None)
OPUS_REJECTION = LimitRejection(rate_limit_type="seven_day_opus", resets_at=None)


@pytest.mark.parametrize(
    ("end", "payload", "rejection", "stderr", "expected"),
    [
        (ended(124, 10.0), None, None, "", ClaudeOutcome.TIMEOUT),
        (ended(137, CALL_TIMEOUT_SECONDS + 30.0), None, None, "", ClaudeOutcome.TIMEOUT),
        (ended(137, 12.0), None, None, "", ClaudeOutcome.KILLED),
        (
            ended(1, 1.0),
            None,
            None,
            "No conversation found with session ID: x",
            ClaudeOutcome.SESSION_MISSING,
        ),
        (ended(1, 1.0), None, FIVE_HOUR_REJECTION, "", ClaudeOutcome.WINDOW_LIMIT),
        (ended(1, 1.0), None, None, "You've hit your weekly limit", ClaudeOutcome.WEEKLY_LIMIT),
        (ended(0, 1.0), None, None, "", ClaudeOutcome.NO_OUTPUT),
        (ended(0, 1.0), SUCCESS, None, "", ClaudeOutcome.OK),
        (
            ended(0, 1.0),
            {**SUCCESS, "result": "Added rate limit handling"},
            None,
            "",
            ClaudeOutcome.OK,
        ),
        (ended(0, 1.0), {**SUCCESS, "result": "Kept the budget check"}, None, "", ClaudeOutcome.OK),
        (
            ended(0, 1.0),
            {**SUCCESS, "result": "weekly limit"},
            OPUS_REJECTION,
            "",
            ClaudeOutcome.OK,
        ),
        (
            ended(1, 1.0),
            limit_result("You've hit your session limit · resets 3:45pm", 0.2),
            None,
            "",
            ClaudeOutcome.WINDOW_LIMIT,
        ),
        (
            ended(1, 1.0),
            limit_result("You've hit your Opus limit · resets Mon 12:00am", 0.2),
            None,
            "",
            ClaudeOutcome.WEEKLY_LIMIT,
        ),
        (
            ended(1, 1.0),
            limit_result("You've hit your limit", 0.2),
            OPUS_REJECTION,
            "",
            ClaudeOutcome.WEEKLY_LIMIT,
        ),
        (ended(1, 1.0), limit_result("Request failed", 0.2), None, "", ClaudeOutcome.WINDOW_LIMIT),
        (
            ended(1, 1.0),
            error_result("error_max_budget_usd", ["Reached maximum budget ($6)"], None, 6.0),
            None,
            "",
            ClaudeOutcome.BUDGET,
        ),
        (
            ended(1, 1.0),
            error_result("error_during_execution", ["context"], "blocking_limit", 0.3),
            None,
            "",
            ClaudeOutcome.FAILED,
        ),
        (
            ended(0, 1.0),
            {"is_error": True, "result": "something broke"},
            None,
            "",
            ClaudeOutcome.FAILED,
        ),
    ],
)
def test_the_outcome_is_read_from_the_structured_fields(
    end: CallEnd,
    payload: dict[str, Any] | None,
    rejection: LimitRejection | None,
    stderr: str,
    expected: ClaudeOutcome,
) -> None:
    assert classify_outcome(end, payload, rejection, stderr) is expected


def test_a_cancelled_call_is_cancelled_whatever_it_printed() -> None:
    cancelled = ended(143, 5.0).model_copy(update={"ending": Ending.CANCELLED})
    assert classify_outcome(cancelled, SUCCESS, None, "") is ClaudeOutcome.CANCELLED


def test_a_rejected_allowance_report_is_read_without_its_windows() -> None:
    resets_at = datetime(2026, 9, 18, 23, 30, tzinfo=UTC)
    stream = "\n".join(
        [
            json.dumps(usage_event(0.5, 0.2, resets_at, resets_at)),
            json.dumps(rejected_event("five_hour", resets_at)),
            json.dumps(limit_result("You've hit your session limit", 0.0)),
        ]
    )
    assert parse_rejection(stream) == LimitRejection(
        rate_limit_type="five_hour", resets_at=resets_at
    )


def test_an_allowed_report_is_no_rejection() -> None:
    resets_at = datetime(2026, 9, 18, 23, 30, tzinfo=UTC)
    stream = json.dumps(usage_event(0.5, 0.2, resets_at, resets_at))
    assert parse_rejection(stream) is None


def test_the_stream_reports_the_last_result_message() -> None:
    stream = "\n".join(
        [
            json.dumps({"type": "assistant", "message": "working"}),
            "not json at all",
            json.dumps({"type": "result", "total_cost_usd": 0.1}),
        ]
    )
    message = last_result_message(stream)
    assert message is not None
    assert message["total_cost_usd"] == 0.1


def test_the_stream_reports_the_allowance_the_limits_carried() -> None:
    observed_at = datetime(2026, 9, 18, 21, 0, tzinfo=UTC)
    stream = "\n".join(
        [
            json.dumps(
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {
                        "status": "allowed_warning",
                        "isUsingOverage": False,
                        "unifiedWindows": {
                            "five_hour": {"utilization": 0.12, "resetsAt": 1789731000},
                            "seven_day": {"utilization": 0.89, "resetsAt": 1789923600},
                        },
                    },
                }
            ),
            "not json at all",
            json.dumps({"type": "assistant", "message": "working"}),
            json.dumps({"type": "result", "total_cost_usd": 0.1}),
        ]
    )
    reading = usage_from_stream(stream, observed_at)
    assert reading is not None
    assert reading.seven_day.utilization == 0.89
    assert reading.observed_at == observed_at


def test_a_stream_without_an_allowance_report_carries_no_usage() -> None:
    stream = json.dumps({"type": "result", "total_cost_usd": 0.1})
    assert usage_from_stream(stream, datetime(2026, 9, 18, 21, 0, tzinfo=UTC)) is None


def test_structured_output_falls_back_to_json_written_as_text() -> None:
    payload = assessment_payload("skip", "L", "interface", ["too_large"], [])
    fenced = "```json\n" + json.dumps(payload) + "\n```"
    assert extract_structured_output({"result": fenced}) == payload
