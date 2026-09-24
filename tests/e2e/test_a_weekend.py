from __future__ import annotations

import pytest

from tests.e2e.conftest import REPO_KEY
from weekend_loop.board import open_board
from weekend_loop.cli import EXIT_OK, main
from weekend_loop.models import RunPhase, TaskStatus, Workspace
from weekend_loop.policy import policy_at
from weekend_loop.questions import is_agent_comment
from weekend_loop.runs import latest_run_id, load_run_state, open_run_directory

pytestmark = pytest.mark.e2e

NEW_CHAT_ISSUE = 1
RATINGS_ISSUE = 3


def weekend(workspace: Workspace) -> int:
    return main(
        ["--home", str(workspace.root), "weekend", "--repo-key", REPO_KEY, "--ignore-window"]
    )


def finished_state(workspace: Workspace) -> tuple[RunPhase, list[TaskStatus]]:
    policy = policy_at(workspace.root)
    run_id = latest_run_id(policy.state_dir)
    assert run_id is not None
    state = load_run_state(open_run_directory(policy.state_dir, run_id))
    return state.phase, [task.status for task in state.tasks]


def test_a_weekend_runs_from_triage_to_a_draft_pull_request_on_the_local_board(
    workspace: Workspace,
) -> None:
    assert weekend(workspace) == EXIT_OK

    phase, statuses = finished_state(workspace)
    assert phase is RunPhase.FINISHED
    assert TaskStatus.REVIEW in statuses

    policy = policy_at(workspace.root)
    board = open_board(policy.state_dir, policy.repos[REPO_KEY].slug)
    branches = [
        pull_request.head_branch
        for pull_request in board.open_pull_requests()
        if pull_request.head_branch.startswith(policy.worker.branch_prefix)
    ]
    assert branches == [
        f"{policy.worker.branch_prefix}{NEW_CHAT_ISSUE}-the-new-chat-button-does-nothing"
    ]


def test_a_weekend_labels_the_issue_it_delivered_and_leaves_the_rest_alone(
    workspace: Workspace,
) -> None:
    assert weekend(workspace) == EXIT_OK

    policy = policy_at(workspace.root)
    board = open_board(policy.state_dir, policy.repos[REPO_KEY].slug)
    delivered = board.read_issue(NEW_CHAT_ISSUE)
    assert policy.labels.review in delivered.labels
    untouched = board.read_issue(RATINGS_ISSUE)
    assert policy.labels.review not in untouched.labels


def test_a_weekend_leaves_a_digest_the_reviewer_can_read(workspace: Workspace) -> None:
    assert weekend(workspace) == EXIT_OK

    policy = policy_at(workspace.root)
    run_id = latest_run_id(policy.state_dir)
    assert run_id is not None
    digest = open_run_directory(policy.state_dir, run_id).digest_path.read_text()
    assert "weekend" in digest.lower()
    board = open_board(policy.state_dir, policy.repos[REPO_KEY].slug)
    posted = [issue for issue in board.open_issues() if is_agent_comment(issue.body)]
    assert len(posted) == 1


def test_a_second_weekend_announces_the_same_pull_request_only_once(
    workspace: Workspace,
) -> None:
    assert weekend(workspace) == EXIT_OK
    policy = policy_at(workspace.root)
    board = open_board(policy.state_dir, policy.repos[REPO_KEY].slug)
    announcements = len(board.comments(NEW_CHAT_ISSUE))
    digests = len([issue for issue in board.open_issues() if is_agent_comment(issue.body)])

    assert weekend(workspace) == EXIT_OK

    board = open_board(policy.state_dir, policy.repos[REPO_KEY].slug)
    assert len(board.comments(NEW_CHAT_ISSUE)) == announcements
    assert (
        len([issue for issue in board.open_issues() if is_agent_comment(issue.body)]) == digests + 1
    )
