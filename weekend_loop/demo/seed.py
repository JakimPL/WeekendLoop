from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel, ConfigDict, TypeAdapter

from weekend_loop.acceptance import acceptance_map_path
from weekend_loop.github import GhCommands
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
OVERLAP_FILE_PATH: Final[str] = "pocketchat/static/dark-mode.css"
OVERLAP_FILE_CONTENT: Final[str] = (
    "@media (prefers-color-scheme: dark) {\n"
    "  :root {\n"
    "    --page: #1A1A1A;\n"
    "    --surface: #262626;\n"
    "  }\n"
    "}\n"
)
OVERLAP_COMMIT_MESSAGE: Final[str] = "feat(page): start the dark mode"
OVERLAP_PULL_REQUEST_TITLE: Final[str] = "feat(page): dark mode (work in progress, #{issue_number})"
EXAMPLE_LABEL_COLOUR: Final[str] = "C5DEF5"
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
    overlapping_branch: str | None
    body: str


class ListedIssue(BaseModel):
    model_config = ConfigDict(frozen=True)

    number: int
    title: str


class ReferencedObject(BaseModel):
    model_config = ConfigDict(frozen=True)

    sha: str


class ListedReference(BaseModel):
    model_config = ConfigDict(frozen=True)

    ref: str
    object: ReferencedObject


class SeedOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    issue_numbers: dict[str, int]
    created_issues: list[str]
    opened_pull_requests: list[str]


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


def branch_exists(commands: GhCommands, slug: str, branch: str) -> bool:
    output = commands.run(["api", f"repos/{slug}/git/matching-refs/heads/{branch}"])
    references = TypeAdapter(list[ListedReference]).validate_json(output or "[]")
    return any(reference.ref == f"refs/heads/{branch}" for reference in references)


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


def base_commit(commands: GhCommands, repo: RepoTarget) -> str:
    output = commands.run(["api", f"repos/{repo.slug}/git/ref/heads/{repo.base_branch}"])
    return ListedReference.model_validate_json(output).object.sha


def create_overlapping_branch(commands: GhCommands, repo: RepoTarget, branch: str) -> None:
    commands.run(
        [
            "api",
            "--method",
            "POST",
            f"repos/{repo.slug}/git/refs",
            "-f",
            f"ref=refs/heads/{branch}",
            "-f",
            f"sha={base_commit(commands, repo)}",
        ]
    )
    encoded_content = base64.b64encode(OVERLAP_FILE_CONTENT.encode()).decode()
    commands.run(
        [
            "api",
            "--method",
            "PUT",
            f"repos/{repo.slug}/contents/{OVERLAP_FILE_PATH}",
            "-f",
            f"message={OVERLAP_COMMIT_MESSAGE}",
            "-f",
            f"branch={branch}",
            "-f",
            f"content={encoded_content}",
        ]
    )


def open_overlapping_pull_request(
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
            OVERLAP_PULL_REQUEST_TITLE.format(issue_number=issue_number),
            "--body",
            f"Work in progress by a human colleague.\n\nCloses #{issue_number}",
        ]
    )


def ensure_overlapping_pull_request(
    commands: GhCommands, repo: RepoTarget, branch: str, issue_number: int
) -> bool:
    if not branch_exists(commands, repo.slug, branch):
        create_overlapping_branch(commands, repo, branch)
    if pull_request_exists(commands, repo.slug, branch):
        return False
    open_overlapping_pull_request(commands, repo, branch, issue_number)
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


def ensure_overlapping_pull_requests(
    commands: GhCommands, repo: RepoTarget, issues: list[SeedIssue], issue_numbers: dict[str, int]
) -> list[str]:
    return [
        issue.overlapping_branch
        for issue in issues
        if issue.overlapping_branch is not None
        and ensure_overlapping_pull_request(
            commands, repo, issue.overlapping_branch, issue_numbers[issue.key]
        )
    ]


def seed_repository(
    commands: GhCommands, repo: RepoTarget, labels: LabelPolicy, issues: list[SeedIssue]
) -> SeedOutcome:
    for label in [*board_labels(labels), *example_labels()]:
        create_label(commands, repo.slug, label)
    issue_numbers, created_issues = ensure_issues(commands, repo.slug, issues)
    return SeedOutcome(
        issue_numbers=issue_numbers,
        created_issues=created_issues,
        opened_pull_requests=ensure_overlapping_pull_requests(
            commands, repo, issues, issue_numbers
        ),
    )


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
