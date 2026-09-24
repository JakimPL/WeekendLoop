from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from weekend_loop.guards import assert_branch_allowed, assert_labels_allowed
from weekend_loop.models import (
    IdentityPolicy,
    Issue,
    IssueComment,
    PullRequest,
    RepoTarget,
)
from weekend_loop.questions import AGENT_MARKER, is_agent_comment
from weekend_loop.workbench import clone_url, run_git

GH_BINARY: Final[str] = "gh"
GH_TIMEOUT_SECONDS: Final[int] = 120
TOKEN_VARIABLE: Final[str] = "GH_TOKEN"
CONFIG_DIRECTORY_VARIABLE: Final[str] = "GH_CONFIG_DIR"
CONFIG_DIRECTORY_NAME: Final[str] = "gh-config"
INHERITED_ENVIRONMENT_KEYS: Final[tuple[str, ...]] = ("PATH", "HOME", "LANG", "LC_ALL", "TZ")
ISSUE_FIELDS: Final[str] = (
    "number,title,body,labels,assignees,milestone,author,createdAt,updatedAt,url"
)
PULL_REQUEST_FIELDS: Final[str] = "number,title,body,headRefName,author,url"
COMMENT_FIELDS: Final[str] = "comments"
CLOSING_KEYWORD_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b\s*:?\s+#(\d+)", re.IGNORECASE
)
BRANCH_ISSUE_PATTERN: Final[re.Pattern[str]] = re.compile(r"(?:^|[/_-])(\d{1,6})(?:[/_-]|$)")


def read_token(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"GitHub token file {path} is missing")
    token = path.read_text().strip()
    if not token:
        raise ValueError(f"GitHub token file {path} is empty")
    return token


def github_environment(token: str, config_directory: Path) -> dict[str, str]:
    environment = {key: os.environ[key] for key in INHERITED_ENVIRONMENT_KEYS if key in os.environ}
    environment[TOKEN_VARIABLE] = token
    environment[CONFIG_DIRECTORY_VARIABLE] = str(config_directory)
    return environment


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def text_of(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def login_of(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return text_of(value, "login") if isinstance(value, dict) else ""


def names_of(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [text_of(item, "name") for item in value if isinstance(item, dict)]


def logins_of(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [text_of(item, "login") for item in value if isinstance(item, dict)]


def milestone_of(payload: dict[str, Any]) -> str | None:
    value = payload.get("milestone")
    return text_of(value, "title") if isinstance(value, dict) else None


def issue_from_payload(payload: dict[str, Any], open_linked_pull_requests: list[int]) -> Issue:
    return Issue(
        number=int(payload["number"]),
        title=text_of(payload, "title"),
        body=text_of(payload, "body"),
        labels=names_of(payload, "labels"),
        assignees=logins_of(payload, "assignees"),
        milestone=milestone_of(payload),
        author=login_of(payload, "author"),
        created_at=parse_timestamp(payload["createdAt"]),
        updated_at=parse_timestamp(payload["updatedAt"]),
        url=text_of(payload, "url"),
        open_linked_pull_requests=open_linked_pull_requests,
        last_foreign_activity_at=None,
    )


def pull_request_from_payload(payload: dict[str, Any]) -> PullRequest:
    return PullRequest(
        number=int(payload["number"]),
        title=text_of(payload, "title"),
        body=text_of(payload, "body"),
        head_branch=text_of(payload, "headRefName"),
        author=login_of(payload, "author"),
        url=text_of(payload, "url"),
    )


def comment_from_payload(payload: dict[str, Any]) -> IssueComment:
    return IssueComment(
        author=login_of(payload, "author"),
        created_at=parse_timestamp(payload["createdAt"]),
        body=text_of(payload, "body"),
    )


def linked_issue_numbers(pull_request: PullRequest) -> list[int]:
    numbers = [int(match) for match in CLOSING_KEYWORD_PATTERN.findall(pull_request.body)]
    numbers.extend(int(match) for match in BRANCH_ISSUE_PATTERN.findall(pull_request.head_branch))
    return sorted(set(numbers))


def pull_requests_by_issue(pull_requests: list[PullRequest]) -> dict[int, list[int]]:
    linked: dict[int, list[int]] = {}
    for pull_request in pull_requests:
        for issue_number in linked_issue_numbers(pull_request):
            linked.setdefault(issue_number, []).append(pull_request.number)
    return linked


def last_foreign_activity(comments: list[IssueComment], owner_login: str) -> datetime | None:
    foreign = [
        comment.created_at
        for comment in comments
        if comment.author != owner_login and not is_agent_comment(comment.body)
    ]
    return max(foreign) if foreign else None


class GitHubReader:
    def __init__(self, slug: str, token: str, config_directory: Path) -> None:
        self.slug = slug
        self.environment = github_environment(token, config_directory)
        config_directory.mkdir(parents=True, exist_ok=True)

    def run(self, arguments: list[str], stdin: str | None = None) -> str:
        if stdin is not None:
            return self.run_with_input(arguments, stdin)
        completed = subprocess.run(
            [GH_BINARY, *arguments],
            stdin=subprocess.DEVNULL,
            env=self.environment,
            capture_output=True,
            text=True,
            check=True,
            timeout=GH_TIMEOUT_SECONDS,
        )
        return completed.stdout

    def run_with_input(self, arguments: list[str], body: str) -> str:
        completed = subprocess.run(
            [GH_BINARY, *arguments],
            input=body,
            env=self.environment,
            capture_output=True,
            text=True,
            check=True,
            timeout=GH_TIMEOUT_SECONDS,
        )
        return completed.stdout

    def run_json_list(self, arguments: list[str]) -> list[dict[str, Any]]:
        parsed = json.loads(self.run(arguments) or "[]")
        return [item for item in parsed if isinstance(item, dict)]

    def run_json_object(self, arguments: list[str]) -> dict[str, Any]:
        parsed = json.loads(self.run(arguments) or "{}")
        return parsed if isinstance(parsed, dict) else {}

    def viewer_login(self) -> str:
        return self.run(["api", "user", "--jq", ".login"]).strip()

    def open_pull_requests(self, limit: int) -> list[PullRequest]:
        payload = self.run_json_list(
            [
                "pr",
                "list",
                "--repo",
                self.slug,
                "--state",
                "open",
                "--limit",
                str(limit),
                "--json",
                PULL_REQUEST_FIELDS,
            ]
        )
        return [pull_request_from_payload(item) for item in payload]

    def open_issues(self, limit: int) -> list[Issue]:
        payload = self.run_json_list(
            [
                "issue",
                "list",
                "--repo",
                self.slug,
                "--state",
                "open",
                "--limit",
                str(limit),
                "--json",
                ISSUE_FIELDS,
            ]
        )
        linked = pull_requests_by_issue(self.open_pull_requests(limit))
        return [issue_from_payload(item, linked.get(int(item["number"]), [])) for item in payload]

    def issue_comments(self, issue_number: int) -> list[IssueComment]:
        payload = self.run_json_object(
            ["issue", "view", str(issue_number), "--repo", self.slug, "--json", COMMENT_FIELDS]
        )
        comments = payload.get("comments", [])
        if not isinstance(comments, list):
            return []
        return [comment_from_payload(item) for item in comments if isinstance(item, dict)]


def reader_for(repo: RepoTarget, state_directory: Path) -> GitHubReader:
    token = read_token(repo.token_path())
    return GitHubReader(repo.slug, token, state_directory / CONFIG_DIRECTORY_NAME)


PULL_REQUEST_URL_PATTERN: Final[re.Pattern[str]] = re.compile(r"https://\S+/pull/\d+")
ISSUE_URL_PATTERN: Final[re.Pattern[str]] = re.compile(r"https://\S+/issues/\d+")


def push_refspec(branch: str) -> str:
    return f"refs/heads/{branch}:refs/heads/{branch}"


def url_from_output(output: str, pattern: re.Pattern[str]) -> str:
    match = pattern.search(output)
    if match is None:
        raise ValueError(f"gh returned no url: {output.strip()!r}")
    return match.group(0)


def signed(body: str, footer: str, run_id: str) -> str:
    return f"{body.rstrip()}\n\n{AGENT_MARKER}\n{footer.format(run_id=run_id)}\n"


class GitHubWriter:
    def __init__(
        self,
        repo: RepoTarget,
        commands: GitHubReader,
        identity: IdentityPolicy,
        state_directory: Path,
        label_namespace: str,
    ) -> None:
        self.repo = repo
        self.commands = commands
        self.identity = identity
        self.state_directory = state_directory
        self.label_namespace = label_namespace

    def assert_remote_is_the_policy_repository(
        self, workbench: Path, environment: dict[str, str]
    ) -> None:
        actual = run_git(
            ["remote", "get-url", "origin"], cwd=workbench, environment=environment
        ).strip()
        expected = clone_url(self.repo, self.state_directory)
        if actual != expected:
            raise ValueError(f"workbench remote is {actual!r}, policy names {expected!r}")

    def push_branch(
        self, branch: str, prefix: str, workbench: Path, environment: dict[str, str]
    ) -> None:
        assert_branch_allowed(branch, prefix)
        self.assert_remote_is_the_policy_repository(workbench, environment)
        run_git(["push", "origin", push_refspec(branch)], cwd=workbench, environment=environment)

    def open_draft_pull_request(self, branch: str, title: str, body: str) -> str:
        output = self.commands.run(
            [
                "pr",
                "create",
                "--repo",
                self.repo.slug,
                "--draft",
                "--head",
                branch,
                "--base",
                self.repo.base_branch,
                "--title",
                title,
                "--body-file",
                "-",
            ],
            stdin=body,
        )
        return url_from_output(output, PULL_REQUEST_URL_PATTERN)

    def comment_on_issue(self, issue_number: int, body: str) -> None:
        self.commands.run(
            ["issue", "comment", str(issue_number), "--repo", self.repo.slug, "--body-file", "-"],
            stdin=body,
        )

    def add_labels(self, issue_number: int, labels: list[str]) -> None:
        assert_labels_allowed(labels, self.label_namespace)
        arguments = ["issue", "edit", str(issue_number), "--repo", self.repo.slug]
        for label in labels:
            arguments.extend(["--add-label", label])
        self.commands.run(arguments)

    def remove_labels(self, issue_number: int, labels: list[str]) -> None:
        assert_labels_allowed(labels, self.label_namespace)
        arguments = ["issue", "edit", str(issue_number), "--repo", self.repo.slug]
        for label in labels:
            arguments.extend(["--remove-label", label])
        self.commands.run(arguments)

    def create_issue(self, title: str, body: str) -> str:
        output = self.commands.run(
            ["issue", "create", "--repo", self.repo.slug, "--title", title, "--body-file", "-"],
            stdin=body,
        )
        return url_from_output(output, ISSUE_URL_PATTERN)


def writer_for(
    repo: RepoTarget, state_directory: Path, identity: IdentityPolicy, label_namespace: str
) -> GitHubWriter:
    return GitHubWriter(
        repo, reader_for(repo, state_directory), identity, state_directory, label_namespace
    )
