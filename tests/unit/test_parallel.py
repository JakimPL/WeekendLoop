from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from tests.support.fakes import install_fake
from tests.unit.conftest import (
    ELIGIBLE,
    base_policy,
    build_assessment,
    build_run_state,
    build_task,
    delivery_payload,
    issue_payload,
    write_github_data,
    write_policy,
    write_worker_plan,
    write_worker_plans,
)
from tests.unit.test_execute import (
    FIXED_RECORDS,
    ISSUE_BODY,
    ISSUE_TITLE,
    RUN_ID,
    build_origin,
    execute,
    run_git,
    workbench_of,
)
from tests.unit.test_resume import (
    SESSION,
    finished_plan,
    open_weekend_run,
    remember_session,
    weekend,
    worker_calls,
)
from weekend_loop.backends import repository_token
from weekend_loop.mailbox import request_stop
from weekend_loop.models import (
    Effort,
    Policy,
    RepoMode,
    Risk,
    RunPhase,
    RunState,
    SoloReason,
    StopReason,
    TaskStatus,
    Verdict,
)
from weekend_loop.policy import policy_at, repo_target
from weekend_loop.runs import (
    create_run_directory,
    load_run_state,
    open_run_directory,
    save_run_state,
)
from weekend_loop.workbench import (
    add_worktree,
    base_reference,
    branch_name,
    git_environment,
    prepare_checkout,
    write_askpass_script,
)

REPO_KEY: Final[str] = "demo"
RECORDS_PATH: Final[str] = "logbook/records.py"
README_PATH: Final[str] = "README.md (new)"
CHANGELOG_PATH: Final[str] = "CHANGELOG.md"
NOTE_SETUP: Final[str] = """#!/usr/bin/env python3
import os
from pathlib import Path

with (Path(__file__).resolve().parent / "setup-runs.txt").open("a") as log:
    log.write(os.getcwd() + "\\n")
"""


def write_parallel_policy(
    tmp_path: Path,
    remote_url: str,
    parallel: int,
    shared_paths: list[str],
    max_tasks: int,
    envelope_usd: float,
) -> Path:
    raw = base_policy(tmp_path)
    raw["budget"]["weekly_reset_at"] = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    raw["budget"]["max_tasks"] = max_tasks
    raw["budget"]["envelope_usd"] = envelope_usd
    raw["worker"]["parallel"] = parallel
    raw["worker"]["shared_paths"] = shared_paths
    demo = raw["repos"][REPO_KEY]
    demo["remote_url"] = remote_url
    demo["setup_commands"] = ["note-setup"]
    demo["acceptance_command"] = None
    demo["gate_commands"] = ["python3 -m compileall -q {changed_python_files}"]
    return write_policy(tmp_path, raw)


def seed_run(policy: Policy, touched: dict[int, list[str]]) -> None:
    tasks = [
        build_task(
            number,
            ISSUE_TITLE,
            TaskStatus.ASSESSED,
            ELIGIBLE,
            build_assessment(Verdict.EXECUTE, Effort.XS, Risk.TESTS, [], []).model_copy(
                update={"touched_paths": paths}
            ),
        )
        for number, paths in touched.items()
    ]
    run_directory = create_run_directory(policy.state_dir, RUN_ID)
    save_run_state(
        run_directory, build_run_state(tasks, 0.0, [], RUN_ID, REPO_KEY, RepoMode.EXECUTE)
    )


def prepare_parallel(
    tmp_path: Path,
    fake_binaries: Path,
    touched: dict[int, list[str]],
    parallel: int,
    shared_paths: list[str],
    max_tasks: int,
    envelope_usd: float,
) -> tuple[Policy, Path]:
    install_fake(fake_binaries, "note-setup", NOTE_SETUP)
    policy_path = write_parallel_policy(
        tmp_path, build_origin(tmp_path), parallel, shared_paths, max_tasks, envelope_usd
    )
    write_github_data(
        fake_binaries,
        issues=[
            issue_payload(number, ISSUE_TITLE, ISSUE_BODY, ["weekend:auto"], [])
            for number in touched
        ],
        pull_requests=[],
        comments={},
        push=True,
    )
    write_worker_plan(
        fake_binaries,
        {RECORDS_PATH: FIXED_RECORDS},
        delivery_payload("done", "fix(records): treat an empty speed as unknown", []),
        1.2,
        None,
    )
    policy = policy_at(policy_path)
    seed_run(policy, touched)
    return policy, policy_path


def latest(policy: Policy) -> RunState:
    return load_run_state(open_run_directory(policy.state_dir, RUN_ID))


def events_of(policy: Policy) -> list[str]:
    lines = open_run_directory(policy.state_dir, RUN_ID).events_path.read_text().splitlines()
    return [f"{json.loads(line)['event']}: {json.loads(line)['detail']}" for line in lines]


def setup_runs(fake_binaries: Path) -> list[Path]:
    return [
        Path(line).resolve() for line in (fake_binaries / "setup-runs.txt").read_text().splitlines()
    ]


def branch_commits(policy: Policy, branch: str) -> int:
    counted = run_git(["rev-list", "--count", f"origin/main..{branch}"], workbench_of(policy))
    return int(counted.strip())


def test_tasks_with_disjoint_paths_share_a_wave_each_on_its_own_worktree(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare_parallel(
        tmp_path, fake_binaries, {1: [RECORDS_PATH], 2: [README_PATH]}, 2, [], 3, 15.0
    )
    assert execute(policy_path) == 0
    state = latest(policy)
    assert [task.status for task in state.tasks] == [TaskStatus.REVIEW, TaskStatus.REVIEW]
    assert [task.wave for task in state.tasks] == [1, 1]
    assert [task.solo_reason for task in state.tasks] == [None, None]
    assert len(worker_calls(fake_binaries)) == 2
    assert "wave_started: wave 1: #1, #2" in events_of(policy)

    worktrees = policy.workspace.worktrees_path(REPO_KEY)
    assert not worktrees.exists()
    bench = workbench_of(policy)
    assert run_git(["rev-parse", "--abbrev-ref", "HEAD"], bench).strip() == "main"
    assert run_git(["status", "--porcelain"], bench).strip() == ""
    branches = [task.branch for task in state.tasks if task.branch is not None]
    assert [branch_commits(policy, branch) for branch in branches] == [1, 1]
    runs = setup_runs(fake_binaries)
    assert runs[0] == bench.resolve()
    assert sorted(runs[1:]) == sorted((worktrees / branch).resolve() for branch in branches)
    ledger = (policy.state_dir / "ledger.jsonl").read_text().splitlines()
    assert sorted(json.loads(line)["issue_number"] for line in ledger) == [1, 2]


def test_tasks_that_overlap_take_turns_in_separate_waves(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare_parallel(
        tmp_path, fake_binaries, {1: [RECORDS_PATH], 2: ["logbook"]}, 2, [], 3, 15.0
    )
    assert execute(policy_path) == 0
    state = latest(policy)
    assert [task.status for task in state.tasks] == [TaskStatus.REVIEW, TaskStatus.REVIEW]
    assert [task.wave for task in state.tasks] == [1, 2]
    events = events_of(policy)
    assert "wave_started: wave 1: #1" in events
    assert "wave_started: wave 2: #2" in events
    assert not policy.workspace.worktrees_path(REPO_KEY).exists()


def test_a_task_on_a_shared_path_or_without_paths_runs_alone_and_says_why(
    tmp_path: Path, fake_binaries: Path
) -> None:
    touched = {1: [RECORDS_PATH], 2: [README_PATH], 3: [CHANGELOG_PATH], 4: []}
    policy, policy_path = prepare_parallel(
        tmp_path, fake_binaries, touched, 3, [CHANGELOG_PATH], 4, 30.0
    )
    assert execute(policy_path) == 0
    state = latest(policy)
    assert [task.status for task in state.tasks] == [TaskStatus.REVIEW] * 4
    assert [task.wave for task in state.tasks] == [1, 1, 2, 3]
    assert [task.solo_reason for task in state.tasks] == [
        None,
        None,
        SoloReason.SHARED_PATH,
        SoloReason.NO_TOUCHED_PATHS,
    ]


def test_a_wave_starts_only_with_the_budget_for_every_task_in_it(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare_parallel(
        tmp_path, fake_binaries, {1: [RECORDS_PATH], 2: [README_PATH]}, 2, [], 3, 7.0
    )
    assert execute(policy_path) == 0
    state = latest(policy)
    assert [task.status for task in state.tasks] == [TaskStatus.REVIEW, TaskStatus.ASSESSED]
    assert [task.wave for task in state.tasks] == [1, None]
    assert state.stop_reason is StopReason.ENVELOPE
    assert len(worker_calls(fake_binaries)) == 1


def test_a_stop_request_ends_the_run_before_a_wave_starts(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare_parallel(
        tmp_path, fake_binaries, {1: [RECORDS_PATH], 2: [README_PATH]}, 2, [], 3, 15.0
    )
    request_stop(open_run_directory(policy.state_dir, RUN_ID).inbox, "the reviewer said so")
    assert execute(policy_path) == 0
    state = latest(policy)
    assert [task.branch for task in state.tasks] == [None, None]
    assert [task.wave for task in state.tasks] == [None, None]
    assert state.stop_reason is StopReason.OPERATOR
    assert worker_calls(fake_binaries) == []


def test_a_task_interrupted_in_its_worktree_resumes_there(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare_parallel(
        tmp_path, fake_binaries, {1: [RECORDS_PATH]}, 2, [], 3, 15.0
    )
    repo = repo_target(policy, REPO_KEY)
    token = repository_token(repo)
    git_settings = git_environment(token, write_askpass_script(policy.state_dir))
    workbench = prepare_checkout(repo, REPO_KEY, policy.workspace, token)
    branch = branch_name(policy.worker.branch_prefix, 1, ISSUE_TITLE)
    worktree = policy.workspace.worktree_path(REPO_KEY, branch)
    add_worktree(workbench, worktree, branch, base_reference(repo), git_settings)
    (worktree / "logbook" / "records.py").write_text(FIXED_RECORDS)
    working = (
        latest(policy)
        .tasks[0]
        .model_copy(
            update={
                "status": TaskStatus.WORKING,
                "branch": branch,
                "session_id": SESSION,
                "wave": 1,
            }
        )
    )
    open_weekend_run(policy, RunPhase.EXECUTE, [working])
    remember_session(fake_binaries)
    write_worker_plans(fake_binaries, [finished_plan({})])

    assert weekend(policy_path) == 0

    state = latest(policy)
    assert [task.status for task in state.tasks] == [TaskStatus.REVIEW]
    assert state.tasks[0].resumes == 1
    assert state.tasks[0].wave == 1
    (resumed,) = worker_calls(fake_binaries)
    assert resumed[resumed.index("--resume") + 1] == SESSION
    assert not worktree.exists()
    assert run_git(["rev-parse", "--abbrev-ref", "HEAD"], workbench).strip() == "main"
    diff = (
        open_run_directory(policy.state_dir, RUN_ID).task_directory(1) / "diff.patch"
    ).read_text()
    assert "strip()" in diff
