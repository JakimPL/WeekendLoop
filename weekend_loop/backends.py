from pathlib import Path
from typing import Protocol

from weekend_loop.github import last_foreign_activity, read_token
from weekend_loop.github import reader_for as github_reader_for
from weekend_loop.github import writer_for as github_writer_for
from weekend_loop.models import (
    IdentityPolicy,
    Issue,
    IssueComment,
    PullRequest,
    RepoTarget,
)


class BoardReader(Protocol):
    slug: str

    def viewer_login(self) -> str: ...

    def open_issues(self, limit: int) -> list[Issue]: ...

    def open_pull_requests(self, limit: int) -> list[PullRequest]: ...

    def issue_comments(self, issue_number: int) -> list[IssueComment]: ...


class BoardWriter(Protocol):
    def push_branch(
        self, branch: str, prefix: str, workbench: Path, environment: dict[str, str]
    ) -> None: ...

    def open_draft_pull_request(self, branch: str, title: str, body: str, base: str) -> str: ...

    def link_stack(self, pull_request_numbers: list[int]) -> None: ...

    def comment_on_issue(self, issue_number: int, body: str) -> None: ...

    def add_labels(self, issue_number: int, labels: list[str]) -> None: ...

    def remove_labels(self, issue_number: int, labels: list[str]) -> None: ...

    def create_issue(self, title: str, body: str) -> str: ...


def with_foreign_activity(reader: BoardReader, issue: Issue, owner_login: str) -> Issue:
    comments = reader.issue_comments(issue.number)
    return issue.model_copy(
        update={"last_foreign_activity_at": last_foreign_activity(comments, owner_login)}
    )


def reader_for(repo: RepoTarget, state_directory: Path) -> BoardReader:
    return github_reader_for(repo, state_directory)


def writer_for(
    repo: RepoTarget, state_directory: Path, identity: IdentityPolicy, label_namespace: str
) -> BoardWriter:
    return github_writer_for(repo, state_directory, identity, label_namespace)


def board_operator_login(identity: IdentityPolicy, reader: BoardReader) -> str:
    if identity.operator_login is not None:
        return identity.operator_login
    return reader.viewer_login()


def repository_token(repo: RepoTarget) -> str:
    return read_token(repo.token_path())
