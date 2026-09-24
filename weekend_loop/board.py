from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from weekend_loop.models import (
    BoardComment,
    BoardIndex,
    BoardIssue,
    BoardPullRequest,
    IssueState,
)
from weekend_loop.records import append_record, read_record, write_record

BOARD_DIRECTORY_NAME: Final[str] = "board"
REPOSITORY_DIRECTORY_NAME: Final[str] = "repo.git"
ISSUES_DIRECTORY_NAME: Final[str] = "issues"
COMMENTS_DIRECTORY_NAME: Final[str] = "comments"
PULL_REQUESTS_DIRECTORY_NAME: Final[str] = "pull_requests"
INDEX_FILENAME: Final[str] = "index.json"
SLUG_SEPARATOR: Final[str] = "__"
URL_SCHEME: Final[str] = "board"
FIRST_NUMBER: Final[int] = 1


def board_directory(state_directory: Path, slug: str) -> Path:
    return state_directory / BOARD_DIRECTORY_NAME / slug.replace("/", SLUG_SEPARATOR)


def local_repository_path(state_directory: Path, slug: str) -> Path:
    return board_directory(state_directory, slug) / REPOSITORY_DIRECTORY_NAME


def issue_url(slug: str, number: int) -> str:
    return f"{URL_SCHEME}://{slug}/issues/{number}"


def pull_request_url(slug: str, number: int) -> str:
    return f"{URL_SCHEME}://{slug}/pull/{number}"


def now_utc() -> datetime:
    return datetime.now(UTC)


class LocalBoard:
    def __init__(self, slug: str, directory: Path) -> None:
        self.slug = slug
        self.directory = directory

    @property
    def index_path(self) -> Path:
        return self.directory / INDEX_FILENAME

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

    def issues(self) -> list[BoardIssue]:
        directory = self.directory / ISSUES_DIRECTORY_NAME
        if not directory.is_dir():
            return []
        issues = [read_record(BoardIssue, path) for path in directory.glob("*.json")]
        return sorted(issues, key=lambda issue: issue.number)

    def open_issues(self) -> list[BoardIssue]:
        return [issue for issue in self.issues() if issue.state is IssueState.OPEN]

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

    def write_pull_request(self, pull_request: BoardPullRequest) -> None:
        write_record(pull_request, self.pull_request_path(pull_request.number))

    def comments(self, number: int) -> list[BoardComment]:
        path = self.comments_path(number)
        if not path.is_file():
            return []
        return [BoardComment.model_validate_json(line) for line in path.read_text().splitlines()]

    def append_comment(self, number: int, comment: BoardComment) -> None:
        append_record(comment, self.comments_path(number))

    def set_labels(self, number: int, labels: list[str], now: datetime) -> BoardIssue:
        updated = self.read_issue(number).model_copy(update={"labels": labels, "updated_at": now})
        self.write_issue(updated)
        return updated

    def add_labels(self, number: int, labels: list[str], now: datetime) -> BoardIssue:
        issue = self.read_issue(number)
        merged = issue.labels + [label for label in labels if label not in issue.labels]
        updated = issue.model_copy(update={"labels": merged, "updated_at": now})
        self.write_issue(updated)
        return updated

    def remove_labels(self, number: int, labels: list[str], now: datetime) -> BoardIssue:
        issue = self.read_issue(number)
        kept = [label for label in issue.labels if label not in labels]
        if kept == issue.labels:
            return issue
        updated = issue.model_copy(update={"labels": kept, "updated_at": now})
        self.write_issue(updated)
        return updated


def open_board(state_directory: Path, slug: str) -> LocalBoard:
    return LocalBoard(slug, board_directory(state_directory, slug))


def create_board(state_directory: Path, slug: str, viewer_login: str) -> LocalBoard:
    board = open_board(state_directory, slug)
    board.directory.mkdir(parents=True, exist_ok=True)
    board.write_index(BoardIndex(viewer_login=viewer_login, next_number=FIRST_NUMBER, labels=[]))
    return board
