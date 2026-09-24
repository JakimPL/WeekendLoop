from __future__ import annotations

import pytest

from tests.contract.conftest import ISSUE_TITLE, BoardFixture

# The GitHub half runs against a fake `gh`: it pins the call shape and the parsing, not GitHub's
# own behaviour. What it proves is that both backends keep the same contract.

ISSUE_NUMBER = 1
COMMENT = "A question from the agent."
BRANCH = "weekend/1-empty-speed-field"


def test_an_open_issue_comes_back_with_its_title_and_labels(board: BoardFixture) -> None:
    issues = board.reader.open_issues(100)
    assert [issue.number for issue in issues] == [ISSUE_NUMBER]
    assert issues[0].title == ISSUE_TITLE
    assert "enhancement" in issues[0].labels


def test_the_board_names_the_account_it_speaks_as(board: BoardFixture) -> None:
    assert board.reader.viewer_login()


def test_a_label_the_agent_writes_is_readable_and_removable(board: BoardFixture) -> None:
    review = board.policy.labels.review
    board.writer.add_labels(ISSUE_NUMBER, [review])
    assert review in board.labels_of(ISSUE_NUMBER)
    board.writer.remove_labels(ISSUE_NUMBER, [review])
    assert review not in board.labels_of(ISSUE_NUMBER)


def test_removing_a_label_that_is_not_there_is_no_error(board: BoardFixture) -> None:
    board.writer.remove_labels(ISSUE_NUMBER, [board.policy.labels.review])
    assert board.policy.labels.review not in board.labels_of(ISSUE_NUMBER)


def test_a_label_outside_the_namespace_is_refused_by_either_board(board: BoardFixture) -> None:
    with pytest.raises(ValueError, match=board.policy.labels.namespace):
        board.writer.add_labels(ISSUE_NUMBER, ["priority:high"])


def test_a_comment_the_agent_writes_comes_back_out(board: BoardFixture) -> None:
    board.writer.comment_on_issue(ISSUE_NUMBER, COMMENT)
    assert COMMENT in board.comments_of(ISSUE_NUMBER)


def test_a_draft_pull_request_answers_with_a_link_to_itself(board: BoardFixture) -> None:
    url = board.writer.open_draft_pull_request(BRANCH, "weekend: fix the parser", "Refs #1")
    assert "/pull/" in url


def test_an_issue_the_agent_opens_answers_with_a_link_to_itself(board: BoardFixture) -> None:
    url = board.writer.create_issue("Weekend digest", "What the run did.")
    assert "/issues/" in url


def test_a_branch_outside_the_prefix_never_reaches_the_board(board: BoardFixture) -> None:
    with pytest.raises(ValueError, match="prefix"):
        board.writer.push_branch(
            "main", board.policy.worker.branch_prefix, board.policy.state_dir, {}
        )
