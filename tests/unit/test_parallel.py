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
    worker_plan,
    worker_result,
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
    amend_demo_repo,
    build_on_setup,
    build_origin,
    execute,
    run_git,
    workbench_of,
)
from tests.unit.test_publish import gh_calls, publish
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
    Overlap,
    Policy,
    RepoMode,
    Risk,
    RunPhase,
    RunState,
    StopReason,
    TaskStatus,
    Verdict,
)
from weekend_loop.policy import policy_at, repo_target
from weekend_loop.publish import pull_request_title
from weekend_loop.report import render_digest
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
PER_TASK_USD: Final[float] = 6.0
GATES_AT_ONCE: Final[int] = 1
SLUG: Final[str] = "example-org/example-board"
RECORDS_PATH: Final[str] = "logbook/records.py"
README_PATH: Final[str] = "README.md (new)"
README_FILE: Final[str] = "README.md"
FIX_SUBJECT: Final[str] = "fix(records): treat an empty speed as unknown"
CHANGELOG_PATH: Final[str] = "CHANGELOG.md"
NOTE_SETUP: Final[str] = """#!/usr/bin/env python3
import os
from pathlib import Path

with (Path(__file__).resolve().parent / "setup-runs.txt").open("a") as log:
    log.write(os.getcwd() + "\\n")
"""


TIMED_GATE: Final[str] = """#!/usr/bin/env python3
import time
from pathlib import Path

log = Path(__file__).resolve().parent / "gate-times.txt"
started = time.monotonic()
time.sleep(0.4)
with log.open("a") as times:
    times.write(f"{started} {time.monotonic()}\\n")
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
    raw["budget"]["per_task_usd"] = PER_TASK_USD
    raw["resources"] = {"gates_at_once": GATES_AT_ONCE}
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


def write_plans_by_issue(fake_binaries: Path, files_by_issue: dict[int, dict[str, str]]) -> None:
    delivery = delivery_payload("done", FIX_SUBJECT, [])
    plans = {
        str(number): worker_plan(files, [worker_result(delivery, 1.2, "done")], 0)
        for number, files in files_by_issue.items()
    }
    fallback = next(iter(plans.values()))
    (fake_binaries / "claude-worker.json").write_text(json.dumps({**plans, "default": fallback}))


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


def test_tasks_work_side_by_side_each_on_its_own_worktree(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare_parallel(
        tmp_path, fake_binaries, {1: [RECORDS_PATH], 2: [README_PATH]}, 2, [], 3, 15.0
    )
    write_plans_by_issue(
        fake_binaries, {1: {RECORDS_PATH: FIXED_RECORDS}, 2: {README_FILE: "# Logbook\n"}}
    )
    assert execute(policy_path) == 0
    state = latest(policy)
    assert [task.status for task in state.tasks] == [TaskStatus.REVIEW, TaskStatus.REVIEW]
    assert [task.stacked_on for task in state.tasks] == [None, None]
    assert [task.overlaps for task in state.tasks] == [[], []]
    assert [task.gate.changed_paths for task in state.tasks if task.gate is not None] == [
        [RECORDS_PATH],
        [README_FILE],
    ]
    assert len(worker_calls(fake_binaries)) == 2
    run_directory = open_run_directory(policy.state_dir, RUN_ID)
    first_task = (run_directory.task_directory(1) / "TASK.md").read_text()
    second_task = (run_directory.task_directory(2) / "TASK.md").read_text()
    assert f"Other tasks of this run change: #2: {README_FILE}." in first_task
    assert f"Other tasks of this run change: #1: {RECORDS_PATH}." in second_task

    worktrees = policy.workspace.worktrees_path(REPO_KEY)
    assert not worktrees.exists()
    bench = workbench_of(policy)
    assert run_git(["rev-parse", "--abbrev-ref", "HEAD"], bench).strip() == "main"
    assert run_git(["status", "--porcelain"], bench).strip() == ""
    branches = [task.branch for task in state.tasks if task.branch is not None]
    assert [branch_commits(policy, branch) for branch in branches] == [1, 1]
    baseline, *tasks = setup_runs(fake_binaries)
    assert baseline == bench.resolve()
    assert sorted(tasks) == sorted((worktrees / branch).resolve() for branch in branches)
    ledger = (policy.state_dir / "ledger.jsonl").read_text().splitlines()
    assert sorted(json.loads(line)["issue_number"] for line in ledger) == [1, 2]


def test_what_setup_builds_in_each_worktree_reaches_its_gate(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare_parallel(
        tmp_path, fake_binaries, {1: [RECORDS_PATH], 2: [README_PATH]}, 2, [], 3, 15.0
    )
    write_plans_by_issue(
        fake_binaries, {1: {RECORDS_PATH: FIXED_RECORDS}, 2: {README_FILE: "# Logbook\n"}}
    )
    build_on_setup(tmp_path, fake_binaries, policy_path)
    assert execute(policy_path) == 0
    assert [task.status for task in latest(policy).tasks] == [TaskStatus.REVIEW] * 2


def test_a_task_whose_checkout_breaks_ends_alone_and_its_sibling_carries_on(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare_parallel(
        tmp_path, fake_binaries, {1: [RECORDS_PATH], 2: [README_PATH]}, 2, [], 3, 15.0
    )
    delivery = delivery_payload("done", FIX_SUBJECT, [])
    broken = worker_plan({RECORDS_PATH: FIXED_RECORDS}, [worker_result(delivery, 1.2, "done")], 0)
    sound = worker_plan({README_FILE: "# Logbook\n"}, [worker_result(delivery, 1.2, "done")], 0)
    (fake_binaries / "claude-worker.json").write_text(
        json.dumps({"1": {**broken, "remove": [".git"]}, "2": sound, "default": sound})
    )
    assert execute(policy_path) == 0
    state = latest(policy)
    assert [task.status for task in state.tasks] == [TaskStatus.ABANDONED, TaskStatus.REVIEW]
    assert any(
        event.startswith("task_failed: git status --porcelain failed with exit 128")
        for event in events_of(policy)
    )
    ledger = (policy.state_dir / "ledger.jsonl").read_text().splitlines()
    assert sorted(json.loads(line)["issue_number"] for line in ledger) == [1, 2]
    assert not policy.workspace.worktrees_path(REPO_KEY).exists()


def test_gates_of_tasks_working_side_by_side_take_turns(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare_parallel(
        tmp_path, fake_binaries, {1: [RECORDS_PATH], 2: [README_PATH]}, 2, [], 3, 15.0
    )
    write_plans_by_issue(
        fake_binaries, {1: {RECORDS_PATH: FIXED_RECORDS}, 2: {README_FILE: "# Logbook\n"}}
    )
    install_fake(fake_binaries, "timed-gate", TIMED_GATE)
    amend_demo_repo(policy_path, {"gate_commands": ["timed-gate"]})
    assert execute(policy_path) == 0
    assert [task.status for task in latest(policy).tasks] == [TaskStatus.REVIEW] * 2
    spans = sorted(
        tuple(float(moment) for moment in line.split())
        for line in (fake_binaries / "gate-times.txt").read_text().splitlines()
    )
    assert len(spans) == 3
    assert all(earlier[1] <= later[0] for earlier, later in zip(spans, spans[1:], strict=False))


def test_tasks_on_the_same_file_run_side_by_side_and_note_a_clean_merge(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare_parallel(
        tmp_path, fake_binaries, {1: [RECORDS_PATH], 2: ["logbook"]}, 2, [], 3, 15.0
    )
    assert execute(policy_path) == 0
    state = latest(policy)
    assert [task.status for task in state.tasks] == [TaskStatus.REVIEW, TaskStatus.REVIEW]
    assert state.tasks[0].overlaps == [
        Overlap(issue_number=2, paths=[RECORDS_PATH], merges_cleanly=True)
    ]
    assert pull_request_title(state.tasks[0]) == FIX_SUBJECT
    assert not policy.workspace.worktrees_path(REPO_KEY).exists()


def test_a_task_on_a_shared_path_or_without_paths_runs_beside_the_others(
    tmp_path: Path, fake_binaries: Path
) -> None:
    touched = {1: [RECORDS_PATH], 2: [README_PATH], 3: [CHANGELOG_PATH], 4: []}
    policy, policy_path = prepare_parallel(
        tmp_path, fake_binaries, touched, 3, [CHANGELOG_PATH], 4, 30.0
    )
    write_plans_by_issue(
        fake_binaries,
        {
            1: {RECORDS_PATH: FIXED_RECORDS},
            2: {README_FILE: "# Logbook\n"},
            3: {CHANGELOG_PATH: "- noted\n"},
            4: {"logbook/units.py": "KNOTS = 1.0\n"},
        },
    )
    assert execute(policy_path) == 0
    state = latest(policy)
    assert [task.status for task in state.tasks] == [TaskStatus.REVIEW] * 4
    started = [event for event in events_of(policy) if event.startswith("task_started")]
    finished = [event for event in events_of(policy) if event.startswith("task_finished")]
    assert len(started) == len(finished) == 4


def test_a_task_starts_only_while_the_budget_covers_it_beside_those_running(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare_parallel(
        tmp_path, fake_binaries, {1: [RECORDS_PATH], 2: [README_PATH]}, 2, [], 3, 7.0
    )
    assert execute(policy_path) == 0
    state = latest(policy)
    assert [task.status for task in state.tasks] == [TaskStatus.REVIEW, TaskStatus.ASSESSED]
    assert state.stop_reason is StopReason.ENVELOPE
    assert len(worker_calls(fake_binaries)) == 1


def test_a_stop_request_ends_the_run_before_any_task_starts(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare_parallel(
        tmp_path, fake_binaries, {1: [RECORDS_PATH], 2: [README_PATH]}, 2, [], 3, 15.0
    )
    request_stop(open_run_directory(policy.state_dir, RUN_ID).inbox, "the reviewer said so")
    assert execute(policy_path) == 0
    state = latest(policy)
    assert [task.branch for task in state.tasks] == [None, None]
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
    (resumed,) = worker_calls(fake_binaries)
    assert resumed[resumed.index("--resume") + 1] == SESSION
    assert not worktree.exists()
    assert run_git(["rev-parse", "--abbrev-ref", "HEAD"], workbench).strip() == "main"
    diff = (
        open_run_directory(policy.state_dir, RUN_ID).task_directory(1) / "diff.patch"
    ).read_text()
    assert "strip()" in diff


def test_branches_git_cannot_merge_are_flagged_for_the_reviewer(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare_parallel(
        tmp_path, fake_binaries, {1: [RECORDS_PATH], 2: [README_PATH]}, 2, [], 3, 15.0
    )
    write_plans_by_issue(
        fake_binaries,
        {
            1: {RECORDS_PATH: FIXED_RECORDS},
            2: {RECORDS_PATH: "def speed(value: str) -> int:\n    return int(value or 0)\n"},
        },
    )
    assert execute(policy_path) == 0
    state = latest(policy)
    first, second = state.tasks
    assert [task.status for task in state.tasks] == [TaskStatus.REVIEW, TaskStatus.REVIEW]
    assert first.overlaps == [Overlap(issue_number=2, paths=[RECORDS_PATH])]
    assert second.overlaps == [Overlap(issue_number=1, paths=[RECORDS_PATH])]
    detail = f"#1 and #2 both changed {RECORDS_PATH}, and git cannot merge them on its own"
    assert f"overlap_found: {detail}" in events_of(policy)
    digest = render_digest(state, SLUG)
    assert f"- #1 and #2 both changed {RECORDS_PATH}: git cannot merge them" in digest
    assert pull_request_title(first) == f"{FIX_SUBJECT} [merge care]"
    assert pull_request_title(second) == f"{FIX_SUBJECT} [merge care]"


def test_a_file_the_operator_shares_is_no_reason_for_care(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare_parallel(
        tmp_path, fake_binaries, {1: [RECORDS_PATH], 2: [README_PATH]}, 2, ["CHANGELOG.md"], 3, 15.0
    )
    write_plans_by_issue(
        fake_binaries,
        {
            1: {RECORDS_PATH: FIXED_RECORDS, "CHANGELOG.md": "- fixed the speed\n"},
            2: {README_FILE: "# Logbook\n", "CHANGELOG.md": "- wrote the readme\n"},
        },
    )
    assert execute(policy_path) == 0
    state = latest(policy)
    assert [task.status for task in state.tasks] == [TaskStatus.REVIEW, TaskStatus.REVIEW]
    assert [task.overlaps for task in state.tasks] == [[], []]
    assert not any(event.startswith("overlap_found") for event in events_of(policy))


def test_a_branch_the_gate_refused_is_no_partner_for_merge_care(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare_parallel(
        tmp_path, fake_binaries, {1: [RECORDS_PATH], 2: [README_PATH]}, 2, [], 3, 15.0
    )
    write_plans_by_issue(
        fake_binaries,
        {
            1: {RECORDS_PATH: FIXED_RECORDS},
            2: {RECORDS_PATH: FIXED_RECORDS, ".github/workflows/evil.yml": "on: push\n"},
        },
    )
    assert execute(policy_path) == 0
    state = latest(policy)
    assert [task.status for task in state.tasks] == [TaskStatus.REVIEW, TaskStatus.ABANDONED]
    assert [task.overlaps for task in state.tasks] == [[], []]


def depend(policy: Policy, child: int, parent: int) -> None:
    run_directory = open_run_directory(policy.state_dir, RUN_ID)
    state = load_run_state(run_directory)
    tasks = [
        task.model_copy(
            update={"assessment": task.assessment.model_copy(update={"depends_on": [parent]})}
        )
        if task.issue_number == child and task.assessment is not None
        else task
        for task in state.tasks
    ]
    save_run_state(run_directory, state.model_copy(update={"tasks": tasks}))


def test_a_freed_slot_starts_the_next_task_without_waiting_for_the_slowest(
    tmp_path: Path, fake_binaries: Path
) -> None:
    touched = {1: [RECORDS_PATH], 2: [README_PATH], 3: ["logbook/units.py"]}
    policy, policy_path = prepare_parallel(tmp_path, fake_binaries, touched, 2, [], 3, 30.0)
    delivery = delivery_payload("done", FIX_SUBJECT, [])
    plans = {
        "1": {
            **worker_plan({RECORDS_PATH: FIXED_RECORDS}, [worker_result(delivery, 1.2, "done")], 0),
            "sleep_seconds": 3,
        },
        "2": worker_plan({README_FILE: "# Logbook\n"}, [worker_result(delivery, 1.2, "done")], 0),
        "3": worker_plan(
            {"logbook/units.py": "KNOTS = 1.0\n"}, [worker_result(delivery, 1.2, "done")], 0
        ),
    }
    (fake_binaries / "claude-worker.json").write_text(json.dumps({**plans, "default": plans["2"]}))
    assert execute(policy_path) == 0
    assert [task.status for task in latest(policy).tasks] == [TaskStatus.REVIEW] * 3
    events = events_of(policy)
    third_started = next(
        i for i, event in enumerate(events) if event.startswith("task_started") and "3-" in event
    )
    first_finished = next(
        i
        for i, event in enumerate(events)
        if event == "task_finished: review on weekend/1-empty-speed-field"
    )
    assert third_started < first_finished


def test_an_issue_that_builds_on_another_starts_from_its_branch(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare_parallel(
        tmp_path, fake_binaries, {1: [RECORDS_PATH], 2: [README_PATH]}, 2, [], 3, 15.0
    )
    write_plans_by_issue(
        fake_binaries, {1: {RECORDS_PATH: FIXED_RECORDS}, 2: {README_FILE: "# Logbook\n"}}
    )
    depend(policy, 2, 1)
    assert execute(policy_path) == 0
    parent, child = latest(policy).tasks
    assert [parent.status, child.status] == [TaskStatus.REVIEW, TaskStatus.REVIEW]
    assert child.stacked_on == 1
    assert child.base_branch == parent.branch
    assert parent.branch is not None and child.branch is not None
    run_git(["merge-base", "--is-ancestor", parent.branch, child.branch], workbench_of(policy))
    assert child.gate is not None and child.gate.changed_paths == [README_FILE]
    assert child.overlaps == []
    task_record = (
        open_run_directory(policy.state_dir, RUN_ID).task_directory(2) / "TASK.md"
    ).read_text()
    assert f"(based on {parent.branch})" in task_record
    assert "Builds on: #1 Empty speed field, whose branch is your base" in task_record
    assert "#1 → #2: each builds on the one before it" in render_digest(latest(policy), SLUG)


def test_a_child_whose_parent_fails_waits_for_a_later_run(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare_parallel(
        tmp_path, fake_binaries, {1: [RECORDS_PATH], 2: [README_PATH]}, 2, [], 3, 15.0
    )
    write_plans_by_issue(
        fake_binaries,
        {
            1: {RECORDS_PATH: FIXED_RECORDS, ".github/workflows/evil.yml": "on: push\n"},
            2: {README_FILE: "# Logbook\n"},
        },
    )
    depend(policy, 2, 1)
    assert execute(policy_path) == 0
    parent, child = latest(policy).tasks
    assert [parent.status, child.status] == [TaskStatus.ABANDONED, TaskStatus.ASSESSED]
    assert (
        "task_skipped: its parent #1 did not reach review in this run, so it waits for a later one"
        in events_of(policy)
    )
    assert len(worker_calls(fake_binaries)) == 1


def test_a_stack_goes_out_parent_first_with_the_child_on_its_parents_branch(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare_parallel(
        tmp_path, fake_binaries, {1: [RECORDS_PATH], 2: [README_PATH]}, 2, [], 3, 15.0
    )
    write_plans_by_issue(
        fake_binaries, {1: {RECORDS_PATH: FIXED_RECORDS}, 2: {README_FILE: "# Logbook\n"}}
    )
    depend(policy, 2, 1)
    assert execute(policy_path) == 0
    assert publish(policy_path) == 0
    parent, child = latest(policy).tasks
    created = [call for call in gh_calls(fake_binaries) if call[:2] == ["pr", "create"]]
    assert [call[call.index("--head") + 1] for call in created] == [parent.branch, child.branch]
    assert [call[call.index("--base") + 1] for call in created] == ["main", parent.branch]
    assert [parent.pull_request_url, child.pull_request_url] == [
        "https://github.com/owner/repo/pull/42",
        "https://github.com/owner/repo/pull/43",
    ]
    stacks = (fake_binaries / "gh-stacks.jsonl").read_text().splitlines()
    assert [json.loads(line) for line in stacks] == [{"pull_requests": [42, 43]}]
    assert "stack_linked: #1 → #2 form a stack on GitHub" in events_of(policy)
