from __future__ import annotations

import shutil
from pathlib import Path

from tests.unit.test_execute import build_origin
from weekend_loop.models import Backend, IdentityPolicy, Record, RepoMode, RepoTarget
from weekend_loop.workbench import (
    add_worktree,
    base_reference,
    clone_repository,
    commit_changes,
    commit_count,
    current_branch,
    current_commit,
    git_environment,
    hooks_off_directory,
    prune_worktrees,
    registered_worktrees,
    remove_worktree,
    reset_worktree,
    run_git,
    working_tree_is_dirty,
    write_askpass_script,
)

FIRST_BRANCH = "weekend/1-empty-speed-field"
SECOND_BRANCH = "weekend/2-round-the-heading"
IDENTITY = IdentityPolicy(git_author_email="weekend-loop@example.invalid")


class Checkout(Record):
    repo: RepoTarget
    workbench: Path
    worktrees_root: Path
    state_directory: Path
    environment: dict[str, str]

    def worktree(self, branch: str) -> Path:
        return self.worktrees_root / branch


def clone(tmp_path: Path) -> Checkout:
    repo = RepoTarget(
        slug="example-org/example-repo",
        mode=RepoMode.EXECUTE,
        backend=Backend.GITHUB,
        gate_commands=["true"],
        remote_url=build_origin(tmp_path),
    )
    state_directory = tmp_path / "state"
    environment = git_environment("token", write_askpass_script(state_directory))
    workbench = tmp_path / "work" / "repo"
    clone_repository(repo, workbench, state_directory, environment)
    return Checkout(
        repo=repo,
        workbench=workbench,
        worktrees_root=tmp_path / "work" / "repo-worktrees",
        state_directory=state_directory,
        environment=environment,
    )


def open_worktree(checkout: Checkout, branch: str) -> Path:
    worktree = checkout.worktree(branch)
    add_worktree(
        checkout.workbench, worktree, branch, base_reference(checkout.repo), checkout.environment
    )
    return worktree


def commit_file(checkout: Checkout, worktree: Path, name: str, subject: str) -> str:
    (worktree / name).write_text(f"{name}\n")
    committed = commit_changes(worktree, IDENTITY, subject, checkout.environment)
    assert committed is not None
    return committed


def branch_tip(checkout: Checkout, branch: str) -> str:
    return run_git(["rev-parse", branch], cwd=checkout.workbench, environment=checkout.environment)


def test_a_worktree_starts_on_the_task_branch_at_the_base(tmp_path: Path) -> None:
    checkout = clone(tmp_path)
    worktree = open_worktree(checkout, FIRST_BRANCH)
    assert current_branch(worktree, checkout.environment) == FIRST_BRANCH
    assert current_commit(worktree, checkout.environment) == current_commit(
        checkout.workbench, checkout.environment
    )
    assert (worktree / "logbook" / "records.py").is_file()
    assert current_branch(checkout.workbench, checkout.environment) == "main"


def test_a_worktree_keeps_the_hooks_off_like_the_checkout(tmp_path: Path) -> None:
    checkout = clone(tmp_path)
    worktree = open_worktree(checkout, FIRST_BRANCH)
    configured = run_git(
        ["config", "core.hooksPath"], cwd=worktree, environment=checkout.environment
    )
    assert configured.strip() == str(hooks_off_directory(checkout.state_directory))


def test_two_worktrees_commit_side_by_side_and_leave_the_checkout_alone(tmp_path: Path) -> None:
    checkout = clone(tmp_path)
    first = open_worktree(checkout, FIRST_BRANCH)
    second = open_worktree(checkout, SECOND_BRANCH)
    first_commit = commit_file(checkout, first, "first.txt", "first")
    second_commit = commit_file(checkout, second, "second.txt", "second")
    base = base_reference(checkout.repo)
    assert branch_tip(checkout, FIRST_BRANCH).strip() == first_commit
    assert branch_tip(checkout, SECOND_BRANCH).strip() == second_commit
    assert commit_count(first, base, checkout.environment) == 1
    assert commit_count(second, base, checkout.environment) == 1
    assert not (first / "second.txt").exists()
    assert current_branch(checkout.workbench, checkout.environment) == "main"
    assert not working_tree_is_dirty(checkout.workbench, checkout.environment)


def test_removing_a_worktree_keeps_the_branch_for_publishing(tmp_path: Path) -> None:
    checkout = clone(tmp_path)
    worktree = open_worktree(checkout, FIRST_BRANCH)
    committed = commit_file(checkout, worktree, "first.txt", "first")
    remove_worktree(checkout.workbench, worktree, checkout.environment)
    assert not worktree.exists()
    assert branch_tip(checkout, FIRST_BRANCH).strip() == committed
    assert registered_worktrees(checkout.workbench, checkout.environment) == [
        checkout.workbench.resolve()
    ]


def test_resetting_a_worktree_throws_its_work_away_and_keeps_the_environment(
    tmp_path: Path,
) -> None:
    checkout = clone(tmp_path)
    worktree = open_worktree(checkout, FIRST_BRANCH)
    commit_file(checkout, worktree, "first.txt", "first")
    (worktree / "stray.txt").write_text("untracked\n")
    (worktree / ".venv").mkdir()
    (worktree / ".venv" / "pyvenv.cfg").write_text("home = elsewhere\n")
    reset_worktree(worktree, FIRST_BRANCH, base_reference(checkout.repo), checkout.environment)
    assert commit_count(worktree, base_reference(checkout.repo), checkout.environment) == 0
    assert not (worktree / "first.txt").exists()
    assert not (worktree / "stray.txt").exists()
    assert (worktree / ".venv" / "pyvenv.cfg").is_file()
    assert current_branch(worktree, checkout.environment) == FIRST_BRANCH


def test_pruning_clears_what_a_crashed_run_left_behind(tmp_path: Path) -> None:
    checkout = clone(tmp_path)
    kept = open_worktree(checkout, FIRST_BRANCH)
    lost = open_worktree(checkout, SECOND_BRANCH)
    shutil.rmtree(lost)
    stray = checkout.worktrees_root / "weekend" / "9-stray"
    stray.mkdir(parents=True)
    (stray / ".git").write_text("gitdir: nowhere\n")
    removed = prune_worktrees(checkout.workbench, checkout.worktrees_root, checkout.environment)
    assert [path.resolve() for path in removed] == [kept.resolve()]
    assert not checkout.worktrees_root.exists()
    assert registered_worktrees(checkout.workbench, checkout.environment) == [
        checkout.workbench.resolve()
    ]
    assert FIRST_BRANCH in run_git(
        ["branch", "--list", FIRST_BRANCH], cwd=checkout.workbench, environment=checkout.environment
    )


def test_pruning_with_nothing_to_prune_changes_nothing(tmp_path: Path) -> None:
    checkout = clone(tmp_path)
    assert prune_worktrees(checkout.workbench, checkout.worktrees_root, checkout.environment) == []
    assert current_branch(checkout.workbench, checkout.environment) == "main"
