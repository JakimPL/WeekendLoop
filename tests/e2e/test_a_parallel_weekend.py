from __future__ import annotations

import pytest

from tests.e2e.conftest import EXECUTE_ISSUES, REPO_KEY
from tests.e2e.test_a_weekend import weekend
from weekend_loop.board import open_board
from weekend_loop.cli import EXIT_OK
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
    assert [task.wave for task in delivered] == [1, 1]
    assert [task.solo_reason for task in delivered] == [None, None]

    board = open_board(policy.state_dir, policy.repos[REPO_KEY].slug)
    branches = sorted(
        pull_request.head_branch
        for pull_request in board.open_pull_requests()
        if pull_request.head_branch.startswith(policy.worker.branch_prefix)
    )
    assert branches == sorted(task.branch for task in delivered if task.branch is not None)
    assert not policy.workspace.worktrees_path(REPO_KEY).exists()
    assert "- wave 1: #1, #2" in run_directory.digest_path.read_text()
