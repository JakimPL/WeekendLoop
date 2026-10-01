from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.unit.conftest import base_policy, write_policy
from weekend_loop.demo.playground import git
from weekend_loop.demo.publish import (
    OPERATOR_ACTION_EXIT_CODE,
    OperatorActionRequiredError,
    SetupContext,
    SetupStep,
    branch_names,
    github_target,
    is_permission_refusal,
    is_workflow_rejection,
    publish_playground,
    readiness,
    run_steps,
    seed_issues,
    seed_summary,
)
from weekend_loop.demo.seed import GITHUB_REPO_KEY, SeedOutcome
from weekend_loop.models import (
    Backend,
    CheckOutcome,
    Policy,
    PreflightCheck,
    PreflightReport,
    RepoMode,
)
from weekend_loop.policy import policy_at, repo_target

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = REPOSITORY_ROOT / "examples"
WORKFLOW_REJECTION = (
    " ! [remote rejected] main -> main (refusing to allow a Personal Access Token to create or"
    " update workflow `.github/workflows/tests.yml` without `workflow` scope)"
)


def context_for(tmp_path: Path, remote_url: str) -> SetupContext:
    policy = policy_at(write_policy(tmp_path, base_policy(tmp_path)))
    return SetupContext(
        policy=policy,
        repo_key="demo",
        repo=repo_target(policy, "demo"),
        remote_url=remote_url,
        examples=EXAMPLES,
    )


def empty_remote(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(["init", "--bare", "--initial-branch", "main"], cwd=remote)
    return remote


def test_branch_names_come_from_the_heads_listing() -> None:
    output = "1a2b\trefs/heads/main\n3c4d\trefs/heads/feat/dark-mode\n"
    assert branch_names(output) == ["main", "feat/dark-mode"]
    assert branch_names("") == []


def test_the_missing_workflow_permission_is_recognised() -> None:
    assert is_workflow_rejection(WORKFLOW_REJECTION)
    assert not is_workflow_rejection(" ! [rejected] main -> main (fetch first)")


def test_an_empty_repository_receives_the_mockup_once(tmp_path: Path) -> None:
    remote = empty_remote(tmp_path)
    context = context_for(tmp_path, str(remote))
    assert publish_playground(context).startswith("pushed main, feat/dark-mode")
    tracked = git(["ls-tree", "-r", "--name-only", "main"], cwd=remote).splitlines()
    assert "pocketchat/chat.py" in tracked
    assert ".venv" not in {path.split("/")[0] for path in tracked}
    overlap = git(["diff", "--name-only", "main", "feat/dark-mode"], cwd=remote).split()
    assert overlap == ["pocketchat/static/dark-mode.css"]
    assert "already carries the mockup" in publish_playground(context)


def test_a_missing_colleague_branch_is_pushed_on_its_own(tmp_path: Path) -> None:
    remote = empty_remote(tmp_path)
    context = context_for(tmp_path, str(remote))
    publish_playground(context)
    git(["branch", "-D", "feat/dark-mode"], cwd=remote)
    assert publish_playground(context).startswith("pushed feat/dark-mode")


def test_a_missing_token_asks_the_operator_for_one(tmp_path: Path) -> None:
    context = context_for(tmp_path, str(empty_remote(tmp_path)))
    context.repo.token_path().unlink()
    with pytest.raises(OperatorActionRequiredError, match="fine-grained GitHub token"):
        publish_playground(context)


def test_an_unreachable_repository_asks_the_operator_to_create_it(tmp_path: Path) -> None:
    context = context_for(tmp_path, str(tmp_path / "missing.git"))
    with pytest.raises(OperatorActionRequiredError, match="empty private repository"):
        publish_playground(context)


def test_a_repository_with_other_history_asks_the_operator(tmp_path: Path) -> None:
    remote = empty_remote(tmp_path)
    context = context_for(tmp_path, str(remote))
    publish_playground(context)
    git(["branch", "-m", "main", "trunk"], cwd=remote)
    with pytest.raises(OperatorActionRequiredError, match="trunk"):
        publish_playground(context)


def test_a_run_without_a_terminal_stops_at_the_first_operator_action(tmp_path: Path) -> None:
    context = context_for(tmp_path, str(tmp_path / "missing.git"))
    calls: list[str] = []

    def later_step(_: SetupContext) -> str:
        calls.append("later")
        return "ran"

    steps = (
        SetupStep("publish", publish_playground),
        SetupStep("later", later_step),
    )
    assert run_steps(steps, context, interactive=False) == OPERATOR_ACTION_EXIT_CODE
    assert calls == []


def test_the_setup_prepares_only_a_github_target(workspace_policy: Policy) -> None:
    local = workspace_policy.repos["demo"].model_copy(update={"backend": Backend.LOCAL})
    policy = workspace_policy.model_copy(
        update={"repos": {**workspace_policy.repos, "demo": local}}
    )
    assert github_target(policy, GITHUB_REPO_KEY).slug == local.slug
    with pytest.raises(ValueError, match="local backend"):
        github_target(policy, "demo")


def test_a_refused_permission_is_recognised() -> None:
    assert is_permission_refusal("HTTP 403: Resource not accessible by personal access token")
    assert not is_permission_refusal("HTTP 422: Validation Failed")


def test_the_seed_summary_says_what_changed() -> None:
    fresh = SeedOutcome(
        issue_numbers={"new-chat": 1, "readme": 2},
        created_issues=["new-chat", "readme"],
        opened_pull_requests=["feat/dark-mode"],
    )
    tests = {"2": "pilot/acceptance/test_readme.py", "1": "pilot/acceptance/test_new_chat.py"}
    assert seed_summary(fresh, tests) == (
        "created 2 of 2 issues, opened 1 colleague draft pull request(s), hidden tests for #1, #2"
    )
    again = fresh.model_copy(update={"created_issues": [], "opened_pull_requests": []})
    assert seed_summary(again, tests).startswith("all 2 issues were there, opened 0")


def test_seeding_without_a_token_asks_the_operator_for_one(tmp_path: Path) -> None:
    context = context_for(tmp_path, str(empty_remote(tmp_path)))
    context.repo.token_path().unlink()
    with pytest.raises(OperatorActionRequiredError, match="fine-grained GitHub token"):
        seed_issues(context)


def preflight_report(checks: list[PreflightCheck]) -> PreflightReport:
    return PreflightReport(
        repo_key=GITHUB_REPO_KEY,
        mode=RepoMode.EXECUTE,
        checked_at=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
        checks=checks,
    )


def test_a_clear_preflight_reports_ready_with_its_warnings() -> None:
    report = preflight_report(
        [
            PreflightCheck(name="git", outcome=CheckOutcome.PASSED, required=True, detail="2.43"),
            PreflightCheck(
                name="socat", outcome=CheckOutcome.FAILED, required=False, detail="gone"
            ),
        ]
    )
    assert readiness(report) == "clear to run, 2 checks\n  - socat: gone"


def test_a_blocked_preflight_asks_the_operator_for_each_blocker() -> None:
    report = preflight_report(
        [
            PreflightCheck(
                name="claude oauth token",
                outcome=CheckOutcome.FAILED,
                required=True,
                detail="missing",
            ),
            PreflightCheck(name="git", outcome=CheckOutcome.PASSED, required=True, detail="2.43"),
        ]
    )
    with pytest.raises(OperatorActionRequiredError, match="claude oauth token: missing"):
        readiness(report)
