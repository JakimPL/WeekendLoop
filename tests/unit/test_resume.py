from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.unit.conftest import (
    ELIGIBLE,
    OWNER_LOGIN,
    assessment_payload,
    build_assessment,
    claude_response,
    delivery_payload,
    issue_payload,
    worker_plan,
    worker_result,
    write_claude_responses,
    write_github_data,
    write_worker_plans,
)
from tests.unit.test_execute import FIXED_RECORDS, ISSUE_BODY, ISSUE_TITLE, RUN_ID, prepare
from weekend_loop.backends import repository_token
from weekend_loop.cli import main
from weekend_loop.execute import MAX_TASK_RESUMES, ResumeAction, resume_action
from weekend_loop.models import (
    Delivery,
    Effort,
    GateResult,
    Policy,
    Risk,
    RunKind,
    RunPhase,
    RunState,
    SpecSignals,
    Task,
    TaskStatus,
    Verdict,
)
from weekend_loop.policy import policy_at, repo_target
from weekend_loop.questions import AGENT_MARKER
from weekend_loop.runs import list_run_ids, load_run_state, open_run_directory, save_run_state
from weekend_loop.workbench import (
    branch_name,
    create_task_branch,
    git_environment,
    origin_url,
    prepare_checkout,
    write_askpass_script,
)

FIX = {"logbook/records.py": FIXED_RECORDS}
SESSION = "5a559473-d30b-4fe4-b990-edc20b2c942e"
PULL_REQUEST_URL = "https://github.com/owner/repo/pull/42"


def approved() -> dict[str, Any]:
    return delivery_payload("done", "fix(records): treat an empty speed as unknown", [])


def finished_plan(files: dict[str, str]) -> dict[str, Any]:
    return worker_plan(files, [worker_result(approved(), 0.4, "done")], 0)


def weekend(policy_path: Path) -> int:
    return main(["--home", str(policy_path), "weekend", "--repo-key", "demo"])


def claude_calls(fake_binaries: Path) -> list[list[str]]:
    log = fake_binaries / "claude-calls.jsonl"
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


def worker_calls(fake_binaries: Path) -> list[list[str]]:
    return [call for call in claude_calls(fake_binaries) if "acceptEdits" in call]


def assessor_calls(fake_binaries: Path) -> list[list[str]]:
    return [
        call
        for call in claude_calls(fake_binaries)
        if call[:1] == ["-p"] and "acceptEdits" not in call and not call[1].startswith("Reply with")
    ]


def gh_verbs(fake_binaries: Path) -> list[tuple[str, str]]:
    lines = (fake_binaries / "gh-calls.jsonl").read_text().splitlines()
    return [(call[0], call[1]) for call in (json.loads(line) for line in lines) if len(call) > 1]


def open_weekend_run(policy: Policy, phase: RunPhase, tasks: list[Task]) -> None:
    run_directory = open_run_directory(policy.state_dir, RUN_ID)
    state = load_run_state(run_directory)
    save_run_state(
        run_directory,
        state.model_copy(
            update={
                "phase": phase,
                "kind": RunKind.WEEKEND,
                "deadline_at": datetime.now(UTC) + timedelta(days=1),
                "repo_slug": repo_target(policy, "demo").slug,
                "tasks": tasks,
            }
        ),
    )


def interrupted_on_disk(policy: Policy, task: Task) -> Task:
    repo = repo_target(policy, "demo")
    token = repository_token(repo)
    workbench = prepare_checkout(repo, "demo", policy.workspace, token)
    branch = branch_name(policy.worker.branch_prefix, task.issue_number, ISSUE_TITLE)
    create_task_branch(
        workbench, branch, git_environment(token, write_askpass_script(policy.state_dir))
    )
    (workbench / "logbook" / "records.py").write_text(FIXED_RECORDS)
    return task.model_copy(
        update={"status": TaskStatus.WORKING, "branch": branch, "session_id": SESSION}
    )


def seeded_task(policy: Policy) -> Task:
    return load_run_state(open_run_directory(policy.state_dir, RUN_ID)).tasks[0]


def remember_session(fake_binaries: Path) -> None:
    (fake_binaries / "claude-sessions.txt").write_text(SESSION + "\n")


def latest(policy: Policy) -> RunState:
    return load_run_state(open_run_directory(policy.state_dir, RUN_ID))


@pytest.mark.parametrize(
    ("resumes", "on_branch", "calls_allowed", "budget_left", "recorded", "expected"),
    [
        (0, True, True, 5.0, False, ResumeAction.RESUME_SESSION),
        (0, False, True, 5.0, False, ResumeAction.START_OVER),
        (0, True, True, 5.0, True, ResumeAction.SETTLE),
        (0, True, False, 5.0, False, ResumeAction.SETTLE),
        (MAX_TASK_RESUMES, True, True, 5.0, False, ResumeAction.SETTLE),
        (0, True, True, 0.0, False, ResumeAction.SETTLE),
        (0, False, False, 5.0, False, ResumeAction.ABANDON),
    ],
)
def test_an_interrupted_task_is_resumed_restarted_settled_or_abandoned(
    resumes: int,
    on_branch: bool,
    calls_allowed: bool,
    budget_left: float,
    recorded: bool,
    expected: ResumeAction,
) -> None:
    delivery = Delivery.model_validate(approved()) if recorded else None
    task = Task(
        issue_number=1,
        title=ISSUE_TITLE,
        status=TaskStatus.WORKING,
        eligibility=ELIGIBLE,
        spec_signals=None,
        assessment=build_assessment(Verdict.EXECUTE, Effort.XS, Risk.TESTS, [], []),
        delivery=delivery,
        gate=None,
        branch="weekend/1-empty-speed-field",
        pull_request_url=None,
        session_id=SESSION,
        attempts=1,
        cost_usd=0.1,
        resumes=resumes,
    )
    assert resume_action(task, on_branch, calls_allowed, budget_left) is expected


def test_a_restarted_run_resumes_the_worker_session_it_was_in(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path, fake_binaries, ["weekend:auto"], FIX, approved(), 3, [1]
    )
    working = interrupted_on_disk(policy, seeded_task(policy))
    open_weekend_run(policy, RunPhase.EXECUTE, [working])
    remember_session(fake_binaries)
    write_worker_plans(fake_binaries, [finished_plan({})])
    assert weekend(policy_path) == 0
    state = latest(policy)
    assert [task.status for task in state.tasks] == [TaskStatus.REVIEW]
    assert state.tasks[0].resumes == 1
    assert state.phase is RunPhase.FINISHED
    (resumed,) = worker_calls(fake_binaries)
    assert resumed[resumed.index("--resume") + 1] == SESSION
    assert assessor_calls(fake_binaries) == []
    assert gh_verbs(fake_binaries).count(("pr", "create")) == 1
    assert "run_resumed" in open_run_directory(policy.state_dir, RUN_ID).events_path.read_text()


def test_a_task_whose_session_is_gone_starts_over(tmp_path: Path, fake_binaries: Path) -> None:
    policy, policy_path = prepare(
        tmp_path, fake_binaries, ["weekend:auto"], FIX, approved(), 3, [1]
    )
    working = interrupted_on_disk(policy, seeded_task(policy))
    open_weekend_run(policy, RunPhase.EXECUTE, [working])
    write_worker_plans(fake_binaries, [finished_plan(FIX)])
    assert weekend(policy_path) == 0
    resumed, fresh = worker_calls(fake_binaries)
    assert resumed[resumed.index("--resume") + 1] == SESSION
    assert fresh[fresh.index("--session-id") + 1] != SESSION
    assert latest(policy).tasks[0].status is TaskStatus.REVIEW


def test_a_task_that_keeps_crashing_the_run_is_settled_without_another_call(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path, fake_binaries, ["weekend:auto"], FIX, approved(), 3, [1]
    )
    working = interrupted_on_disk(policy, seeded_task(policy))
    exhausted = working.model_copy(update={"resumes": MAX_TASK_RESUMES})
    open_weekend_run(policy, RunPhase.EXECUTE, [exhausted])
    assert weekend(policy_path) == 0
    assert worker_calls(fake_binaries) == []
    assert latest(policy).tasks[0].status is TaskStatus.UNFINISHED


def delivered_task(policy: Policy) -> Task:
    gate = GateResult(
        passed=True,
        commands=[],
        diff_lines=3,
        forbidden_paths_touched=[],
        secret_matches=[],
        binary_files=[],
        commit_count=1,
    )
    return seeded_task(policy).model_copy(
        update={
            "status": TaskStatus.REVIEW,
            "branch": branch_name(policy.worker.branch_prefix, 1, ISSUE_TITLE),
            "delivery": Delivery.model_validate(approved()),
            "gate": gate,
        }
    )


def test_publishing_after_a_crash_reuses_the_pull_request_and_its_comment(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path, fake_binaries, ["weekend:auto"], FIX, approved(), 3, [1]
    )
    task = delivered_task(policy)
    open_weekend_run(policy, RunPhase.PUBLISH, [task])
    announcement = {
        "author": {"login": OWNER_LOGIN},
        "createdAt": datetime.now(UTC).isoformat(),
        "body": f"A draft pull request is ready for review: {PULL_REQUEST_URL}\n\n"
        f"{AGENT_MARKER}\n— weekend-loop run x\n",
    }
    existing_pull_request = {
        "number": 42,
        "title": "fix(records): treat an empty speed as unknown",
        "body": "Refs #1",
        "headRefName": task.branch,
        "author": {"login": OWNER_LOGIN},
        "url": PULL_REQUEST_URL,
    }
    write_github_data(
        fake_binaries,
        issues=[issue_payload(1, ISSUE_TITLE, ISSUE_BODY, ["weekend:auto"], [])],
        pull_requests=[existing_pull_request],
        comments={"1": [announcement]},
        push=True,
    )
    assert weekend(policy_path) == 0
    verbs = gh_verbs(fake_binaries)
    assert ("pr", "create") not in verbs
    assert ("issue", "comment") not in verbs
    assert verbs.count(("issue", "create")) == 1
    state = latest(policy)
    assert state.tasks[0].pull_request_url == PULL_REQUEST_URL
    assert state.tasks[0].published_at is not None
    assert state.phase is RunPhase.FINISHED


def test_a_crashed_triage_only_pays_for_the_issues_it_had_not_assessed(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path, fake_binaries, ["weekend:auto"], FIX, approved(), 3, [1, 2]
    )
    first, second = load_run_state(open_run_directory(policy.state_dir, RUN_ID)).tasks
    signals = SpecSignals(
        body_length=len(ISSUE_BODY),
        sections_present=[],
        has_template=False,
        referenced_paths=[],
        resolved_paths=[],
        has_acceptance_criteria=False,
    )
    pending = second.model_copy(
        update={"status": TaskStatus.CANDIDATE, "assessment": None, "spec_signals": signals}
    )
    open_weekend_run(policy, RunPhase.TRIAGE, [first, pending])
    write_claude_responses(
        fake_binaries,
        {"default": claude_response(assessment_payload("skip", "L", "interface", [], []), 0.02)},
    )
    write_worker_plans(fake_binaries, [finished_plan(FIX)])
    assert weekend(policy_path) == 0
    assessed = assessor_calls(fake_binaries)
    assert len(assessed) == 1
    assert "issue #2" in assessed[0][1]
    assert latest(policy).tasks[0].status is TaskStatus.REVIEW


def test_a_run_written_before_resume_existed_is_left_alone(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path, fake_binaries, ["weekend:auto"], FIX, approved(), 3, [1]
    )
    run_directory = open_run_directory(policy.state_dir, RUN_ID)
    legacy = load_run_state(run_directory).model_copy(update={"phase": RunPhase.EXECUTE})
    save_run_state(run_directory, legacy)
    write_claude_responses(
        fake_binaries,
        {"default": claude_response(assessment_payload("skip", "L", "interface", [], []), 0.02)},
    )
    assert weekend(policy_path) == 0
    assert len(list_run_ids(policy_at(policy_path).state_dir)) == 2


def exited_or_reaped(pid: int) -> bool:
    try:
        status = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return True
    return status.rsplit(")", 1)[1].split()[0] == "Z"


def process_gone(pid: int) -> bool:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if exited_or_reaped(pid):
            return True
        time.sleep(0.1)
    return False


def test_a_run_killed_mid_task_takes_its_worker_along_and_resumes_on_the_next_start(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path, fake_binaries, ["weekend:auto"], FIX, approved(), 3, [1]
    )
    open_weekend_run(policy, RunPhase.EXECUTE, [seeded_task(policy)])
    killer = {**worker_plan(FIX, [{"type": "assistant", "message": "editing"}], 0)}
    killer.update({"kill_orchestrator": True, "sleep_seconds": 60})
    write_worker_plans(fake_binaries, [killer, finished_plan({})])
    arguments = ["--home", str(policy_path), "weekend", "--repo-key", "demo"]
    killed = subprocess.run(
        [sys.executable, "-m", "weekend_loop.cli", *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert killed.returncode == -signal.SIGKILL
    assert process_gone(int((fake_binaries / "claude-worker.pid").read_text()))
    assert latest(policy).tasks[0].status is TaskStatus.WORKING
    assert weekend(policy_path) == 0
    first, resumed = worker_calls(fake_binaries)
    assert resumed[resumed.index("--resume") + 1] == first[first.index("--session-id") + 1]
    assert latest(policy).tasks[0].status is TaskStatus.REVIEW


def test_a_run_for_a_repository_the_policy_no_longer_names_is_left_alone(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path, fake_binaries, ["weekend:auto"], FIX, approved(), 3, [1]
    )
    open_weekend_run(policy, RunPhase.PUBLISH, [delivered_task(policy)])
    run_directory = open_run_directory(policy.state_dir, RUN_ID)
    moved = load_run_state(run_directory).model_copy(update={"repo_slug": "owner/old-repo"})
    save_run_state(run_directory, moved)
    write_claude_responses(
        fake_binaries,
        {"default": claude_response(assessment_payload("skip", "L", "interface", [], []), 0.02)},
    )
    assert weekend(policy_path) == 0
    assert load_run_state(run_directory).phase is RunPhase.PUBLISH
    assert len(list_run_ids(policy_at(policy_path).state_dir)) == 2


def test_a_checkout_of_another_repository_is_cloned_afresh(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, _ = prepare(tmp_path, fake_binaries, ["weekend:auto"], FIX, approved(), 3, [1])
    repo = repo_target(policy, "demo")
    token = repository_token(repo)
    environment = git_environment(token, write_askpass_script(policy.state_dir))
    workbench = prepare_checkout(repo, "demo", policy.workspace, token)
    run_git_in(
        workbench, ["remote", "set-url", "origin", "https://example.com/old.git"], environment
    )
    (workbench / "stale.txt").write_text("left by the old repository")
    prepare_checkout(repo, "demo", policy.workspace, token)
    assert origin_url(workbench, environment) == repo.remote_url
    assert not (workbench / "stale.txt").exists()


def run_git_in(workbench: Path, arguments: list[str], environment: dict[str, str]) -> None:
    subprocess.run(["git", *arguments], cwd=workbench, env=environment, check=True)
