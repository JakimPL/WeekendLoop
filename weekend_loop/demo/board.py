from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from weekend_loop.acceptance import acceptance_map_path
from weekend_loop.board import (
    LocalBoard,
    board_directory,
    create_board,
    local_repository_path,
)
from weekend_loop.briefing import prepared_path
from weekend_loop.demo.seed import (
    OVERLAP_COMMIT_MESSAGE,
    OVERLAP_FILE_CONTENT,
    OVERLAP_FILE_PATH,
    OVERLAP_PULL_REQUEST_TITLE,
    SeedIssue,
    acceptance_directory,
    acceptance_map,
    demo_repository,
    issues_directory,
    load_seed_issues,
    write_acceptance_map,
)
from weekend_loop.labels import board_labels
from weekend_loop.models import (
    Backend,
    BoardIssue,
    BoardPullRequest,
    IssueState,
    LabelPolicy,
    Policy,
    Record,
    RepoTarget,
    Workspace,
)
from weekend_loop.policy import repo_target
from weekend_loop.runs import RUNS_DIRECTORY_NAME, ledger_path

DEMO_REPOSITORY_NAME: Final[str] = "demo-repo"
OWNER_LOGIN: Final[str] = "example-operator"
COLLEAGUE_LOGIN: Final[str] = "a-colleague"
SEED_TIMESTAMP: Final[datetime] = datetime(2026, 9, 14, 9, 0, tzinfo=UTC)
SEED_GIT_DATE: Final[str] = "2026-09-14T09:00:00+00:00"
INITIAL_COMMIT_MESSAGE: Final[str] = "Added: the Pocketchat mockup"
GIT_BINARY: Final[str] = "git"
EXCLUDED_FROM_SEED: Final[tuple[str, ...]] = (".git", ".venv", "__pycache__")


def git(arguments: list[str], cwd: Path) -> str:
    environment = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(cwd),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": OWNER_LOGIN,
        "GIT_AUTHOR_EMAIL": "seed@example.invalid",
        "GIT_COMMITTER_NAME": OWNER_LOGIN,
        "GIT_COMMITTER_EMAIL": "seed@example.invalid",
        "GIT_AUTHOR_DATE": SEED_GIT_DATE,
        "GIT_COMMITTER_DATE": SEED_GIT_DATE,
    }
    completed = subprocess.run(
        [GIT_BINARY, *arguments],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


def copy_playground(examples: Path, destination: Path) -> None:
    shutil.copytree(
        demo_repository(examples),
        destination,
        ignore=shutil.ignore_patterns(*EXCLUDED_FROM_SEED),
    )


def commit_playground(examples: Path, staging: Path, base_branch: str) -> None:
    copy_playground(examples, staging)
    git(["init", "--initial-branch", base_branch], cwd=staging)
    git(["add", "--all"], cwd=staging)
    git(["commit", "--message", INITIAL_COMMIT_MESSAGE], cwd=staging)


def build_repository(examples: Path, repo: RepoTarget, state_directory: Path) -> Path:
    bare = local_repository_path(state_directory, repo.slug)
    staging = bare.parent / "seed-checkout"
    bare.parent.mkdir(parents=True, exist_ok=True)
    git(["init", "--bare", "--initial-branch", repo.base_branch, str(bare)], cwd=bare.parent)
    commit_playground(examples, staging, repo.base_branch)
    git(["remote", "add", "origin", str(bare)], cwd=staging)
    git(["push", "origin", repo.base_branch], cwd=staging)
    return staging


def push_overlapping_branch(repo: RepoTarget, staging: Path, branch: str) -> None:
    git(["checkout", "-b", branch], cwd=staging)
    overlap = staging / OVERLAP_FILE_PATH
    overlap.parent.mkdir(parents=True, exist_ok=True)
    overlap.write_text(OVERLAP_FILE_CONTENT)
    git(["add", OVERLAP_FILE_PATH], cwd=staging)
    git(["commit", "--message", OVERLAP_COMMIT_MESSAGE], cwd=staging)
    git(["push", "origin", branch], cwd=staging)
    git(["checkout", repo.base_branch], cwd=staging)


def board_issue(issue: SeedIssue, number: int) -> BoardIssue:
    return BoardIssue(
        number=number,
        title=issue.title,
        body=issue.body,
        labels=issue.labels,
        assignees=[],
        milestone=None,
        author=OWNER_LOGIN,
        created_at=SEED_TIMESTAMP,
        updated_at=SEED_TIMESTAMP,
        state=IssueState.OPEN,
    )


def overlapping_pull_request(
    board: LocalBoard, repo: RepoTarget, issue: SeedIssue, issue_number: int
) -> None:
    if issue.overlapping_branch is None:
        raise ValueError(f"issue {issue.key} declares no overlapping branch")
    number = board.take_number()
    board.write_pull_request(
        BoardPullRequest(
            number=number,
            title=OVERLAP_PULL_REQUEST_TITLE.format(issue_number=issue_number),
            body=f"Work in progress by a human colleague.\n\nCloses #{issue_number}",
            head_branch=issue.overlapping_branch,
            base_branch=repo.base_branch,
            author=COLLEAGUE_LOGIN,
            draft=True,
            state=IssueState.OPEN,
            created_at=SEED_TIMESTAMP,
        )
    )


def seed_board(
    examples: Path,
    state_directory: Path,
    repo: RepoTarget,
    labels: LabelPolicy,
    issues: list[SeedIssue],
) -> dict[str, int]:
    board = create_board(state_directory, repo.slug, OWNER_LOGIN)
    board.write_index(board.read_index().model_copy(update={"labels": board_labels(labels)}))
    issue_numbers: dict[str, int] = {}
    for issue in issues:
        number = board.take_number()
        board.write_issue(board_issue(issue, number))
        issue_numbers[issue.key] = number
    staging = build_repository(examples, repo, state_directory)
    for issue in issues:
        if issue.overlapping_branch is not None:
            push_overlapping_branch(repo, staging, issue.overlapping_branch)
            overlapping_pull_request(board, repo, issue, issue_numbers[issue.key])
    shutil.rmtree(staging)
    return issue_numbers


def removable_paths(policy: Policy, repo: RepoTarget, repo_key: str) -> list[Path]:
    return [
        board_directory(policy.state_dir, repo.slug),
        policy.workspace.workbench_path(repo_key),
        policy.state_dir / RUNS_DIRECTORY_NAME,
        acceptance_map_path(policy.state_dir),
        ledger_path(policy.state_dir),
        prepared_path(policy.state_dir, repo_key),
    ]


def remove(paths: list[Path]) -> list[Path]:
    removed = []
    for path in paths:
        if path.is_dir():
            shutil.rmtree(path)
            removed.append(path)
        elif path.is_file():
            path.unlink()
            removed.append(path)
    return removed


class DemoOutcome(Record):
    removed: list[Path]
    issue_numbers: dict[str, int]
    acceptance_tests: dict[str, str]


def copy_acceptance_tests(examples: Path, workspace: Workspace) -> None:
    source = acceptance_directory(examples)
    if not source.is_dir():
        return
    workspace.acceptance_dir.mkdir(parents=True, exist_ok=True)
    for test in sorted(source.glob("test_*.py")):
        shutil.copy2(test, workspace.acceptance_dir / test.name)


def build_demo(examples: Path, policy: Policy, repo_key: str, reset: bool) -> DemoOutcome:
    repo = repo_target(policy, repo_key)
    if repo.backend is not Backend.LOCAL:
        raise ValueError(f"repository {repo_key!r} does not keep its board on disk")
    issues = load_seed_issues(issues_directory(examples))
    removed = remove(removable_paths(policy, repo, repo_key)) if reset else []
    issue_numbers = seed_board(examples, policy.state_dir, repo, policy.labels, issues)
    copy_acceptance_tests(examples, policy.workspace)
    tests = acceptance_map(issues, issue_numbers)
    write_acceptance_map(policy.state_dir, tests)
    return DemoOutcome(removed=removed, issue_numbers=issue_numbers, acceptance_tests=tests)


def planned_demo(examples: Path, policy: Policy, repo_key: str, reset: bool) -> DemoOutcome:
    repo = repo_target(policy, repo_key)
    issues = load_seed_issues(issues_directory(examples))
    removed = removable_paths(policy, repo, repo_key) if reset else []
    numbers = {issue.key: number for number, issue in enumerate(issues, start=1)}
    return DemoOutcome(
        removed=removed, issue_numbers=numbers, acceptance_tests=acceptance_map(issues, numbers)
    )


def render_demo(outcome: DemoOutcome, planned: bool) -> str:
    lines = [f"{'would remove' if planned else 'removed'} {path}" for path in outcome.removed]
    lines.extend(
        f"{'would seed' if planned else 'seeded'} #{number} {key}"
        for key, number in outcome.issue_numbers.items()
    )
    lines.extend(
        f"hidden test for #{number}: {test}" for number, test in outcome.acceptance_tests.items()
    )
    return "\n".join(lines)
