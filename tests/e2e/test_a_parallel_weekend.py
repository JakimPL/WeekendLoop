from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.conftest import EXECUTE_ISSUES, REPO_KEY, write_worker_plans_by_issue
from tests.e2e.test_a_weekend import weekend
from weekend_loop.cli import EXIT_OK
from weekend_loop.local_github.store import open_board
from weekend_loop.models import RunPhase, TaskStatus, Workspace
from weekend_loop.policy import policy_at
from weekend_loop.runs import latest_run_id, load_run_state, open_run_directory

pytestmark = pytest.mark.e2e


def test_a_parallel_weekend_works_two_issues_at_once_in_their_own_worktrees(
    parallel_workspace: Workspace,
) -> None:
    assert weekend(parallel_workspace) == EXIT_OK

    policy = policy_at(parallel_workspace.root)
    run_directory = open_run_directory(policy.state_dir, latest_run_id(policy.state_dir))
    state = load_run_state(run_directory)
    assert state.phase is RunPhase.FINISHED
    delivered = [task for task in state.tasks if task.status is TaskStatus.REVIEW]
    assert sorted(task.issue_number for task in delivered) == list(EXECUTE_ISSUES)
    assert [task.stacked_on for task in delivered] == [None, None]

    board = open_board(policy.state_dir, policy.repos[REPO_KEY].slug)
    branches = sorted(
        pull_request.head_branch
        for pull_request in board.open_pull_requests()
        if pull_request.head_branch.startswith(policy.worker.branch_prefix)
    )
    assert branches == sorted(task.branch for task in delivered if task.branch is not None)
    assert not policy.workspace.worktrees_path(REPO_KEY).exists()
    assert "git merges them cleanly" in run_directory.digest_path.read_text()


def test_a_parallel_weekend_stacks_an_issue_on_the_one_github_says_blocks_it(
    parallel_workspace: Workspace, binaries: Path
) -> None:
    write_worker_plans_by_issue(
        binaries,
        {
            EXECUTE_ISSUES[0]: {"pocketchat/chat.py": "GREETING = 'Hello'\n"},
            EXECUTE_ISSUES[1]: {"README.md": "# Pocketchat\n\nSay hello to start.\n"},
        },
    )
    policy = policy_at(parallel_workspace.root)
    board = open_board(policy.state_dir, policy.repos[REPO_KEY].slug)
    second = board.read_issue(EXECUTE_ISSUES[1])
    board.write_issue(second.model_copy(update={"blocked_by": [EXECUTE_ISSUES[0]]}))

    assert weekend(parallel_workspace) == EXIT_OK

    run_directory = open_run_directory(policy.state_dir, latest_run_id(policy.state_dir))
    parent, child = (
        task for task in load_run_state(run_directory).tasks if task.issue_number in EXECUTE_ISSUES
    )
    assert [parent.status, child.status] == [TaskStatus.REVIEW, TaskStatus.REVIEW]
    assert child.stacked_on == parent.issue_number
    opened = {pull_request.head_branch: pull_request for pull_request in board.open_pull_requests()}
    assert parent.branch is not None and child.branch is not None
    assert opened[child.branch].base_branch == parent.branch
    assert board.read_index().stacks == [
        [opened[parent.branch].number, opened[child.branch].number]
    ]
    assert "#1 → #2: each builds on the one before it" in run_directory.digest_path.read_text()
