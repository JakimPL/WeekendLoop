from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest

from weekend_loop.demo.playground import git
from weekend_loop.github import GH_BINARY, ISSUE_FIELDS, PULL_REQUEST_FIELDS, json_documents
from weekend_loop.github_queries import BLOCKERS_QUERY
from weekend_loop.local_github.commands import Invocation, run_gh
from weekend_loop.local_github.payloads import issue_id
from weekend_loop.local_github.repository import initialise_bare
from weekend_loop.local_github.store import create_board, write_account
from weekend_loop.models import BoardAccount

pytestmark = pytest.mark.live

LIVE_REPOSITORY_VARIABLE: Final[str] = "WEEKEND_LOOP_LIVE_REPO"
LOCAL_SLUG: Final[str] = "example-org/example-board"
BASE_BRANCH: Final[str] = "main"
BRANCH: Final[str] = "weekend/1-change"
LIVE_TIMEOUT_SECONDS: Final[int] = 60
NULL_SHAPE: Final[str] = "null"

type Read = Callable[[str, int], list[str]]

READS: Final[dict[str, Read]] = {
    "viewer": lambda slug, number: ["api", "user"],
    "repository": lambda slug, number: ["api", f"repos/{slug}"],
    "issues": lambda slug, number: [
        *("issue", "list", "--repo", slug, "--state", "all", "--limit", "3"),
        *("--json", ISSUE_FIELDS),
    ],
    "pull requests": lambda slug, number: [
        *("pr", "list", "--repo", slug, "--state", "all", "--limit", "3"),
        *("--json", PULL_REQUEST_FIELDS),
    ],
    "comments": lambda slug, number: [
        *("issue", "view", str(number), "--repo", slug, "--json", "comments"),
    ],
    "issue": lambda slug, number: ["api", f"repos/{slug}/issues/{number}"],
    "blockers": lambda slug, number: [
        *("api", "graphql", "-f", f"query={BLOCKERS_QUERY}"),
        *("-f", f"owner={slug.split('/')[0]}", "-f", f"name={slug.split('/')[1]}"),
    ],
}


def shape(value: Any) -> Any:  # noqa: ANN401
    if isinstance(value, dict):
        return {key: shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    if value is None:
        return NULL_SHAPE
    return type(value).__name__


def mismatches(local: Any, live: Any, path: str) -> list[str]:  # noqa: ANN401
    if NULL_SHAPE in (local, live) or live == []:
        return []
    if isinstance(local, dict) and isinstance(live, dict):
        return [
            problem
            for key, item in local.items()
            for problem in (
                mismatches(item, live[key], f"{path}.{key}")
                if key in live
                else [f"{path}.{key} is missing on GitHub"]
            )
        ]
    if isinstance(local, list) and isinstance(live, list):
        return mismatches(local[0], live[0], f"{path}[]") if local else []
    return [] if local == live else [f"{path} is {local} locally and {live} on GitHub"]


@pytest.fixture(scope="module")
def live_repository() -> str:
    slug = os.environ.get(LIVE_REPOSITORY_VARIABLE)
    if not slug:
        pytest.skip(f"set {LIVE_REPOSITORY_VARIABLE} to a public repository to compare shapes")
    return slug


def live_answer(arguments: list[str]) -> Any:  # noqa: ANN401
    completed = subprocess.run(
        [GH_BINARY, *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
        timeout=LIVE_TIMEOUT_SECONDS,
    )
    return json_documents(completed.stdout)[0]


def local_run(root: Path, arguments: list[str]) -> str:
    answer = run_gh(
        Invocation(
            root=root,
            arguments=arguments,
            read_stdin=lambda: "",
            environment={},
            working_directory=root,
            now=datetime.now(UTC).replace(microsecond=0),
        )
    )
    assert answer.exit_code == 0, answer.stderr
    return answer.stdout


def local_answer(root: Path, arguments: list[str]) -> Any:  # noqa: ANN401
    return json_documents(local_run(root, arguments))[0]


def push_branch(repository: Path, checkout: Path) -> None:
    initialise_bare(repository, BASE_BRANCH)
    checkout.mkdir()
    git(["init", "--initial-branch", BASE_BRANCH], cwd=checkout)
    (checkout / "README.md").write_text("# Board\n")
    git(["add", "--all"], cwd=checkout)
    git(["commit", "--message", "Added: the README"], cwd=checkout)
    git(["checkout", "-b", BRANCH], cwd=checkout)
    (checkout / "README.md").write_text("# Board\n\nA change.\n")
    git(["commit", "--all", "--message", "Changed: the README"], cwd=checkout)
    git(["push", str(repository), BASE_BRANCH, BRANCH], cwd=checkout)


@pytest.fixture(scope="module")
def local_board(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("board")
    write_account(root, BoardAccount(login="example-operator"))
    board = create_board(root, LOCAL_SLUG, True)
    push_branch(board.repository_path, tmp_path_factory.mktemp("checkout") / "work")
    for title in ("Child", "Parent"):
        create = ["issue", "create", "-R", LOCAL_SLUG, "-t", title, "-b", "Body", "-l", "bug"]
        local_run(root, create)
    local_run(root, ["issue", "comment", "1", "-R", LOCAL_SLUG, "-b", "A comment"])
    link = ["api", "-X", "POST", f"repos/{LOCAL_SLUG}/issues/1/dependencies/blocked_by"]
    local_answer(root, [*link, "-F", f"issue_id={issue_id(2)}"])
    pull_request = ["pr", "create", "-R", LOCAL_SLUG, "--head", BRANCH, "--base", BASE_BRANCH]
    local_run(root, [*pull_request, "--draft", "-t", "A change", "-b", "Refs #1"])
    return root


def first_issue_number(slug: str) -> int:
    arguments = ["issue", "list", "--repo", slug, "--state", "all", "--limit", "1"]
    return int(live_answer([*arguments, "--json", "number"])[0]["number"])


@pytest.mark.parametrize("read", sorted(READS))
def test_the_local_gh_answers_in_the_shapes_github_does(
    read: str, live_repository: str, local_board: Path
) -> None:
    number = first_issue_number(live_repository)
    live = shape(live_answer(READS[read](live_repository, number)))
    local = shape(local_answer(local_board, READS[read](LOCAL_SLUG, 1)))
    assert mismatches(local, live, read) == []
