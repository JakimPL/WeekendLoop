import subprocess
from pathlib import Path
from typing import Final

GIT_BINARY: Final[str] = "git"
HEADS_PREFIX: Final[str] = "refs/heads/"


def git(repository: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [GIT_BINARY, "--git-dir", str(repository), *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )


def initialise_bare(repository: Path, base_branch: str) -> None:
    repository.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [GIT_BINARY, "init", "--quiet", "--bare", "--initial-branch", base_branch, str(repository)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    )


def commit_exists(repository: Path, sha: str) -> bool:
    return git(repository, ["cat-file", "-e", f"{sha}^{{commit}}"]).returncode == 0


def branch_commit(repository: Path, branch: str) -> str | None:
    answer = git(repository, ["rev-parse", "--verify", "--quiet", f"{HEADS_PREFIX}{branch}"])
    return answer.stdout.strip() if answer.returncode == 0 else None


def reference_exists(repository: Path, reference: str) -> bool:
    return git(repository, ["rev-parse", "--verify", "--quiet", reference]).returncode == 0


def create_reference(repository: Path, reference: str, sha: str) -> None:
    git(repository, ["update-ref", reference, sha]).check_returncode()


def default_branch(repository: Path) -> str:
    answer = git(repository, ["symbolic-ref", "--short", "HEAD"])
    return answer.stdout.strip()


def commits_between(repository: Path, base: str, head: str) -> int:
    answer = git(repository, ["rev-list", "--count", f"{base}..{head}"])
    answer.check_returncode()
    return int(answer.stdout.strip())


def branch_diff(repository: Path, base: str, head: str) -> str:
    answer = git(repository, ["diff", f"{HEADS_PREFIX}{base}...{HEADS_PREFIX}{head}"])
    answer.check_returncode()
    return answer.stdout
