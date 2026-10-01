from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.support.fakes import (
    FAKE_CLAUDE,
    FAKE_GH,
    FAKE_SANDBOX_TOOL,
    install_confinement_fakes,
    install_fake,
)
from tests.unit.conftest import base_policy, issue_payload, write_github_data, write_policy
from weekend_loop.backends import BoardReader, BoardWriter, reader_for, writer_for
from weekend_loop.demo.playground import git
from weekend_loop.demo.seed import SeedIssue, create_issue
from weekend_loop.github import reader_for as github_reader_for
from weekend_loop.labels import create_labels, label_commands
from weekend_loop.local_github.paths import board_root
from weekend_loop.local_github.repository import initialise_bare
from weekend_loop.local_github.store import create_board, write_account
from weekend_loop.local_github.wrapper import install_wrapper
from weekend_loop.models import Backend, BoardAccount, Policy, RepoTarget
from weekend_loop.policy import policy_at, repo_target

BACKENDS = (Backend.LOCAL, Backend.GITHUB)
REPO_KEY = "demo"
OPERATOR = "example-operator"
ISSUE_TITLE = "Empty speed field"
ISSUE_BODY = "## Business requirement\nThe parser crashes on an empty field.\n"
BRANCH = "weekend/1-empty-speed-field"


@dataclass(frozen=True)
class BoardFixture:
    backend: Backend
    policy: Policy
    repo: RepoTarget
    reader: BoardReader
    writer: BoardWriter
    binaries: Path

    def calls(self) -> list[list[str]]:
        log = self.binaries / "gh-calls.jsonl"
        if not log.is_file():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    def labels_of(self, issue_number: int) -> list[str]:
        if self.backend is Backend.LOCAL:
            issues = self.reader.open_issues(100)
            return next(issue.labels for issue in issues if issue.number == issue_number)
        written = [call for call in self.calls() if call[:2] == ["issue", "edit"]]
        added: list[str] = []
        for call in written:
            if "--add-label" in call:
                added.extend(call[call.index("--add-label") + 1].split(","))
            if "--remove-label" in call:
                removed = call[call.index("--remove-label") + 1].split(",")
                added = [label for label in added if label not in removed]
        return added

    def comments_of(self, issue_number: int) -> list[str]:
        if self.backend is Backend.LOCAL:
            return [comment.body for comment in self.reader.issue_comments(issue_number)]
        log = self.binaries / "gh-bodies.jsonl"
        if not log.is_file():
            return []
        written = [json.loads(line) for line in log.read_text().splitlines()]
        return [
            entry["body"] for entry in written if entry["arguments"][:2] == ["issue", "comment"]
        ]


def seed_issue(number: int) -> SeedIssue:
    return SeedIssue(
        key=f"issue-{number}",
        title=ISSUE_TITLE,
        body=ISSUE_BODY,
        labels=["enhancement"],
        expected_verdict=None,
        expected_ineligibility=None,
        acceptance_test=None,
        overlapping_branch=None,
    )


def push_branch(repository: Path, checkout: Path, base_branch: str) -> None:
    checkout.mkdir()
    git(["init", "--initial-branch", base_branch], cwd=checkout)
    (checkout / "parser.py").write_text("SPEED = 0\n")
    git(["add", "--all"], cwd=checkout)
    git(["commit", "--message", "Added: the parser"], cwd=checkout)
    git(["checkout", "-b", BRANCH], cwd=checkout)
    (checkout / "parser.py").write_text("SPEED = None\n")
    git(["commit", "--all", "--message", "Fixed: an empty speed field"], cwd=checkout)
    git(["push", str(repository), base_branch, BRANCH], cwd=checkout)


def build_local(policy: Policy, repo: RepoTarget, numbers: list[int], checkout: Path) -> None:
    root = board_root(policy.state_dir)
    write_account(root, BoardAccount(login=OPERATOR))
    board = create_board(root, repo.slug, True)
    initialise_bare(board.repository_path, repo.base_branch)
    push_branch(board.repository_path, checkout, repo.base_branch)
    install_wrapper(policy.state_dir)
    commands = github_reader_for(repo, policy.state_dir)
    assert create_labels(commands, label_commands(policy, repo)) is None
    for number in numbers:
        assert create_issue(commands, repo.slug, seed_issue(number)) == number


def build_github(binaries: Path, numbers: list[int]) -> None:
    payloads: list[dict[str, Any]] = [
        issue_payload(number, ISSUE_TITLE, ISSUE_BODY, ["enhancement"], []) for number in numbers
    ]
    write_github_data(binaries, issues=payloads, pull_requests=[], comments={}, push=True)


@pytest.fixture
def fake_binaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "bin"
    directory.mkdir()
    install_fake(directory, "claude", FAKE_CLAUDE)
    install_fake(directory, "gh", FAKE_GH)
    install_fake(directory, "socat", FAKE_SANDBOX_TOOL)
    install_fake(directory, "bwrap", FAKE_SANDBOX_TOOL)
    install_confinement_fakes(directory)
    monkeypatch.setenv("PATH", f"{directory}:/usr/bin:/bin")
    return directory


@pytest.fixture(params=BACKENDS, ids=[backend.value for backend in BACKENDS])
def board(request: pytest.FixtureRequest, tmp_path: Path, fake_binaries: Path) -> BoardFixture:
    backend: Backend = request.param
    raw = base_policy(tmp_path)
    raw["repos"][REPO_KEY]["backend"] = backend.value
    policy = policy_at(write_policy(tmp_path, raw))
    repo = repo_target(policy, REPO_KEY)
    numbers = [1]
    if backend is Backend.LOCAL:
        build_local(policy, repo, numbers, tmp_path / "checkout")
    else:
        build_github(fake_binaries, numbers)
    return BoardFixture(
        backend=backend,
        policy=policy,
        repo=repo,
        reader=reader_for(repo, policy.state_dir),
        writer=writer_for(repo, policy.state_dir, policy.identity, policy.labels.namespace),
        binaries=fake_binaries,
    )
