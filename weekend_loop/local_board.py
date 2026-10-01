from pathlib import Path
from typing import Final

from weekend_loop.github import (
    pull_requests_by_issue,
    push_refspec,
)
from weekend_loop.guards import assert_branch_allowed, assert_labels_allowed
from weekend_loop.local_github.paths import (
    REPOSITORY_DIRECTORY_NAME,
    issue_url,
    local_repository_path,
    pull_request_url,
)
from weekend_loop.local_github.store import LocalBoard, now_utc, open_board, read_account
from weekend_loop.models import (
    BoardComment,
    BoardIssue,
    BoardPullRequest,
    IdentityPolicy,
    Issue,
    IssueComment,
    IssueState,
    PullRequest,
    RepoTarget,
)
from weekend_loop.workbench import run_git

BOARD_MISSING_HINT: Final[str] = "run `python -m weekend_loop.demo.board --apply` to create it"


def as_pull_request(pull_request: BoardPullRequest, slug: str) -> PullRequest:
    return PullRequest(
        number=pull_request.number,
        title=pull_request.title,
        body=pull_request.body,
        head_branch=pull_request.head_branch,
        author=pull_request.author,
        url=pull_request_url(slug, pull_request.number),
    )


def as_issue(issue: BoardIssue, slug: str, linked: list[int], open_numbers: set[int]) -> Issue:
    return Issue(
        number=issue.number,
        title=issue.title,
        body=issue.body,
        labels=issue.labels,
        assignees=issue.assignees,
        milestone=issue.milestone,
        author=issue.author,
        created_at=issue.created_at,
        updated_at=issue.updated_at,
        url=issue_url(slug, issue.number),
        open_linked_pull_requests=linked,
        last_foreign_activity_at=None,
        blocked_by=[number for number in issue.blocked_by if number in open_numbers],
    )


class LocalBoardReader:
    def __init__(self, board: LocalBoard) -> None:
        self.board = board
        self.slug = board.slug

    def require_board(self) -> LocalBoard:
        if not self.board.exists():
            raise FileNotFoundError(
                f"no local board at {self.board.directory}; {BOARD_MISSING_HINT}"
            )
        return self.board

    def viewer_login(self) -> str:
        return read_account(self.require_board().directory.parent).login

    def open_pull_requests(self, limit: int) -> list[PullRequest]:
        found = self.require_board().open_pull_requests()[:limit]
        return [as_pull_request(pull_request, self.slug) for pull_request in found]

    def open_issues(self, limit: int) -> list[Issue]:
        board = self.require_board()
        linked = pull_requests_by_issue(self.open_pull_requests(limit))
        open_issues = board.open_issues()
        open_numbers = {issue.number for issue in open_issues}
        return [
            as_issue(issue, self.slug, linked.get(issue.number, []), open_numbers)
            for issue in open_issues[:limit]
        ]

    def issue_comments(self, issue_number: int) -> list[IssueComment]:
        return [
            IssueComment(author=comment.author, created_at=comment.created_at, body=comment.body)
            for comment in self.require_board().comments(issue_number)
        ]


class LocalBoardWriter:
    def __init__(
        self,
        repo: RepoTarget,
        board: LocalBoard,
        identity: IdentityPolicy,
        label_namespace: str,
    ) -> None:
        self.repo = repo
        self.board = board
        self.identity = identity
        self.label_namespace = label_namespace

    def assert_remote_is_the_policy_repository(
        self, workbench: Path, environment: dict[str, str]
    ) -> None:
        actual = run_git(
            ["remote", "get-url", "origin"], cwd=workbench, environment=environment
        ).strip()
        expected = str(self.board.directory / REPOSITORY_DIRECTORY_NAME)
        if actual != expected:
            raise ValueError(f"workbench remote is {actual!r}, the board is at {expected!r}")

    def push_branch(
        self, branch: str, prefix: str, workbench: Path, environment: dict[str, str]
    ) -> None:
        assert_branch_allowed(branch, prefix)
        self.assert_remote_is_the_policy_repository(workbench, environment)
        run_git(["push", "origin", push_refspec(branch)], cwd=workbench, environment=environment)

    def open_draft_pull_request(self, branch: str, title: str, body: str, base: str) -> str:
        number = self.board.take_number()
        self.board.write_pull_request(
            BoardPullRequest(
                number=number,
                title=title,
                body=body,
                head_branch=branch,
                base_branch=base,
                author=self.identity.git_author_name,
                draft=True,
                state=IssueState.OPEN,
                created_at=now_utc(),
            )
        )
        return pull_request_url(self.board.slug, number)

    def link_stack(self, pull_request_numbers: list[int]) -> None:
        self.board.link_stack(pull_request_numbers)

    def comment_on_issue(self, issue_number: int, body: str) -> None:
        self.board.append_comment(
            issue_number,
            BoardComment(
                id=self.board.take_comment_id(),
                author=self.identity.git_author_name,
                created_at=now_utc(),
                body=body,
            ),
        )

    def add_labels(self, issue_number: int, labels: list[str]) -> None:
        assert_labels_allowed(labels, self.label_namespace)
        self.board.add_labels(issue_number, labels, now_utc())

    def remove_labels(self, issue_number: int, labels: list[str]) -> None:
        assert_labels_allowed(labels, self.label_namespace)
        self.board.remove_labels(issue_number, labels, now_utc())

    def create_issue(self, title: str, body: str) -> str:
        number = self.board.take_number()
        now = now_utc()
        self.board.write_issue(
            BoardIssue(
                number=number,
                title=title,
                body=body,
                labels=[],
                assignees=[],
                milestone=None,
                author=self.identity.git_author_name,
                created_at=now,
                updated_at=now,
                state=IssueState.OPEN,
            )
        )
        return issue_url(self.board.slug, number)


def local_reader_for(repo: RepoTarget, state_directory: Path) -> LocalBoardReader:
    return LocalBoardReader(open_board(state_directory, repo.slug))


def local_writer_for(
    repo: RepoTarget, state_directory: Path, identity: IdentityPolicy, label_namespace: str
) -> LocalBoardWriter:
    return LocalBoardWriter(repo, open_board(state_directory, repo.slug), identity, label_namespace)


def local_remote_url(repo: RepoTarget, state_directory: Path) -> str:
    return str(local_repository_path(state_directory, repo.slug))
