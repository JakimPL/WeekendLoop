import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.unit.conftest import base_policy, write_policy
from weekend_loop.backends import reader_for, with_foreign_activity, writer_for
from weekend_loop.briefing import write_prepared
from weekend_loop.cli import EXIT_OK, main
from weekend_loop.demo.playground import git
from weekend_loop.github import reader_for as github_reader_for
from weekend_loop.github import signed
from weekend_loop.labels import LABEL_COLOUR, board_labels, create_labels, label_arguments
from weekend_loop.local_github.paths import local_repository_path
from weekend_loop.local_github.store import open_board, read_account
from weekend_loop.models import (
    Backend,
    BoardLabel,
    CheckOutcome,
    IssueState,
    Policy,
    PreparedSession,
)
from weekend_loop.policy import policy_at, repo_target
from weekend_loop.preflight import ISSUE_DEPENDENCIES_CHECK, run_preflight
from weekend_loop.runs import ledger_path
from weekend_loop.workbench import run_git

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
SEEDED_ISSUE_COUNT = 7
DARK_MODE_ISSUE = 6
FIRST_ISSUE = 1
ARCHIVED_BRANCH = "weekend/5-archived"
SPEED_BRANCH = "weekend/1-empty-speed"


def local_policy_path(tmp_path: Path) -> Path:
    raw = base_policy(tmp_path)
    raw["repos"]["demo"]["backend"] = Backend.LOCAL.value
    return write_policy(tmp_path, raw)


def seeded(tmp_path: Path) -> Path:
    policy_path = local_policy_path(tmp_path)
    assert main(["--home", str(policy_path), "demo", "up", "--repo-key", "demo"]) == EXIT_OK
    return policy_path


def test_seeding_writes_every_issue_and_the_colleagues_pull_request(tmp_path: Path) -> None:
    policy = policy_at(seeded(tmp_path))
    board = open_board(policy.state_dir, repo_target(policy, "demo").slug)
    assert [issue.number for issue in board.open_issues()] == list(
        range(FIRST_ISSUE, SEEDED_ISSUE_COUNT + 1)
    )
    pull_requests = board.open_pull_requests()
    assert [pull_request.head_branch for pull_request in pull_requests] == ["feat/dark-mode"]
    assert pull_requests[0].draft
    assert pull_requests[0].author == read_account(board.directory.parent).login
    labels = {label.name for label in board.read_index().labels}
    assert {label.name for label in board_labels(policy.labels)} <= labels
    assert {"enhancement", "refactor"} <= labels


def test_the_reader_reports_the_pull_request_that_overlaps_an_issue(tmp_path: Path) -> None:
    policy = policy_at(seeded(tmp_path))
    issues = reader_for(repo_target(policy, "demo"), policy.state_dir).open_issues(100)
    overlapping = {issue.number: issue.open_linked_pull_requests for issue in issues}
    assert overlapping[DARK_MODE_ISSUE] != []
    assert overlapping[FIRST_ISSUE] == []


def test_a_comment_and_a_label_land_on_the_board_and_come_back_out(tmp_path: Path) -> None:
    policy = policy_at(seeded(tmp_path))
    repo = repo_target(policy, "demo")
    writer = writer_for(repo, policy.state_dir, policy.identity, policy.labels.namespace)
    writer.comment_on_issue(FIRST_ISSUE, "A question from the agent.")
    writer.add_labels(FIRST_ISSUE, [policy.labels.needs_input])
    board = open_board(policy.state_dir, repo.slug)
    assert board.read_issue(FIRST_ISSUE).labels[-1] == policy.labels.needs_input
    assert board.comments(FIRST_ISSUE)[0].body == "A question from the agent."
    comments = reader_for(repo, policy.state_dir).issue_comments(FIRST_ISSUE)
    assert [comment.body for comment in comments] == ["A question from the agent."]


def test_the_agents_own_comment_leaves_the_issue_eligible(tmp_path: Path) -> None:
    policy = policy_at(seeded(tmp_path))
    repo = repo_target(policy, "demo")
    writer_for(repo, policy.state_dir, policy.identity, policy.labels.namespace).comment_on_issue(
        FIRST_ISSUE, signed("1. Which unit?", policy.identity.comment_footer, "run-7")
    )
    reader = reader_for(repo, policy.state_dir)
    issue = next(issue for issue in reader.open_issues(100) if issue.number == FIRST_ISSUE)
    enriched = with_foreign_activity(reader, issue, reader.viewer_login())
    assert enriched.last_foreign_activity_at is None


def test_a_label_the_agent_wrote_can_come_off_again(tmp_path: Path) -> None:
    policy = policy_at(seeded(tmp_path))
    repo = repo_target(policy, "demo")
    writer = writer_for(repo, policy.state_dir, policy.identity, policy.labels.namespace)
    writer.add_labels(FIRST_ISSUE, [policy.labels.needs_input])
    writer.remove_labels(FIRST_ISSUE, [policy.labels.needs_input])
    writer.remove_labels(FIRST_ISSUE, [policy.labels.needs_input])
    labels = open_board(policy.state_dir, repo.slug).read_issue(FIRST_ISSUE).labels
    assert policy.labels.needs_input not in labels


def test_a_label_outside_the_agents_namespace_is_refused(tmp_path: Path) -> None:
    policy = policy_at(seeded(tmp_path))
    repo = repo_target(policy, "demo")
    writer = writer_for(repo, policy.state_dir, policy.identity, policy.labels.namespace)
    board = open_board(policy.state_dir, repo.slug)
    before = board.read_issue(FIRST_ISSUE).labels
    with pytest.raises(ValueError, match="weekend:"):
        writer.add_labels(FIRST_ISSUE, ["priority:high"])
    assert board.read_issue(FIRST_ISSUE).labels == before


def push_stacked_branches(policy: Policy, repository: Path, checkout: Path) -> None:
    git(["clone", str(repository), str(checkout)], cwd=repository.parent)
    for branch, change in ((ARCHIVED_BRANCH, "archived"), (SPEED_BRANCH, "speed")):
        git(["checkout", "-b", branch], cwd=checkout)
        (checkout / f"{change}.txt").write_text(f"{change}\n")
        git(["add", "--all"], cwd=checkout)
        git(["commit", "--message", f"Added: {change}"], cwd=checkout)
        git(["push", "origin", branch], cwd=checkout)


def test_a_stacked_pull_request_points_at_its_parent_and_joins_its_stack(tmp_path: Path) -> None:
    policy = policy_at(seeded(tmp_path))
    repo = repo_target(policy, "demo")
    repository = local_repository_path(policy.state_dir, repo.slug)
    push_stacked_branches(policy, repository, tmp_path / "checkout")
    writer = writer_for(repo, policy.state_dir, policy.identity, policy.labels.namespace)
    parent = writer.open_draft_pull_request(ARCHIVED_BRANCH, "Archive", "Refs #5", "main")
    child = writer.open_draft_pull_request(
        SPEED_BRANCH, "Fix the parser", "Refs #1", ARCHIVED_BRANCH
    )
    board = open_board(policy.state_dir, repo.slug)
    [opened] = [
        pull_request
        for pull_request in board.open_pull_requests()
        if pull_request.head_branch == SPEED_BRANCH
    ]
    assert opened.draft and opened.state is IssueState.OPEN
    assert opened.base_branch == ARCHIVED_BRANCH
    assert child.endswith(f"/pull/{opened.number}")
    numbers = [int(url.rsplit("/", 1)[1]) for url in (parent, child)]
    writer.link_stack(numbers)
    assert board.read_index().stacks == [numbers]
    with pytest.raises(subprocess.CalledProcessError):
        writer.open_draft_pull_request(SPEED_BRANCH, "Again", "Refs #1", ARCHIVED_BRANCH)


def test_preflight_reaches_the_local_board_through_its_gh(tmp_path: Path) -> None:
    policy = policy_at(seeded(tmp_path))
    report = run_preflight(policy, "demo", NOW, None)
    checks = {check.name: check for check in report.checks}
    assert checks["gh"].detail.startswith("gh version local")
    access = checks[f"github access to {repo_target(policy, 'demo').slug}"]
    assert access.outcome is CheckOutcome.PASSED and access.detail.startswith("push=true")
    assert checks[ISSUE_DEPENDENCIES_CHECK].outcome is CheckOutcome.PASSED


def test_preflight_blocks_a_run_when_the_board_was_never_seeded(tmp_path: Path) -> None:
    policy = policy_at(local_policy_path(tmp_path))
    report = run_preflight(policy, "demo", NOW, None)
    failures = {check.name: check.detail for check in report.checks if check.outcome.value != "ok"}
    assert "`weekend-loop demo up` builds it" in failures["gh"]


def test_reseeding_rebuilds_the_same_repository(tmp_path: Path) -> None:
    policy_path = seeded(tmp_path)
    policy = policy_at(policy_path)
    repository = local_repository_path(policy.state_dir, repo_target(policy, "demo").slug)
    environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(tmp_path)}
    before = run_git(["rev-parse", "main"], cwd=repository, environment=environment)
    assert main(["--home", str(policy_path), "demo", "reset", "--repo-key", "demo"]) == EXIT_OK
    after = run_git(["rev-parse", "main"], cwd=repository, environment=environment)
    assert before == after


def test_a_reset_clears_the_workbench_and_the_earlier_runs(tmp_path: Path) -> None:
    policy_path = seeded(tmp_path)
    policy = policy_at(policy_path)
    workbench = policy.workspace.workbench_path("demo")
    workbench.mkdir(parents=True, exist_ok=True)
    (workbench / "leftover.txt").write_text("from an earlier run")
    runs = policy.state_dir / "runs" / "20260918-210000-demo"
    runs.mkdir(parents=True, exist_ok=True)
    ledger = ledger_path(policy.state_dir)
    ledger.write_text('{"run_id": "20260918-210000-demo"}\n')
    assert main(["--home", str(policy_path), "demo", "reset", "--repo-key", "demo"]) == EXIT_OK
    assert not workbench.exists()
    assert not runs.exists()
    assert not ledger.exists()


def test_a_reset_removes_the_prepared_triage_it_would_otherwise_orphan(tmp_path: Path) -> None:
    policy_path = seeded(tmp_path)
    policy = policy_at(policy_path)
    prepared = PreparedSession(
        run_id="20260918-210000-demo", repo_key="demo", prepared_at=NOW, question_count=2
    )
    path = write_prepared(policy.state_dir, prepared)
    assert main(["--home", str(policy_path), "demo", "reset", "--repo-key", "demo"]) == EXIT_OK
    assert not path.exists()


def test_a_board_under_a_namespace_of_your_own_takes_its_labels(tmp_path: Path) -> None:
    policy = policy_at(seeded(tmp_path))
    repo = repo_target(policy, "demo")
    writer = writer_for(repo, policy.state_dir, policy.identity, "bot/")
    with pytest.raises(subprocess.CalledProcessError):
        writer.add_labels(FIRST_ISSUE, ["bot/review"])
    review = BoardLabel(name="bot/review", description="Review", colour=LABEL_COLOUR)
    reader = github_reader_for(repo, policy.state_dir)
    assert create_labels(reader, [label_arguments(repo.slug, review)]) is None
    writer.add_labels(FIRST_ISSUE, ["bot/review"])
    assert "bot/review" in open_board(policy.state_dir, repo.slug).read_issue(FIRST_ISSUE).labels
    with pytest.raises(ValueError, match="bot/"):
        writer.add_labels(FIRST_ISSUE, ["weekend:review"])


def test_a_board_issue_is_blocked_only_by_issues_still_open(tmp_path: Path) -> None:
    policy = policy_at(seeded(tmp_path))
    board = open_board(policy.state_dir, repo_target(policy, "demo").slug)
    second = board.read_issue(2)
    board.write_issue(second.model_copy(update={"blocked_by": [1, 99]}))
    issues = reader_for(repo_target(policy, "demo"), policy.state_dir).open_issues(100)
    assert {issue.number: issue.blocked_by for issue in issues}[2] == [1]
