from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel, ConfigDict, TypeAdapter

from weekend_loop.acceptance import acceptance_map_path
from weekend_loop.github import API_VERSION_HEADER, PINNED_API_VERSION, GhCommands
from weekend_loop.labels import board_labels, label_arguments
from weekend_loop.models import (
    BoardLabel,
    IneligibilityReason,
    LabelPolicy,
    RepoTarget,
    Verdict,
)

DEFAULT_EXAMPLES: Final[Path] = Path("examples")
ISSUES_DIRECTORY_NAME: Final[str] = "issues"
DEMO_REPOSITORY_NAME: Final[str] = "demo-repo"
ACCEPTANCE_DIRECTORY_NAME: Final[str] = "acceptance"


def issues_directory(examples: Path) -> Path:
    return examples / ISSUES_DIRECTORY_NAME


def demo_repository(examples: Path) -> Path:
    return examples / DEMO_REPOSITORY_NAME


def acceptance_directory(examples: Path) -> Path:
    return examples / ACCEPTANCE_DIRECTORY_NAME


LOCAL_REPO_KEY: Final[str] = "demo"
GITHUB_REPO_KEY: Final[str] = "demo-github"
ISSUE_LIST_LIMIT: Final[int] = 200
FRONT_MATTER_DELIMITER: Final[str] = "---"
ISSUE_URL_PATTERN: Final[re.Pattern[str]] = re.compile(r"/issues/(\d+)\s*$")
LINKED_PULL_REQUEST_TITLE: Final[str] = "feat(page): dark mode (work in progress, #{issue_number})"
LINKED_PULL_REQUEST_BODY: Final[str] = (
    "Work in progress on the dark mode.\n\nCloses #{issue_number}"
)
EXAMPLE_LABEL_COLOUR: Final[str] = "C5DEF5"
DEPENDENCIES_VERSION: Final[str] = f"{API_VERSION_HEADER}: {PINNED_API_VERSION}"
DEPENDENCY_LINK_TEMPLATE: Final[str] = "#{number} blocked by #{blocker}"
EXAMPLE_LABELS: Final[tuple[tuple[str, str], ...]] = (
    ("refactor", "Restructures the code and keeps what it does"),
)


class SeedIssue(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    title: str
    labels: list[str]
    expected_verdict: Verdict | None
    expected_ineligibility: IneligibilityReason | None
    acceptance_test: Path | None
    linked_pull_request_branch: str | None
    blocked_by: list[str]
    body: str


class ListedIssue(BaseModel):
    model_config = ConfigDict(frozen=True)

    number: int
    title: str


class IssueIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    number: int


class SeedOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    issue_numbers: dict[str, int]
    created_issues: list[str]
    opened_pull_requests: list[str]
    linked_dependencies: list[str]


def parse_seed_issue(text: str) -> SeedIssue:
    leading, front_matter, body = text.split(FRONT_MATTER_DELIMITER, 2)
    if leading.strip():
        raise ValueError("seed issue must start with a front matter block")
    metadata = yaml.safe_load(front_matter)
    return SeedIssue.model_validate({**metadata, "body": body.strip() + "\n"})


def load_seed_issues(directory: Path) -> list[SeedIssue]:
    return [parse_seed_issue(path.read_text()) for path in sorted(directory.glob("*.md"))]


def example_labels() -> list[BoardLabel]:
    return [
        BoardLabel(name=name, description=description, colour=EXAMPLE_LABEL_COLOUR)
        for name, description in EXAMPLE_LABELS
    ]


def create_label(commands: GhCommands, slug: str, label: BoardLabel) -> None:
    commands.run(label_arguments(slug, label))


def create_issue(commands: GhCommands, slug: str, issue: SeedIssue) -> int:
    arguments = ["issue", "create", "--repo", slug, "--title", issue.title]
    arguments.extend(["--body-file", "-"])
    for label in issue.labels:
        arguments.extend(["--label", label])
    return issue_number_from_url(commands.run(arguments, stdin=issue.body))


def issue_number_from_url(output: str) -> int:
    match = ISSUE_URL_PATTERN.search(output.strip())
    if match is None:
        raise ValueError(f"gh issue create returned no issue URL: {output!r}")
    return int(match.group(1))


def existing_issue_numbers(commands: GhCommands, slug: str) -> dict[str, int]:
    output = commands.run(
        [
            "issue",
            "list",
            "--repo",
            slug,
            "--state",
            "all",
            "--limit",
            str(ISSUE_LIST_LIMIT),
            "--json",
            "number,title",
        ]
    )
    if not output.strip():
        return {}
    listed = TypeAdapter(list[ListedIssue]).validate_json(output)
    return {issue.title: issue.number for issue in listed}


def pull_request_exists(commands: GhCommands, slug: str, branch: str) -> bool:
    output = commands.run(
        [
            "pr",
            "list",
            "--repo",
            slug,
            "--head",
            branch,
            "--state",
            "all",
            "--json",
            "number",
        ]
    )
    return bool(json.loads(output or "[]"))


def open_linked_pull_request(
    commands: GhCommands, repo: RepoTarget, branch: str, issue_number: int
) -> None:
    commands.run(
        [
            "pr",
            "create",
            "--repo",
            repo.slug,
            "--draft",
            "--head",
            branch,
            "--base",
            repo.base_branch,
            "--title",
            LINKED_PULL_REQUEST_TITLE.format(issue_number=issue_number),
            "--body",
            LINKED_PULL_REQUEST_BODY.format(issue_number=issue_number),
        ]
    )


def ensure_linked_pull_request(
    commands: GhCommands, repo: RepoTarget, branch: str, issue_number: int
) -> bool:
    if pull_request_exists(commands, repo.slug, branch):
        return False
    open_linked_pull_request(commands, repo, branch, issue_number)
    return True


def ensure_issues(
    commands: GhCommands, slug: str, issues: list[SeedIssue]
) -> tuple[dict[str, int], list[str]]:
    existing = existing_issue_numbers(commands, slug)
    issue_numbers: dict[str, int] = {}
    created: list[str] = []
    for issue in issues:
        if issue.title in existing:
            issue_numbers[issue.key] = existing[issue.title]
            continue
        issue_numbers[issue.key] = create_issue(commands, slug, issue)
        created.append(issue.key)
    return issue_numbers, created


def ensure_linked_pull_requests(
    commands: GhCommands, repo: RepoTarget, issues: list[SeedIssue], issue_numbers: dict[str, int]
) -> list[str]:
    return [
        issue.linked_pull_request_branch
        for issue in issues
        if issue.linked_pull_request_branch is not None
        and ensure_linked_pull_request(
            commands, repo, issue.linked_pull_request_branch, issue_numbers[issue.key]
        )
    ]


def issue_identity(commands: GhCommands, slug: str, number: int) -> IssueIdentity:
    return IssueIdentity.model_validate_json(commands.run(["api", f"repos/{slug}/issues/{number}"]))


def dependencies_path(slug: str, number: int) -> str:
    return f"repos/{slug}/issues/{number}/dependencies/blocked_by"


def blockers_linked(commands: GhCommands, slug: str, number: int) -> set[int]:
    output = commands.run(["api", dependencies_path(slug, number), "-H", DEPENDENCIES_VERSION])
    listed = TypeAdapter(list[IssueIdentity]).validate_json(output or "[]")
    return {issue.number for issue in listed}


def link_blocker(commands: GhCommands, slug: str, number: int, blocker: int) -> None:
    blocking = issue_identity(commands, slug, blocker)
    commands.run(
        [
            *("api", "-X", "POST", dependencies_path(slug, number)),
            *("-H", DEPENDENCIES_VERSION, "-F", f"issue_id={blocking.id}"),
        ]
    )


def ensure_dependencies(
    commands: GhCommands, slug: str, issues: list[SeedIssue], issue_numbers: dict[str, int]
) -> list[str]:
    linked: list[str] = []
    for issue in issues:
        if not issue.blocked_by:
            continue
        number = issue_numbers[issue.key]
        present = blockers_linked(commands, slug, number)
        for blocker in (issue_numbers[key] for key in issue.blocked_by):
            if blocker not in present:
                link_blocker(commands, slug, number, blocker)
                linked.append(DEPENDENCY_LINK_TEMPLATE.format(number=number, blocker=blocker))
    return linked


def seed_repository(
    commands: GhCommands, repo: RepoTarget, labels: LabelPolicy, issues: list[SeedIssue]
) -> SeedOutcome:
    for label in [*board_labels(labels), *example_labels()]:
        create_label(commands, repo.slug, label)
    issue_numbers, created_issues = ensure_issues(commands, repo.slug, issues)
    return SeedOutcome(
        issue_numbers=issue_numbers,
        created_issues=created_issues,
        opened_pull_requests=ensure_linked_pull_requests(commands, repo, issues, issue_numbers),
        linked_dependencies=ensure_dependencies(commands, repo.slug, issues, issue_numbers),
    )


def dependency_links(issues: list[SeedIssue], issue_numbers: dict[str, int]) -> list[str]:
    return [
        DEPENDENCY_LINK_TEMPLATE.format(number=issue_numbers[issue.key], blocker=issue_numbers[key])
        for issue in issues
        for key in issue.blocked_by
    ]


def linked_pull_request_branches(issues: list[SeedIssue]) -> list[str]:
    return [
        issue.linked_pull_request_branch
        for issue in issues
        if issue.linked_pull_request_branch is not None
    ]


def acceptance_map(issues: list[SeedIssue], issue_numbers: dict[str, int]) -> dict[str, str]:
    return {
        str(issue_numbers[issue.key]): str(issue.acceptance_test)
        for issue in issues
        if issue.acceptance_test is not None
    }


def write_acceptance_map(state_directory: Path, tests: dict[str, str]) -> Path:
    path = acceptance_map_path(state_directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tests, indent=2) + "\n")
    return path
