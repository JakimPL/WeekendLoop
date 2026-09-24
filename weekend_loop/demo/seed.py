from __future__ import annotations

import base64
import json
import re
import subprocess
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel, ConfigDict, TypeAdapter

from weekend_loop.acceptance import acceptance_map_path
from weekend_loop.github import CONFIG_DIRECTORY_NAME, github_environment, read_token
from weekend_loop.labels import board_labels
from weekend_loop.models import (
    BoardLabel,
    IneligibilityReason,
    LabelPolicy,
    Policy,
    RepoTarget,
    Verdict,
)
from weekend_loop.policy import repo_target
from weekend_loop.workspace import DEFAULT_HOME_NAME

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


DEFAULT_WORKSPACE: Final[Path] = Path.home() / DEFAULT_HOME_NAME
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


class SeedRunner:
    def __init__(self, dry_run: bool, environment: dict[str, str]) -> None:
        self.dry_run = dry_run
        self.environment = environment
        self.log: list[str] = []
        self._placeholder_issue_number = 0

    def run(self, arguments: list[str], stdin: str | None = None) -> str:
        self.log.append(" ".join(arguments))
        if self.dry_run:
            return ""
        completed = subprocess.run(
            arguments,
            input=stdin,
            env=self.environment,
            capture_output=True,
            text=True,
            check=True,
        )
        return completed.stdout

    def next_placeholder_issue_number(self) -> int:
        self._placeholder_issue_number += 1
        return self._placeholder_issue_number


def create_label(runner: SeedRunner, slug: str, label: BoardLabel) -> None:
    runner.run(
        [
            "gh",
            "label",
            "create",
            label.name,
            "--repo",
            slug,
            "--description",
            label.description,
            "--color",
            label.colour,
            "--force",
        ]
    )


def create_issue(runner: SeedRunner, slug: str, issue: SeedIssue) -> int:
    arguments = ["gh", "issue", "create", "--repo", slug, "--title", issue.title]
    arguments.extend(["--body-file", "-"])
    for label in issue.labels:
        arguments.extend(["--label", label])
    output = runner.run(arguments, stdin=issue.body)
    if runner.dry_run:
        return runner.next_placeholder_issue_number()
    return issue_number_from_url(output)


def issue_number_from_url(output: str) -> int:
    match = ISSUE_URL_PATTERN.search(output.strip())
    if match is None:
        raise ValueError(f"gh issue create returned no issue URL: {output!r}")
    return int(match.group(1))


def existing_issue_numbers(runner: SeedRunner, slug: str) -> dict[str, int]:
    output = runner.run(
        [
            "gh",
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


def positive_count(output: str) -> bool:
    return bool(output.strip()) and int(output) > 0


def branch_exists(runner: SeedRunner, slug: str, branch: str) -> bool:
    exact_match = f'[.[] | select(.ref == "refs/heads/{branch}")] | length'
    return positive_count(
        runner.run(
            ["gh", "api", f"repos/{slug}/git/matching-refs/heads/{branch}", "--jq", exact_match]
        )
    )


def pull_request_exists(runner: SeedRunner, slug: str, branch: str) -> bool:
    return positive_count(
        runner.run(
            [
                "gh",
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
                "--jq",
                "length",
            ]
        )
    )


def create_overlapping_branch(runner: SeedRunner, repo: RepoTarget, branch: str) -> None:
    base_reference = runner.run(
        ["gh", "api", f"repos/{repo.slug}/git/ref/heads/{repo.base_branch}", "--jq", ".object.sha"]
    ).strip()
    base_sha = base_reference or "<base-sha>"
    runner.run(
        [
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{repo.slug}/git/refs",
            "-f",
            f"ref=refs/heads/{branch}",
            "-f",
            f"sha={base_sha}",
        ]
    )
    encoded_content = base64.b64encode(OVERLAP_FILE_CONTENT.encode()).decode()
    runner.run(
        [
            "gh",
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
    runner: SeedRunner, repo: RepoTarget, branch: str, issue_number: int
) -> None:
    runner.run(
        [
            "gh",
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
    runner: SeedRunner, repo: RepoTarget, branch: str, issue_number: int
) -> bool:
    if not branch_exists(runner, repo.slug, branch):
        create_overlapping_branch(runner, repo, branch)
    if pull_request_exists(runner, repo.slug, branch):
        return False
    open_overlapping_pull_request(runner, repo, branch, issue_number)
    return True


def ensure_issues(
    runner: SeedRunner, slug: str, issues: list[SeedIssue]
) -> tuple[dict[str, int], list[str]]:
    existing = existing_issue_numbers(runner, slug)
    issue_numbers: dict[str, int] = {}
    created: list[str] = []
    for issue in issues:
        if issue.title in existing:
            issue_numbers[issue.key] = existing[issue.title]
            continue
        issue_numbers[issue.key] = create_issue(runner, slug, issue)
        created.append(issue.key)
    return issue_numbers, created


def ensure_overlapping_pull_requests(
    runner: SeedRunner, repo: RepoTarget, issues: list[SeedIssue], issue_numbers: dict[str, int]
) -> list[str]:
    return [
        issue.overlapping_branch
        for issue in issues
        if issue.overlapping_branch is not None
        and ensure_overlapping_pull_request(
            runner, repo, issue.overlapping_branch, issue_numbers[issue.key]
        )
    ]


def seed_repository(
    runner: SeedRunner, repo: RepoTarget, labels: LabelPolicy, issues: list[SeedIssue]
) -> SeedOutcome:
    for label in [*board_labels(labels), *example_labels()]:
        create_label(runner, repo.slug, label)
    issue_numbers, created_issues = ensure_issues(runner, repo.slug, issues)
    return SeedOutcome(
        issue_numbers=issue_numbers,
        created_issues=created_issues,
        opened_pull_requests=ensure_overlapping_pull_requests(runner, repo, issues, issue_numbers),
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


def seed_environment(repo: RepoTarget, state_directory: Path) -> dict[str, str]:
    token = read_token(repo.token_path())
    config_directory = state_directory / CONFIG_DIRECTORY_NAME
    config_directory.mkdir(parents=True, exist_ok=True)
    return github_environment(token, config_directory)


def seed_github(
    examples: Path, policy: Policy, repo_key: str, apply: bool
) -> tuple[SeedRunner, SeedOutcome, dict[str, str]]:
    repo = repo_target(policy, repo_key)
    runner = SeedRunner(
        dry_run=not apply,
        environment=seed_environment(repo, policy.state_dir) if apply else {},
    )
    issues = load_seed_issues(issues_directory(examples))
    outcome = seed_repository(runner, repo, policy.labels, issues)
    tests = acceptance_map(issues, outcome.issue_numbers)
    if apply:
        write_acceptance_map(policy.state_dir, tests)
    return runner, outcome, tests
