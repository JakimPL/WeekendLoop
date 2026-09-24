from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Final

from weekend_loop.board import local_repository_path
from weekend_loop.commands import DEFAULT_COMMAND_TIMEOUT_SECONDS, run_command
from weekend_loop.models import Backend, CommandResult, IdentityPolicy, RepoTarget, Workspace

GIT_BINARY: Final[str] = "git"
GIT_TIMEOUT_SECONDS: Final[int] = 600
ASKPASS_FILENAME: Final[str] = "git-askpass.sh"
EMPTY_HOOKS_DIRECTORY_NAME: Final[str] = "git-hooks-off"
TOKEN_VARIABLE: Final[str] = "WEEKEND_GITHUB_TOKEN"
CLONE_USERNAME: Final[str] = "x-access-token"
PRESERVED_ENTRIES: Final[tuple[str, ...]] = (".venv",)
BRANCH_SLUG_CHARACTERS: Final[int] = 40
INHERITED_ENVIRONMENT_KEYS: Final[tuple[str, ...]] = ("PATH", "HOME", "LANG", "LC_ALL", "TZ")
ASKPASS_SCRIPT: Final[str] = f'#!/bin/sh\nprintf "%s" "${TOKEN_VARIABLE}"\n'


def clone_url(repo: RepoTarget, state_directory: Path) -> str:
    if repo.backend is Backend.LOCAL:
        return str(local_repository_path(state_directory, repo.slug))
    if repo.remote_url is not None:
        return repo.remote_url
    return github_clone_url(repo.slug)


def github_clone_url(slug: str) -> str:
    return f"https://{CLONE_USERNAME}@github.com/{slug}.git"


def write_askpass_script(state_directory: Path) -> Path:
    path = state_directory / ASKPASS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ASKPASS_SCRIPT)
    path.chmod(0o700)
    return path


def git_environment(token: str, askpass: Path) -> dict[str, str]:
    environment = {key: os.environ[key] for key in INHERITED_ENVIRONMENT_KEYS if key in os.environ}
    environment[TOKEN_VARIABLE] = token
    environment["GIT_ASKPASS"] = str(askpass)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    return environment


def run_git(arguments: list[str], cwd: Path, environment: dict[str, str]) -> str:
    completed = subprocess.run(
        [GIT_BINARY, *arguments],
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    return completed.stdout


def hooks_off_directory(state_directory: Path) -> Path:
    path = state_directory / EMPTY_HOOKS_DIRECTORY_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def clone_repository(
    repo: RepoTarget, destination: Path, state_directory: Path, environment: dict[str, str]
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    arguments = ["clone", "--single-branch", "--branch", repo.base_branch]
    if repo.recurse_submodules:
        arguments.append("--recurse-submodules")
    arguments.extend([clone_url(repo, state_directory), str(destination)])
    run_git(arguments, cwd=destination.parent, environment=environment)
    run_git(
        ["config", "core.hooksPath", str(hooks_off_directory(state_directory))],
        cwd=destination,
        environment=environment,
    )


def reset_to_base(repo: RepoTarget, workbench: Path, environment: dict[str, str]) -> None:
    run_git(["fetch", "origin", repo.base_branch], cwd=workbench, environment=environment)
    run_git(
        ["checkout", "-B", repo.base_branch, f"origin/{repo.base_branch}"],
        cwd=workbench,
        environment=environment,
    )
    exclusions = [argument for entry in PRESERVED_ENTRIES for argument in ("-e", entry)]
    run_git(["clean", "-fdx", *exclusions], cwd=workbench, environment=environment)


def prepare_checkout(repo: RepoTarget, repo_key: str, workspace: Workspace, token: str) -> Path:
    state_directory = workspace.state_dir
    workbench = workspace.workbench_path(repo_key)
    environment = git_environment(token, write_askpass_script(state_directory))
    expected = clone_url(repo, state_directory)
    if (workbench / ".git").is_dir() and origin_url(workbench, environment) != expected:
        shutil.rmtree(workbench)
    if (workbench / ".git").is_dir():
        reset_to_base(repo, workbench, environment)
    else:
        clone_repository(repo, workbench, state_directory, environment)
    return workbench


def origin_url(workbench: Path, environment: dict[str, str]) -> str:
    return run_git(["remote", "get-url", "origin"], cwd=workbench, environment=environment).strip()


def current_branch(workbench: Path, environment: dict[str, str]) -> str:
    return run_git(
        ["rev-parse", "--abbrev-ref", "HEAD"], cwd=workbench, environment=environment
    ).strip()


def switch_branch(workbench: Path, branch: str, environment: dict[str, str]) -> None:
    run_git(["checkout", branch], cwd=workbench, environment=environment)


def current_commit(workbench: Path, environment: dict[str, str]) -> str:
    return run_git(["rev-parse", "HEAD"], cwd=workbench, environment=environment).strip()


def slugify(title: str) -> str:
    lowered = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return lowered[:BRANCH_SLUG_CHARACTERS].rstrip("-") or "issue"


def branch_name(prefix: str, issue_number: int, title: str) -> str:
    return f"{prefix}{issue_number}-{slugify(title)}"


def create_task_branch(workbench: Path, branch: str, environment: dict[str, str]) -> None:
    run_git(["checkout", "-B", branch], cwd=workbench, environment=environment)


def run_setup_commands(
    repo: RepoTarget, workbench: Path, environment: dict[str, str]
) -> list[CommandResult]:
    return [
        run_command(command, workbench, environment, DEFAULT_COMMAND_TIMEOUT_SECONDS)
        for command in repo.setup_commands
    ]


def working_tree_is_dirty(workbench: Path, environment: dict[str, str]) -> bool:
    return bool(run_git(["status", "--porcelain"], cwd=workbench, environment=environment).strip())


def commit_changes(
    workbench: Path, identity: IdentityPolicy, subject: str, environment: dict[str, str]
) -> str | None:
    if not working_tree_is_dirty(workbench, environment):
        return None
    run_git(["add", "--all"], cwd=workbench, environment=environment)
    run_git(
        [
            "-c",
            f"user.name={identity.git_author_name}",
            "-c",
            f"user.email={identity.git_author_email}",
            "commit",
            "--no-verify",
            "--message",
            subject,
        ],
        cwd=workbench,
        environment=environment,
    )
    return current_commit(workbench, environment)


def commit_count(workbench: Path, base_reference: str, environment: dict[str, str]) -> int:
    output = run_git(
        ["rev-list", "--count", f"{base_reference}..HEAD"], cwd=workbench, environment=environment
    )
    return int(output.strip() or "0")


def changed_files_numstat(workbench: Path, base_reference: str, environment: dict[str, str]) -> str:
    return run_git(
        ["diff", "--numstat", f"{base_reference}..HEAD"], cwd=workbench, environment=environment
    )


def diff_text(workbench: Path, base_reference: str, environment: dict[str, str]) -> str:
    return run_git(["diff", f"{base_reference}..HEAD"], cwd=workbench, environment=environment)


def discard_changes(workbench: Path, environment: dict[str, str]) -> None:
    run_git(["checkout", "--", "."], cwd=workbench, environment=environment)
    run_git(["clean", "-fd"], cwd=workbench, environment=environment)
