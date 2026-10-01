import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Final

import pytest

from weekend_loop.github import GitHubReader
from weekend_loop.local_github.paths import board_root
from weekend_loop.local_github.payloads import issue_id
from weekend_loop.local_github.store import board_at, create_board, write_account
from weekend_loop.local_github.wrapper import install_wrapper, is_local_board_wrapper
from weekend_loop.models import BoardAccount

SLUG: Final[str] = "example-org/example-board"
OPERATOR: Final[str] = "example-operator"
PARALLEL_CALLS: Final[int] = 8
WRAPPER_TIMEOUT_SECONDS: Final[int] = 60


@pytest.fixture
def wrapper(tmp_path: Path) -> Path:
    state = tmp_path / "state"
    write_account(board_root(state), BoardAccount(login=OPERATOR))
    create_board(board_root(state), SLUG, True)
    return install_wrapper(state)


def call(wrapper: Path, arguments: list[str], stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(wrapper), *arguments],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        timeout=WRAPPER_TIMEOUT_SECONDS,
    )


def created_issue(wrapper: Path, title: str) -> int:
    arguments = ["issue", "create", "-R", SLUG, "-t", title, "--body-file", "-"]
    answer = call(wrapper, arguments, f"Body of {title}\n")
    assert answer.returncode == 0, answer.stderr
    return int(answer.stdout.strip().rsplit("/", 1)[1])


def test_the_wrapper_is_recognised_and_other_scripts_are_not(wrapper: Path, tmp_path: Path) -> None:
    assert wrapper.name == "gh" and wrapper.parent.name == "bin"
    assert is_local_board_wrapper(wrapper)
    plain = tmp_path / "gh"
    plain.write_text('#!/bin/sh\nexec /usr/bin/gh "$@"\n')
    assert not is_local_board_wrapper(plain)
    assert not is_local_board_wrapper(tmp_path / "absent")


def test_the_wrapper_answers_reads_bodies_and_fails_like_gh(wrapper: Path) -> None:
    assert call(wrapper, ["--version"], "").stdout.startswith("gh version local")
    number = created_issue(wrapper, "README still has TODOs")
    assert board_at(board_root(wrapper.parents[2]), SLUG).read_issue(number).body.startswith("Body")
    refused = call(wrapper, ["issue", "view", "99", "-R", SLUG], "")
    assert refused.returncode == 1 and refused.stdout == ""
    assert "number of 99" in refused.stderr


def test_parallel_writes_keep_every_label_and_comment(wrapper: Path) -> None:
    number = created_issue(wrapper, "Ratings for answers")
    names = [f"weekend:test-{index}" for index in range(PARALLEL_CALLS)]
    for name in names:
        assert call(wrapper, ["label", "create", name, "-R", SLUG], "").returncode == 0

    def write(index: int) -> tuple[int, int]:
        edit = ["issue", "edit", str(number), "-R", SLUG, "--add-label", names[index]]
        comment = ["issue", "comment", str(number), "-R", SLUG, "-b", f"note {index}"]
        return call(wrapper, edit, "").returncode, call(wrapper, comment, "").returncode

    with ThreadPoolExecutor(max_workers=PARALLEL_CALLS) as pool:
        codes = list(pool.map(write, range(PARALLEL_CALLS)))
    assert codes == [(0, 0)] * PARALLEL_CALLS
    board = board_at(board_root(wrapper.parents[2]), SLUG)
    assert sorted(board.read_issue(number).labels) == sorted(names)
    assert len({comment.id for comment in board.comments(number)}) == PARALLEL_CALLS


def test_the_github_reader_reads_the_local_board(wrapper: Path, tmp_path: Path) -> None:
    parent = created_issue(wrapper, "The New chat button does nothing")
    child = created_issue(wrapper, "Count the new chats")
    link = ["api", "-X", "POST", f"repos/{SLUG}/issues/{child}/dependencies/blocked_by"]
    assert call(wrapper, [*link, "-F", f"issue_id={issue_id(parent)}"], "").returncode == 0
    reader = GitHubReader(SLUG, "local-board", tmp_path / "gh-config", str(wrapper))
    assert reader.viewer_login() == OPERATOR
    issues = {issue.number: issue for issue in reader.open_issues(50)}
    assert issues[child].blocked_by == [parent]
    assert issues[parent].title == "The New chat button does nothing"
    assert reader.issue_comments(parent) == []
