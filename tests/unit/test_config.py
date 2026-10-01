from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from weekend_loop.backends import board_operator_login
from weekend_loop.cli import EXIT_OK, EXIT_REFUSED, main
from weekend_loop.config_errors import ConfigError
from weekend_loop.config_view import default_keys, reference_document, resolved_document
from weekend_loop.models import (
    DEFAULT_ENVELOPE_USD,
    DEFAULT_LABEL_NAMESPACE,
    DEFAULT_PARALLEL_WORKERS,
    Issue,
    IssueComment,
    PullRequest,
    Workspace,
)
from weekend_loop.policy import policy_at
from weekend_loop.workspace_init import initialise_workspace

OPERATOR_HOME = Path("/operator-home")
TOKEN_LOGIN = "the-token-account"


class StandInReader:
    slug = "example-org/example-repo"

    def viewer_login(self) -> str:
        return TOKEN_LOGIN

    def open_issues(self, limit: int) -> list[Issue]:
        return []

    def open_pull_requests(self, limit: int) -> list[PullRequest]:
        return []

    def issue_comments(self, issue_number: int) -> list[IssueComment]:
        return []


MINIMAL_CONFIG: dict[str, Any] = {
    "repos": {
        "myrepo": {
            "slug": "example-org/example-repo",
            "mode": "dry_run",
            "backend": "github",
            "gate_commands": ["pytest -q"],
        }
    },
    "identity": {"git_author_email": "operator@example.invalid"},
    "schedule": {"repo_key": "myrepo"},
}


def workspace_with(tmp_path: Path, document: dict[str, Any]) -> Path:
    initialise_workspace(tmp_path, OPERATOR_HOME, force=False, example=None)
    Workspace(root=tmp_path).config_path.write_text(yaml.safe_dump(document))
    return tmp_path


def test_a_repository_and_an_identity_are_enough_to_run(tmp_path: Path) -> None:
    policy = policy_at(workspace_with(tmp_path, MINIMAL_CONFIG))
    assert policy.budget.envelope_usd == DEFAULT_ENVELOPE_USD
    assert policy.labels.auto == f"{DEFAULT_LABEL_NAMESPACE}auto"
    assert policy.repos["myrepo"].token_path() == policy.workspace.repository_token_path("myrepo")
    assert policy.repos["myrepo"].conventions_prompt is None


def test_a_key_nobody_reads_is_refused_by_name(tmp_path: Path) -> None:
    document = {**MINIMAL_CONFIG, "budget": {"envelope_usd": 5.0, "envelopes_usd": 5.0}}
    with pytest.raises(ConfigError, match="budget.envelopes_usd: unknown key"):
        policy_at(workspace_with(tmp_path, document))


def test_a_misspelled_key_is_told_what_it_probably_meant(tmp_path: Path) -> None:
    repos = {"myrepo": {**MINIMAL_CONFIG["repos"]["myrepo"], "base_brunch": "main"}}
    with pytest.raises(ConfigError, match="did you mean base_branch"):
        policy_at(workspace_with(tmp_path, {**MINIMAL_CONFIG, "repos": repos}))


def test_a_label_outside_the_namespace_is_refused_when_the_config_loads(tmp_path: Path) -> None:
    document = {**MINIMAL_CONFIG, "labels": {"review": "other:review"}}
    with pytest.raises(ConfigError, match="labels.review"):
        policy_at(workspace_with(tmp_path, document))


def test_a_namespace_of_your_own_renames_every_label(tmp_path: Path) -> None:
    document = {**MINIMAL_CONFIG, "labels": {"namespace": "bot/"}}
    labels = policy_at(workspace_with(tmp_path, document)).labels
    assert labels.auto == "bot/auto"
    assert labels.needs_input == "bot/needs-input"


def test_the_resolved_configuration_names_the_workspace_and_fills_the_defaults(
    tmp_path: Path,
) -> None:
    policy = policy_at(workspace_with(tmp_path, MINIMAL_CONFIG))
    printed = resolved_document(policy)
    assert str(tmp_path) in printed
    assert "envelope_usd" in printed
    assert "budget.envelope_usd" in default_keys(policy)
    assert "repos.myrepo.slug" not in default_keys(policy)


def test_the_reference_document_carries_every_section() -> None:
    printed = reference_document()
    for section in ("repos", "identity", "schedule", "budget", "usage", "models", "labels"):
        assert f"{section}:" in printed
    assert f"parallel: {DEFAULT_PARALLEL_WORKERS}" in printed
    assert "shared_paths: []" in printed


def test_a_run_works_one_issue_at_a_time_until_the_operator_says_otherwise(
    tmp_path: Path,
) -> None:
    policy = policy_at(workspace_with(tmp_path, MINIMAL_CONFIG))
    assert policy.worker.parallel == DEFAULT_PARALLEL_WORKERS
    assert policy.worker.shared_paths == []


def test_the_operator_names_how_many_issues_run_at_once_and_which_paths_they_share(
    tmp_path: Path,
) -> None:
    document = {
        **MINIMAL_CONFIG,
        "worker": {"parallel": 3, "shared_paths": ["CHANGELOG.md", "docs/generated/**"]},
    }
    worker = policy_at(workspace_with(tmp_path, document)).worker
    assert worker.parallel == 3
    assert worker.shared_paths == ["CHANGELOG.md", "docs/generated/**"]


def test_fewer_than_one_worker_at_a_time_is_refused(tmp_path: Path) -> None:
    document = {**MINIMAL_CONFIG, "worker": {"parallel": 0}}
    with pytest.raises(ConfigError, match="worker.parallel"):
        policy_at(workspace_with(tmp_path, document))


def test_worktrees_live_beside_the_checkout_of_their_repository(tmp_path: Path) -> None:
    workspace = Workspace(root=tmp_path)
    assert workspace.worktrees_path("myrepo") == tmp_path / "work" / "myrepo-worktrees"
    assert (
        workspace.worktree_path("myrepo", "weekend/7-slug")
        == tmp_path / "work" / "myrepo-worktrees" / "weekend" / "7-slug"
    )


def test_the_config_command_refuses_a_broken_file_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    document = {**MINIMAL_CONFIG, "identity": {"git_authour_email": "operator@example.invalid"}}
    root = workspace_with(tmp_path, document)
    assert main(["--home", str(root), "config", "--resolved"]) == EXIT_REFUSED
    assert "did you mean git_author_email" in capsys.readouterr().err


def test_the_config_command_prints_what_the_workspace_holds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = workspace_with(tmp_path, MINIMAL_CONFIG)
    assert main(["--home", str(root), "config"]) == EXIT_OK
    assert "example-org/example-repo" in capsys.readouterr().out


def test_the_operator_can_be_someone_other_than_the_token(tmp_path: Path) -> None:
    document = {
        **MINIMAL_CONFIG,
        "identity": {**MINIMAL_CONFIG["identity"], "operator_login": "the-human"},
    }
    policy = policy_at(workspace_with(tmp_path, document))
    assert board_operator_login(policy.identity, StandInReader()) == "the-human"


def test_without_an_operator_login_the_token_account_is_the_operator(tmp_path: Path) -> None:
    policy = policy_at(workspace_with(tmp_path, MINIMAL_CONFIG))
    assert board_operator_login(policy.identity, StandInReader()) == TOKEN_LOGIN


def test_a_memory_cap_larger_than_the_pool_is_refused(tmp_path: Path) -> None:
    document = {
        **MINIMAL_CONFIG,
        "resources": {"memory_pool_gb": 8.0, "gate_memory_gb": 10.0},
    }
    with pytest.raises(ConfigError, match="gate_memory_gb exceeds memory_pool_gb"):
        policy_at(workspace_with(tmp_path, document))
