from __future__ import annotations

import shutil
from pathlib import Path
from typing import Final

from weekend_loop.acceptance import acceptance_map_path
from weekend_loop.briefing import prepared_path
from weekend_loop.demo.playground import OWNER_LOGIN
from weekend_loop.demo.publish import SetupContext, publish_playground
from weekend_loop.demo.seed import (
    acceptance_directory,
    acceptance_map,
    issues_directory,
    load_seed_issues,
    seed_repository,
    write_acceptance_map,
)
from weekend_loop.github import reader_for
from weekend_loop.local_github.paths import account_path, board_root, repository_directory
from weekend_loop.local_github.repository import initialise_bare
from weekend_loop.local_github.store import board_at, create_board, write_account
from weekend_loop.local_github.wrapper import install_wrapper
from weekend_loop.models import (
    Backend,
    BoardAccount,
    Policy,
    Record,
    RepoMode,
    RepoTarget,
    Workspace,
)
from weekend_loop.policy import repo_target
from weekend_loop.runs import RUNS_DIRECTORY_NAME, ledger_path
from weekend_loop.setup.tokens import save_secret
from weekend_loop.workbench import clone_url

BOARD_TOKEN: Final[str] = "local-board"


def prepare_local_board(policy: Policy, repo: RepoTarget) -> None:
    root = board_root(policy.state_dir)
    if not account_path(root).is_file():
        write_account(root, BoardAccount(login=OWNER_LOGIN))
    if not board_at(root, repo.slug).exists():
        board = create_board(root, repo.slug, repo.mode is RepoMode.EXECUTE)
        initialise_bare(board.repository_path, repo.base_branch)
    install_wrapper(policy.state_dir)
    if not repo.token_path().is_file():
        save_secret(repo.token_path(), BOARD_TOKEN)


def removable_paths(policy: Policy, repo: RepoTarget, repo_key: str) -> list[Path]:
    return [
        repository_directory(board_root(policy.state_dir), repo.slug),
        policy.workspace.workbench_path(repo_key),
        policy.workspace.worktrees_path(repo_key),
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
    prepare_local_board(policy, repo)
    remote = clone_url(repo, policy.state_dir)
    publish_playground(SetupContext(policy, repo_key, repo, remote, examples))
    outcome = seed_repository(reader_for(repo, policy.state_dir), repo, policy.labels, issues)
    copy_acceptance_tests(examples, policy.workspace)
    tests = acceptance_map(issues, outcome.issue_numbers)
    write_acceptance_map(policy.state_dir, tests)
    return DemoOutcome(removed=removed, issue_numbers=outcome.issue_numbers, acceptance_tests=tests)


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
