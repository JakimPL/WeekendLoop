import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from weekend_loop.local_github.paths import (
    COMMENTS_DIRECTORY_NAME,
    INDEX_FILENAME,
    ISSUES_DIRECTORY_NAME,
    PULL_REQUESTS_DIRECTORY_NAME,
    REPOSITORY_DIRECTORY_NAME,
    account_path,
    board_root,
    lock_path,
    repository_directory,
)
from weekend_loop.models import (
    BoardAccount,
    BoardComment,
    BoardIndex,
    BoardIssue,
    BoardLabel,
    BoardPullRequest,
    IssueState,
)
from weekend_loop.records import append_record, read_record, write_record

FIRST_NUMBER: Final[int] = 1
FIRST_COMMENT_ID: Final[int] = 1
DEFAULT_LABELS: Final[tuple[BoardLabel, ...]] = (
    BoardLabel(name="bug", description="Something isn't working", colour="d73a4a"),
    BoardLabel(
        name="documentation",
        description="Improvements or additions to documentation",
        colour="0075ca",
    ),
    BoardLabel(
        name="duplicate", description="This issue or pull request already exists", colour="cfd3d7"
    ),
    BoardLabel(name="enhancement", description="New feature or request", colour="a2eeef"),
    BoardLabel(name="good first issue", description="Good for newcomers", colour="7057ff"),
    BoardLabel(name="help wanted", description="Extra attention is needed", colour="008672"),
    BoardLabel(name="invalid", description="This doesn't seem right", colour="e4e669"),
    BoardLabel(name="question", description="Further information is requested", colour="d876e3"),
    BoardLabel(name="wontfix", description="This will not be worked on", colour="ffffff"),
)


def now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


@contextmanager
def locked(root: Path, exclusive: bool) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    with lock_path(root).open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def read_account(root: Path) -> BoardAccount:
    return read_record(BoardAccount, account_path(root))


def write_account(root: Path, account: BoardAccount) -> None:
    write_record(account, account_path(root))


class LocalBoard:
    def __init__(self, slug: str, directory: Path) -> None:
        self.slug = slug
        self.directory = directory

    @property
    def index_path(self) -> Path:
        return self.directory / INDEX_FILENAME

    @property
    def repository_path(self) -> Path:
        return self.directory / REPOSITORY_DIRECTORY_NAME

    def issue_path(self, number: int) -> Path:
        return self.directory / ISSUES_DIRECTORY_NAME / f"{number}.json"

    def comments_path(self, number: int) -> Path:
        return self.directory / COMMENTS_DIRECTORY_NAME / f"{number}.jsonl"

    def pull_request_path(self, number: int) -> Path:
        return self.directory / PULL_REQUESTS_DIRECTORY_NAME / f"{number}.json"

    def exists(self) -> bool:
        return self.index_path.is_file()

    def read_index(self) -> BoardIndex:
        return read_record(BoardIndex, self.index_path)

    def write_index(self, index: BoardIndex) -> None:
        write_record(index, self.index_path)

    def take_number(self) -> int:
        index = self.read_index()
        self.write_index(index.model_copy(update={"next_number": index.next_number + 1}))
        return index.next_number

    def take_comment_id(self) -> int:
        index = self.read_index()
        self.write_index(index.model_copy(update={"next_comment_id": index.next_comment_id + 1}))
        return index.next_comment_id

    def labels(self) -> dict[str, BoardLabel]:
        return {label.name: label for label in self.read_index().labels}

    def write_label(self, label: BoardLabel) -> None:
        index = self.read_index()
        kept = [existing for existing in index.labels if existing.name != label.name]
        self.write_index(index.model_copy(update={"labels": [*kept, label]}))

    def link_stack(self, pull_request_numbers: list[int]) -> None:
        index = self.read_index()
        self.write_index(index.model_copy(update={"stacks": [*index.stacks, pull_request_numbers]}))

    def issues(self) -> list[BoardIssue]:
        directory = self.directory / ISSUES_DIRECTORY_NAME
        if not directory.is_dir():
            return []
        issues = [read_record(BoardIssue, path) for path in directory.glob("*.json")]
        return sorted(issues, key=lambda issue: issue.number)

    def open_issues(self) -> list[BoardIssue]:
        return [issue for issue in self.issues() if issue.state is IssueState.OPEN]

    def has_issue(self, number: int) -> bool:
        return self.issue_path(number).is_file()

    def read_issue(self, number: int) -> BoardIssue:
        return read_record(BoardIssue, self.issue_path(number))

    def write_issue(self, issue: BoardIssue) -> None:
        write_record(issue, self.issue_path(issue.number))

    def pull_requests(self) -> list[BoardPullRequest]:
        directory = self.directory / PULL_REQUESTS_DIRECTORY_NAME
        if not directory.is_dir():
            return []
        found = [read_record(BoardPullRequest, path) for path in directory.glob("*.json")]
        return sorted(found, key=lambda pull_request: pull_request.number)

    def open_pull_requests(self) -> list[BoardPullRequest]:
        return [
            pull_request
            for pull_request in self.pull_requests()
            if pull_request.state is IssueState.OPEN
        ]

    def has_pull_request(self, number: int) -> bool:
        return self.pull_request_path(number).is_file()

    def read_pull_request(self, number: int) -> BoardPullRequest:
        return read_record(BoardPullRequest, self.pull_request_path(number))

    def write_pull_request(self, pull_request: BoardPullRequest) -> None:
        write_record(pull_request, self.pull_request_path(pull_request.number))

    def comments(self, number: int) -> list[BoardComment]:
        path = self.comments_path(number)
        if not path.is_file():
            return []
        return [BoardComment.model_validate_json(line) for line in path.read_text().splitlines()]

    def append_comment(self, number: int, comment: BoardComment) -> None:
        append_record(comment, self.comments_path(number))
        self.touch(number, comment.created_at)

    def touch(self, number: int, now: datetime) -> BoardIssue:
        updated = self.read_issue(number).model_copy(update={"updated_at": now})
        self.write_issue(updated)
        return updated

    def set_labels(self, number: int, labels: list[str], now: datetime) -> BoardIssue:
        updated = self.read_issue(number).model_copy(update={"labels": labels, "updated_at": now})
        self.write_issue(updated)
        return updated

    def add_labels(self, number: int, labels: list[str], now: datetime) -> BoardIssue:
        issue = self.read_issue(number)
        merged = issue.labels + [label for label in labels if label not in issue.labels]
        return self.set_labels(number, merged, now)

    def remove_labels(self, number: int, labels: list[str], now: datetime) -> BoardIssue:
        issue = self.read_issue(number)
        kept = [label for label in issue.labels if label not in labels]
        if kept == issue.labels:
            return issue
        return self.set_labels(number, kept, now)


def board_at(root: Path, slug: str) -> LocalBoard:
    return LocalBoard(slug, repository_directory(root, slug))


def open_board(state_directory: Path, slug: str) -> LocalBoard:
    return board_at(board_root(state_directory), slug)


def create_board(root: Path, slug: str, contents_writable: bool) -> LocalBoard:
    board = board_at(root, slug)
    board.directory.mkdir(parents=True, exist_ok=True)
    board.write_index(
        BoardIndex(
            next_number=FIRST_NUMBER,
            next_comment_id=FIRST_COMMENT_ID,
            contents_writable=contents_writable,
            labels=list(DEFAULT_LABELS),
            stacks=[],
        )
    )
    return board
