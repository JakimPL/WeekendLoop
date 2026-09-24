import json
from pathlib import Path

import pytest

from weekend_loop.demo.seed import (
    GITHUB_REPO_KEY,
    SeedIssue,
    SeedRunner,
    example_labels,
    issues_directory,
    load_seed_issues,
    seed_environment,
    seed_repository,
)
from weekend_loop.labels import board_labels
from weekend_loop.models import IneligibilityReason, Policy, Verdict

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = REPOSITORY_ROOT / "examples"
CREATED_ISSUE_URL = "https://github.com/owner/repository/issues/99\n"
TEMPLATE_SECTIONS = ("## Business requirement", "## Goal", "## Scope")
EXPECTED_ISSUE_COUNT = 7


class ScriptedRunner(SeedRunner):
    def __init__(self, responses: dict[str, str]) -> None:
        super().__init__(dry_run=False, environment={})
        self.responses = responses

    def run(self, arguments: list[str], stdin: str | None = None) -> str:
        command = " ".join(arguments)
        self.log.append(command)
        return next(
            (output for prefix, output in self.responses.items() if command.startswith(prefix)),
            "",
        )


def listed_issues(issues: list[SeedIssue]) -> str:
    return json.dumps(
        [{"number": number, "title": issue.title} for number, issue in enumerate(issues, 1)]
    )


@pytest.fixture(scope="module")
def issues() -> list[SeedIssue]:
    return load_seed_issues(issues_directory(EXAMPLES))


def test_seven_issues_with_unique_keys(issues: list[SeedIssue]) -> None:
    assert len(issues) == EXPECTED_ISSUE_COUNT
    assert len({issue.key for issue in issues}) == EXPECTED_ISSUE_COUNT


def test_every_issue_follows_the_template(issues: list[SeedIssue]) -> None:
    for issue in issues:
        for section in TEMPLATE_SECTIONS:
            assert section in issue.body, f"{issue.key} lacks {section}"


def test_expected_outcomes_cover_every_pilot_scenario(issues: list[SeedIssue]) -> None:
    verdicts = [issue.expected_verdict for issue in issues if issue.expected_verdict is not None]
    assert verdicts.count(Verdict.EXECUTE) == 2
    assert verdicts.count(Verdict.NEEDS_INPUT) == 1
    assert verdicts.count(Verdict.SKIP) == 2
    ineligibilities = {issue.expected_ineligibility for issue in issues}
    assert IneligibilityReason.NEVER_LABEL in ineligibilities
    assert IneligibilityReason.OPEN_LINKED_PULL_REQUEST in ineligibilities


def test_every_executable_issue_has_a_hidden_acceptance_test(issues: list[SeedIssue]) -> None:
    for issue in issues:
        if issue.expected_verdict is Verdict.EXECUTE:
            assert issue.acceptance_test is not None, issue.key
            assert (EXAMPLES / "acceptance" / issue.acceptance_test).is_file(), issue.key


def test_dry_run_plans_labels_issues_and_the_overlapping_pull_request(
    issues: list[SeedIssue], workspace_policy: Policy
) -> None:
    policy = workspace_policy
    runner = SeedRunner(dry_run=True, environment={})
    outcome = seed_repository(runner, policy.repos["demo"], policy.labels, issues)
    log = "\n".join(runner.log)
    for label in [*board_labels(policy.labels), *example_labels()]:
        assert label.name in log
    for issue in issues:
        assert issue.title in log
    assert log.count("gh issue create") == EXPECTED_ISSUE_COUNT
    assert log.count("gh pr create") == 1
    assert sorted(outcome.issue_numbers.values()) == list(range(1, EXPECTED_ISSUE_COUNT + 1))


def test_applying_runs_gh_as_the_pilot_token_owner(
    tmp_path: Path, workspace_policy: Policy
) -> None:
    token_file = tmp_path / "gh-pilot.token"
    token_file.write_text("pilot-token\n")
    policy = workspace_policy
    repo = policy.repos["demo"].model_copy(update={"token_file": token_file})
    environment = seed_environment(repo, tmp_path)
    assert environment["GH_TOKEN"] == "pilot-token"
    assert environment["GH_CONFIG_DIR"] == str(tmp_path / "gh-config")


def test_a_seeded_repository_is_left_as_it_is(
    issues: list[SeedIssue], workspace_policy: Policy
) -> None:
    policy = workspace_policy
    repo = policy.repos[GITHUB_REPO_KEY]
    runner = ScriptedRunner(
        {
            "gh issue list": listed_issues(issues),
            f"gh api repos/{repo.slug}/git/matching-refs": "1",
            "gh pr list": "1",
        }
    )
    outcome = seed_repository(runner, repo, policy.labels, issues)
    log = "\n".join(runner.log)
    assert outcome.created_issues == []
    assert outcome.opened_pull_requests == []
    assert outcome.issue_numbers == {issue.key: number for number, issue in enumerate(issues, 1)}
    for write in ("gh issue create", "gh pr create", "--method POST", "--method PUT"):
        assert write not in log


def test_a_half_seeded_repository_is_completed(
    issues: list[SeedIssue], workspace_policy: Policy
) -> None:
    policy = workspace_policy
    repo = policy.repos[GITHUB_REPO_KEY]
    runner = ScriptedRunner(
        {
            "gh issue list": listed_issues(issues[:3]),
            "gh issue create": CREATED_ISSUE_URL,
            f"gh api repos/{repo.slug}/git/matching-refs": "1",
            "gh pr list": "0",
        }
    )
    outcome = seed_repository(runner, repo, policy.labels, issues)
    log = "\n".join(runner.log)
    assert outcome.created_issues == [issue.key for issue in issues[3:]]
    assert outcome.opened_pull_requests == ["feat/dark-mode"]
    assert log.count("gh issue create") == EXPECTED_ISSUE_COUNT - 3
    assert "--method POST" not in log
