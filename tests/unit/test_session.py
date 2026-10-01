from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.unit.conftest import (
    ELIGIBLE,
    OWNER_LOGIN,
    ResetsAt,
    assessment_payload,
    build_assessment,
    build_run_state,
    build_task,
    claude_response,
    delivery_payload,
    error_result,
    issue_payload,
    limit_result,
    rejected_event,
    window_closing_soon,
    worker_result,
    write_claude_responses,
    write_execute_policy,
    write_github_data,
    write_probe_sequence,
    write_test_policy,
    write_worker_plan,
    write_worker_stream,
)
from tests.unit.test_execute import FIXED_RECORDS, RUN_ID, build_origin, execute, run_git
from weekend_loop.adopt import ANSWERED_REASON, REPLIED_REASON, stale_reason
from weekend_loop.briefing import (
    PREPARED_SESSION_MAX_AGE_HOURS,
    read_briefing,
    read_prepared,
    record_answer,
    write_prepared,
)
from weekend_loop.cli import main
from weekend_loop.github import issue_from_payload
from weekend_loop.lock import RunLockHeldError, run_lock
from weekend_loop.mailbox import request_stop
from weekend_loop.models import (
    ActivityKind,
    Effort,
    EventType,
    Issue,
    RepoMode,
    Risk,
    RunState,
    StopReason,
    Task,
    TaskStatus,
    Verdict,
    Workspace,
)
from weekend_loop.policy import policy_at
from weekend_loop.runs import (
    create_run_directory,
    latest_run_id,
    list_run_ids,
    load_run_state,
    open_run_directory,
    read_pulse,
    save_run_state,
)
from weekend_loop.schedule import render_crontab
from weekend_loop.session import publishing_allowed, work_may_follow

ISSUE_TITLE = "Empty speed field crashes the parser"
ISSUE_BODY = (
    "## Business requirement\nThe harbour log misreports vessel speed.\n\n"
    "## Goal\nParse an empty speed field as unknown instead of crashing the import.\n\n"
    "## Scope\nTouch `logbook/records.py` only; leave the report renderer alone.\n\n"
    "## Acceptance criteria\n- [ ] an empty speed field yields no reading\n"
    "- [ ] the existing suite stays green\n"
)


def prepare_weekend(tmp_path: Path, fake_binaries: Path, assessor: dict[str, object]) -> Path:
    policy_path = write_execute_policy(tmp_path, build_origin(tmp_path), None, 3)
    write_github_data(
        fake_binaries,
        issues=[issue_payload(1, ISSUE_TITLE, ISSUE_BODY, ["weekend:auto"], [])],
        pull_requests=[],
        comments={},
        push=True,
    )
    write_claude_responses(fake_binaries, {"default": assessor})
    write_worker_plan(
        fake_binaries,
        {"logbook/records.py": FIXED_RECORDS},
        delivery_payload("done", "fix(records): treat an empty speed as unknown", []),
        1.2,
        None,
    )
    return policy_path


def weekend(policy_path: Path) -> int:
    return main(["--home", str(policy_path), "weekend", "--repo-key", "demo"])


EXECUTE_VERDICT = claude_response(assessment_payload("execute", "XS", "tests", [], []), 0.02)
FIX_SUBJECT = "fix(records): treat an empty speed as unknown"


def prepare_two_issue_weekend(
    tmp_path: Path,
    fake_binaries: Path,
    responses: dict[str, dict[str, Any] | list[dict[str, Any]]],
) -> Path:
    policy_path = write_execute_policy(tmp_path, build_origin(tmp_path), None, 3)
    write_github_data(
        fake_binaries,
        issues=[
            issue_payload(number, ISSUE_TITLE, ISSUE_BODY, ["weekend:auto"], [])
            for number in (1, 2)
        ],
        pull_requests=[],
        comments={},
        push=True,
    )
    write_claude_responses(fake_binaries, responses)
    write_worker_plan(
        fake_binaries,
        {"logbook/records.py": FIXED_RECORDS},
        delivery_payload("done", FIX_SUBJECT, []),
        1.2,
        None,
    )
    return policy_path


def set_envelope(policy_path: Path, envelope_usd: float) -> None:
    config = Workspace(root=policy_path).config_path
    raw = yaml.safe_load(config.read_text())
    raw["budget"]["envelope_usd"] = envelope_usd
    config.write_text(yaml.safe_dump(raw))


def latest_state(policy_path: Path) -> RunState:
    policy = policy_at(policy_path)
    return load_run_state(open_run_directory(policy.state_dir, latest_run_id(policy.state_dir)))


def gh_calls(fake_binaries: Path) -> list[list[str]]:
    return [
        json.loads(line) for line in (fake_binaries / "gh-calls.jsonl").read_text().splitlines()
    ]


def worker_calls(fake_binaries: Path) -> list[list[str]]:
    calls = [
        json.loads(line) for line in (fake_binaries / "claude-calls.jsonl").read_text().splitlines()
    ]
    return [call for call in calls if "acceptEdits" in call]


def test_running_out_of_the_envelope_still_publishes_the_work_done(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy_path = prepare_two_issue_weekend(tmp_path, fake_binaries, {"default": EXECUTE_VERDICT})
    set_envelope(policy_path, 7.0)
    assert weekend(policy_path) == 0
    state = latest_state(policy_path)
    assert [task.status for task in state.tasks] == [TaskStatus.REVIEW, TaskStatus.ASSESSED]
    assert state.stop_reason is StopReason.ENVELOPE
    assert ["pr", "create"] in [call[:2] for call in gh_calls(fake_binaries)]


def test_a_five_hour_limit_during_triage_waits_and_assesses_the_same_issue_again(
    tmp_path: Path, fake_binaries: Path
) -> None:
    limited = {
        "stream": [
            rejected_event("five_hour", ResetsAt.SOON_AFTER_THE_CALL),
            limit_result("You've hit your session limit · resets 3:45pm", 0.0),
        ],
        "exit_code": 1,
    }
    policy_path = prepare_two_issue_weekend(
        tmp_path, fake_binaries, {"default": EXECUTE_VERDICT, "2": [limited, EXECUTE_VERDICT]}
    )
    assert weekend(policy_path) == 0
    state = latest_state(policy_path)
    assert [task.status for task in state.tasks] == [TaskStatus.REVIEW, TaskStatus.REVIEW]
    assert state.stop_reason is None
    assert len([call for call in assessor_calls(fake_binaries) if "issue #2" in call[1]]) == 2
    policy = policy_at(policy_path)
    run_directory = open_run_directory(policy.state_dir, state.run_id)
    assert "usage_parked" in run_directory.events_path.read_text()


def test_a_worker_that_writes_about_rate_limits_is_not_taken_for_one(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy_path = prepare_two_issue_weekend(tmp_path, fake_binaries, {"default": EXECUTE_VERDICT})
    write_worker_stream(
        fake_binaries,
        {"logbook/records.py": FIXED_RECORDS},
        [
            worker_result(
                delivery_payload("done", FIX_SUBJECT, []),
                1.2,
                "Added rate limit handling; the budget check stays as it was.",
            )
        ],
        0,
    )
    assert weekend(policy_path) == 0
    state = latest_state(policy_path)
    assert [task.status for task in state.tasks] == [TaskStatus.REVIEW, TaskStatus.REVIEW]
    assert len(worker_calls(fake_binaries)) == 2


def test_work_the_worker_left_unfinished_is_offered_as_a_flagged_draft(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy_path = prepare_weekend(tmp_path, fake_binaries, EXECUTE_VERDICT)
    write_worker_stream(
        fake_binaries,
        {"logbook/records.py": FIXED_RECORDS},
        [error_result("error_max_budget_usd", ["Reached maximum budget ($6)"], None, 6.0)],
        1,
    )
    assert weekend(policy_path) == 0
    state = latest_state(policy_path)
    assert [task.status for task in state.tasks] == [TaskStatus.UNFINISHED]
    create = next(call for call in gh_calls(fake_binaries) if call[:2] == ["pr", "create"])
    assert create[create.index("--title") + 1].startswith("[unfinished] ")
    bodies = (fake_binaries / "gh-bodies.jsonl").read_text()
    assert "do not merge" in bodies
    labelled = [call for call in gh_calls(fake_binaries) if call[:2] == ["issue", "edit"]]
    assert any("weekend:unfinished" in call for call in labelled)
    assert not any("weekend:review" in call for call in labelled)


def test_the_lock_keeps_a_second_run_off_the_same_state(tmp_path: Path) -> None:
    with run_lock(tmp_path):
        with pytest.raises(RunLockHeldError, match="another weekend-loop run"):
            with run_lock(tmp_path):
                pass
    with run_lock(tmp_path):
        pass


def test_a_weekend_runs_from_triage_to_a_draft_pull_request(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy_path = prepare_weekend(
        tmp_path,
        fake_binaries,
        claude_response(assessment_payload("execute", "XS", "tests", [], []), 0.02),
    )
    assert weekend(policy_path) == 0
    policy = policy_at(policy_path)
    state = load_run_state(open_run_directory(policy.state_dir, latest_run_id(policy.state_dir)))
    assert [task.status for task in state.tasks] == [TaskStatus.REVIEW]
    assert state.tasks[0].pull_request_url == "https://github.com/owner/repo/pull/42"
    assert state.spent_usd == pytest.approx(1.221)  # the allowance probe costs too
    verbs = [
        tuple(json.loads(line)[:2])
        for line in (fake_binaries / "gh-calls.jsonl").read_text().splitlines()
    ]
    assert ("pr", "create") in verbs
    assert ("issue", "create") in verbs


def test_every_call_of_a_weekend_leaves_its_transcript_and_the_run_a_pulse(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy_path = prepare_weekend(tmp_path, fake_binaries, EXECUTE_VERDICT)
    assert weekend(policy_path) == 0
    policy = policy_at(policy_path)
    run_directory = open_run_directory(policy.state_dir, latest_run_id(policy.state_dir))
    task_directory = run_directory.task_directory(1)
    for transcript in (
        task_directory / "assessment-1.jsonl",
        task_directory / "worker-1.jsonl",
        run_directory.root / "probes" / "probe-1.jsonl",
    ):
        assert '"type": "result"' in transcript.read_text(), transcript
    pulse = read_pulse(run_directory)
    assert pulse is not None
    assert pulse.activity is not None
    assert pulse.activity.kind is ActivityKind.PUBLISHING


def test_a_weekly_limit_during_triage_stops_before_any_work(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy_path = prepare_weekend(
        tmp_path,
        fake_binaries,
        {
            "type": "result",
            "subtype": "error",
            "is_error": True,
            "result": "You've hit your weekly limit",
            "session_id": "11111111-2222-3333-4444-555555555555",
            "total_cost_usd": 0.01,
            "exit_code": 1,
        },
    )
    assert weekend(policy_path) == 0
    policy = policy_at(policy_path)
    run_directory = open_run_directory(policy.state_dir, latest_run_id(policy.state_dir))
    state = load_run_state(run_directory)
    assert any("weekly_limit" in note for note in state.notes)
    assert state.tasks[0].branch is None
    assert "acceptEdits" not in (fake_binaries / "claude-calls.jsonl").read_text()
    assert "Weekend run" in run_directory.digest_path.read_text()


def test_a_weekend_without_publishing_leaves_github_alone(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy_path = prepare_weekend(
        tmp_path,
        fake_binaries,
        claude_response(assessment_payload("execute", "XS", "tests", [], []), 0.02),
    )
    arguments = ["--home", str(policy_path), "weekend", "--repo-key", "demo", "--no-publish"]
    assert main(arguments) == 0
    verbs = [
        tuple(json.loads(line)[:2])
        for line in (fake_binaries / "gh-calls.jsonl").read_text().splitlines()
    ]
    assert ("pr", "create") not in verbs
    assert ("issue", "create") not in verbs


def test_the_runs_deadline_caps_the_work(tmp_path: Path, fake_binaries: Path) -> None:
    policy_path = prepare_weekend(
        tmp_path,
        fake_binaries,
        claude_response(assessment_payload("execute", "XS", "tests", [], []), 0.02),
    )
    policy = policy_at(policy_path)
    task = build_task(
        1,
        ISSUE_TITLE,
        TaskStatus.ASSESSED,
        ELIGIBLE,
        build_assessment(Verdict.EXECUTE, Effort.XS, Risk.TESTS, [], []),
    )
    state = build_run_state([task], 0.0, [], RUN_ID, "demo", RepoMode.EXECUTE)
    passed_deadline = datetime.now(UTC) - timedelta(minutes=1)
    run_directory = create_run_directory(policy.state_dir, RUN_ID)
    save_run_state(run_directory, state.model_copy(update={"deadline_at": passed_deadline}))
    assert execute(policy_path) == 0
    finished = load_run_state(open_run_directory(policy.state_dir, RUN_ID))
    assert finished.tasks[0].branch is None
    assert finished.stop_reason is StopReason.WINDOW_CLOSED


def test_the_cron_entry_holds_a_lock_and_names_nobodys_home(tmp_path: Path) -> None:
    policy = policy_at(write_test_policy(tmp_path, None))
    rendered = render_crontab(
        policy.schedule, policy.scheduled_repo_key, policy.workspace, Path("/usr/bin/weekend-loop")
    )
    assert "flock --nonblock" in rendered
    assert str(policy.workspace.state_dir / "cron.lock") in rendered
    assert "--repo-key demo" in rendered
    assert str(Path.home()) not in rendered


def test_a_local_remote_is_the_only_place_a_branch_lands(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy_path = prepare_weekend(
        tmp_path,
        fake_binaries,
        claude_response(assessment_payload("execute", "XS", "tests", [], []), 0.02),
    )
    weekend(policy_path)
    policy = policy_at(policy_path)
    remote = policy.repos["demo"].remote_url
    assert remote is not None
    branches = run_git(["branch", "--list"], Path(remote))
    assert "weekend/1-empty-speed-field-crashes-the-parser" in branches


def assessor_calls(fake_binaries: Path) -> list[list[str]]:
    calls = [
        json.loads(line) for line in (fake_binaries / "claude-calls.jsonl").read_text().splitlines()
    ]
    return [
        call
        for call in calls
        if call[:1] == ["-p"] and "acceptEdits" not in call and not call[1].startswith("Reply with")
    ]


def prepare_pass(policy_path: Path) -> int:
    return main(["--home", str(policy_path), "prepare", "--repo-key", "demo"])


def test_a_weekend_takes_up_the_triage_the_prepare_pass_left(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy_path = prepare_weekend(
        tmp_path,
        fake_binaries,
        claude_response(assessment_payload("execute", "XS", "tests", [], []), 0.02),
    )
    assert prepare_pass(policy_path) == 0
    policy = policy_at(policy_path)
    prepared_run_id = latest_run_id(policy.state_dir)
    assessments_before = len(assessor_calls(fake_binaries))

    assert weekend(policy_path) == 0

    assert list_run_ids(policy.state_dir) == [prepared_run_id]
    assert len(assessor_calls(fake_binaries)) == assessments_before
    state = load_run_state(open_run_directory(policy.state_dir, prepared_run_id))
    assert [task.status for task in state.tasks] == [TaskStatus.REVIEW]
    assert read_prepared(policy.state_dir, "demo") is None


def test_an_adopted_run_starts_its_clock_again_rather_than_expiring_at_once(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy_path = prepare_weekend(
        tmp_path,
        fake_binaries,
        claude_response(assessment_payload("execute", "XS", "tests", [], []), 0.02),
    )
    prepare_pass(policy_path)
    policy = policy_at(policy_path)
    run_directory = open_run_directory(policy.state_dir, latest_run_id(policy.state_dir))
    stale = load_run_state(run_directory)
    save_run_state(
        run_directory, stale.model_copy(update={"started_at": stale.started_at - timedelta(days=1)})
    )

    assert weekend(policy_path) == 0

    state = load_run_state(run_directory)
    assert not any("run duration" in note for note in state.notes)
    assert [task.status for task in state.tasks] == [TaskStatus.REVIEW]


def test_answering_a_blocked_issue_buys_a_fresh_verdict_that_carries_the_answer(
    tmp_path: Path, fake_binaries: Path
) -> None:
    question = "Which unit does the feed use?"
    policy_path = prepare_weekend(
        tmp_path,
        fake_binaries,
        claude_response(
            assessment_payload("needs_input", "S", "tests", ["unclear_goal"], [question]), 0.02
        ),
    )
    assert prepare_pass(policy_path) == 0
    policy = policy_at(policy_path)
    prepared_run_id = latest_run_id(policy.state_dir)
    assert load_run_state(open_run_directory(policy.state_dir, prepared_run_id)).tasks[
        0
    ].status is (TaskStatus.ASSESSED)
    record_answer(policy.state_dir, "demo", 1, question, "Knots.")
    write_claude_responses(
        fake_binaries,
        {"default": claude_response(assessment_payload("execute", "XS", "tests", [], []), 0.02)},
    )
    write_worker_plan(
        fake_binaries,
        {"logbook/records.py": FIXED_RECORDS},
        delivery_payload("done", "fix(records): treat an empty speed as unknown", []),
        1.2,
        None,
    )
    assessments_before = len(assessor_calls(fake_binaries))

    assert weekend(policy_path) == 0

    fresh = assessor_calls(fake_binaries)[assessments_before:]
    assert len(fresh) == 1
    assert "Knots." in " ".join(fresh[0])
    state = load_run_state(open_run_directory(policy.state_dir, prepared_run_id))
    assert [task.status for task in state.tasks] == [TaskStatus.REVIEW]


def test_a_prepared_triage_nobody_took_up_in_time_is_left_behind(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy_path = prepare_weekend(
        tmp_path,
        fake_binaries,
        claude_response(assessment_payload("execute", "XS", "tests", [], []), 0.02),
    )
    prepare_pass(policy_path)
    policy = policy_at(policy_path)
    prepared = read_prepared(policy.state_dir, "demo")
    assert prepared is not None
    write_prepared(
        policy.state_dir,
        prepared.model_copy(
            update={
                "prepared_at": prepared.prepared_at
                - timedelta(hours=PREPARED_SESSION_MAX_AGE_HOURS + 1)
            }
        ),
    )
    assessments_before = len(assessor_calls(fake_binaries))

    assert weekend(policy_path) == 0

    assert len(assessor_calls(fake_binaries)) == assessments_before + 1


def test_an_issue_edited_after_the_prepare_pass_is_weighed_again(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy_path = prepare_weekend(
        tmp_path,
        fake_binaries,
        claude_response(assessment_payload("execute", "XS", "tests", [], []), 0.02),
    )
    assert prepare_pass(policy_path) == 0
    edited = issue_payload(1, ISSUE_TITLE, ISSUE_BODY, ["weekend:auto"], [])
    edited["updatedAt"] = datetime.now(UTC).isoformat()
    write_github_data(fake_binaries, issues=[edited], pull_requests=[], comments={}, push=True)
    assessments_before = len(assessor_calls(fake_binaries))

    assert weekend(policy_path) == 0

    assert len(assessor_calls(fake_binaries)) == assessments_before + 1
    policy = policy_at(policy_path)
    state = load_run_state(open_run_directory(policy.state_dir, latest_run_id(policy.state_dir)))
    assert any("re-assessed 1" in note for note in state.notes)


@pytest.mark.parametrize(
    "stop_reason",
    [
        StopReason.ALLOWANCE,
        StopReason.FIVE_HOUR_LIMIT,
        StopReason.WINDOW_CLOSED,
        StopReason.ENVELOPE,
    ],
)
def test_a_run_that_ran_out_of_room_still_publishes_what_it_finished(
    tmp_path: Path, stop_reason: StopReason
) -> None:
    run_directory = create_run_directory(tmp_path, RUN_ID)
    finished = build_run_state([], 0.0, [], RUN_ID, "demo", RepoMode.EXECUTE)
    stopped = finished.model_copy(update={"stop_reason": stop_reason})
    assert publishing_allowed(run_directory, stopped) is True
    assert publishing_allowed(run_directory, finished) is True
    assert work_may_follow(run_directory, stopped) is False
    assert work_may_follow(run_directory, finished) is True


def test_an_operator_stop_withholds_publishing(tmp_path: Path) -> None:
    run_directory = create_run_directory(tmp_path, RUN_ID)
    finished = build_run_state([], 0.0, [], RUN_ID, "demo", RepoMode.EXECUTE)
    stopped = finished.model_copy(update={"stop_reason": StopReason.OPERATOR})
    assert publishing_allowed(run_directory, stopped) is False
    request_stop(run_directory.inbox, "enough")
    assert publishing_allowed(run_directory, finished) is False
    assert work_may_follow(run_directory, finished) is False


def test_notes_that_mention_stopping_decide_nothing(tmp_path: Path) -> None:
    run_directory = create_run_directory(tmp_path, RUN_ID)
    noted = build_run_state(
        [], 0.0, ["execution stopped early: budget"], RUN_ID, "demo", RepoMode.EXECUTE
    )
    assert publishing_allowed(run_directory, noted) is True
    assert work_may_follow(run_directory, noted) is True


def test_a_short_window_the_run_waits_out_reaches_the_pull_request(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy_path = prepare_weekend(
        tmp_path,
        fake_binaries,
        claude_response(assessment_payload("execute", "XS", "tests", [], []), 0.02),
    )
    write_probe_sequence(fake_binaries, window_closing_soon(), 0.001)
    assert weekend(policy_path) == 0
    policy = policy_at(policy_path)
    run_directory = open_run_directory(policy.state_dir, latest_run_id(policy.state_dir))
    state = load_run_state(run_directory)
    assert [task.status for task in state.tasks] == [TaskStatus.REVIEW]
    assert "usage_parked" in run_directory.events_path.read_text()
    assert "allowance:" in run_directory.digest_path.read_text()


def posted_question_comment(fake_binaries: Path) -> str:
    for line in (fake_binaries / "gh-bodies.jsonl").read_text().splitlines():
        record = json.loads(line)
        body = record["body"]
        if record["arguments"][:2] == ["issue", "comment"] and isinstance(body, str):
            return body
    raise AssertionError("the prepare pass posted no question comment")


def test_a_reply_on_the_issue_thread_buys_a_fresh_verdict_that_carries_the_answer(
    tmp_path: Path, fake_binaries: Path
) -> None:
    question = "Which unit does the feed use?"
    policy_path = prepare_weekend(
        tmp_path,
        fake_binaries,
        claude_response(
            assessment_payload("needs_input", "S", "tests", ["unclear_goal"], [question]), 0.02
        ),
    )
    assert prepare_pass(policy_path) == 0
    asked_at = datetime.now(UTC)
    write_github_data(
        fake_binaries,
        issues=[
            issue_payload(1, ISSUE_TITLE, ISSUE_BODY, ["weekend:auto", "weekend:needs-input"], [])
        ],
        pull_requests=[],
        comments={
            "1": [
                {
                    "author": {"login": OWNER_LOGIN},
                    "createdAt": asked_at.isoformat(),
                    "body": posted_question_comment(fake_binaries),
                },
                {
                    "author": {"login": OWNER_LOGIN},
                    "createdAt": (asked_at + timedelta(minutes=5)).isoformat(),
                    "body": "1. Knots.",
                },
            ]
        },
        push=True,
    )
    write_claude_responses(
        fake_binaries,
        {"default": claude_response(assessment_payload("execute", "XS", "tests", [], []), 0.02)},
    )
    write_worker_plan(
        fake_binaries,
        {"logbook/records.py": FIXED_RECORDS},
        delivery_payload("done", "fix(records): treat an empty speed as unknown", []),
        1.2,
        None,
    )
    assessments_before = len(assessor_calls(fake_binaries))

    assert weekend(policy_path) == 0

    fresh = assessor_calls(fake_binaries)[assessments_before:]
    assert len(fresh) == 1
    assert "Knots." in " ".join(fresh[0])
    policy = policy_at(policy_path)
    run_directory = open_run_directory(policy.state_dir, latest_run_id(policy.state_dir))
    assert [task.status for task in load_run_state(run_directory).tasks] == [TaskStatus.REVIEW]
    events = run_directory.events_path.read_text()
    assert EventType.ANSWER_RECEIVED.value in events
    assert ANSWERED_REASON in events


def asking_task(questions: list[str]) -> Task:
    return build_task(
        1,
        ISSUE_TITLE,
        TaskStatus.ASSESSED,
        ELIGIBLE,
        build_assessment(Verdict.NEEDS_INPUT, Effort.S, Risk.TESTS, [], questions),
    )


def open_issue(updated_at: datetime) -> Issue:
    payload = issue_payload(1, ISSUE_TITLE, ISSUE_BODY, ["weekend:auto"], [])
    payload["updatedAt"] = updated_at.isoformat()
    return issue_from_payload(payload, [], [])


def test_a_reply_that_settles_no_question_still_earns_a_fresh_verdict(tmp_path: Path) -> None:
    assessed_at = datetime.now(UTC)
    task = asking_task(["Which unit?", "Round or truncate?"])
    briefing = read_briefing(tmp_path, "demo")
    issue = open_issue(assessed_at - timedelta(days=1))

    assert stale_reason(task, issue, assessed_at, briefing, {1}) == REPLIED_REASON
    assert stale_reason(task, issue, assessed_at, briefing, set()) is None


def test_an_answered_issue_that_was_closed_is_left_alone(tmp_path: Path) -> None:
    record_answer(tmp_path, "demo", 1, "Which unit?", "Knots.")
    task = asking_task(["Which unit?"])
    briefing = read_briefing(tmp_path, "demo")

    assert stale_reason(task, None, datetime.now(UTC), briefing, {1}) is None
