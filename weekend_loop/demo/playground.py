from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Final

from weekend_loop.demo.seed import demo_repository

OWNER_LOGIN: Final[str] = "example-operator"
SEED_GIT_DATE: Final[str] = "2026-09-14T09:00:00+00:00"
INITIAL_COMMIT_MESSAGE: Final[str] = "Added: the Pocketchat mockup"
GIT_BINARY: Final[str] = "git"
EXCLUDED_FROM_SEED: Final[tuple[str, ...]] = (".git", ".venv", "__pycache__")
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


def commit_overlapping_branch(staging: Path, branch: str, base_branch: str) -> None:
    git(["checkout", "-b", branch], cwd=staging)
    overlap = staging / OVERLAP_FILE_PATH
    overlap.parent.mkdir(parents=True, exist_ok=True)
    overlap.write_text(OVERLAP_FILE_CONTENT)
    git(["add", OVERLAP_FILE_PATH], cwd=staging)
    git(["commit", "--message", OVERLAP_COMMIT_MESSAGE], cwd=staging)
    git(["checkout", base_branch], cwd=staging)


def stage_playground(
    examples: Path, staging: Path, base_branch: str, overlapping_branches: list[str]
) -> None:
    commit_playground(examples, staging, base_branch)
    for branch in overlapping_branches:
        commit_overlapping_branch(staging, branch, base_branch)
