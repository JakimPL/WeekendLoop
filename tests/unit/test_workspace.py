from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from weekend_loop.cli import EXIT_OK, main
from weekend_loop.models import Workspace
from weekend_loop.workspace import (
    DEFAULT_HOME_NAME,
    HOME_VARIABLE,
    WorkspaceError,
    home_directory,
    missing_parts,
    open_workspace,
)
from weekend_loop.workspace_init import initialise_workspace

OPERATOR_HOME = Path("/operator-home")


def test_the_flag_beats_the_variable_and_the_variable_beats_the_default(tmp_path: Path) -> None:
    named = tmp_path / "named"
    flagged = tmp_path / "flagged"
    environment = {HOME_VARIABLE: str(named)}
    assert home_directory(flagged, environment, tmp_path) == flagged
    assert home_directory(None, environment, tmp_path) == named
    assert home_directory(None, {}, tmp_path) == tmp_path / DEFAULT_HOME_NAME


def test_an_empty_home_variable_is_an_error_rather_than_a_quiet_default(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError, match=HOME_VARIABLE):
        home_directory(None, {HOME_VARIABLE: "  "}, tmp_path)


def test_a_directory_that_was_never_initialised_names_what_it_lacks(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError, match="weekend-loop init"):
        open_workspace(tmp_path)


def test_initialising_writes_the_tree_the_run_needs(tmp_path: Path) -> None:
    initialise_workspace(tmp_path, OPERATOR_HOME, force=False, example=None)
    workspace = open_workspace(tmp_path)
    assert missing_parts(tmp_path) == []
    assert workspace.config_path.is_file()
    assert workspace.worker_fence.is_file() and workspace.assessor_fence.is_file()
    assert (workspace.agent_home / ".claude" / "CLAUDE.md").is_file()


def test_the_workspace_keeps_its_secrets_to_the_operator(tmp_path: Path) -> None:
    previous = os.umask(0o000)
    try:
        initialise_workspace(tmp_path, OPERATOR_HOME, force=False, example=None)
    finally:
        os.umask(previous)
    workspace = Workspace(root=tmp_path)
    for path in (workspace.root, workspace.secrets_dir, workspace.state_dir, workspace.agent_home):
        assert stat.S_IMODE(path.stat().st_mode) == 0o700, path
    assert stat.S_IMODE(workspace.config_path.stat().st_mode) == 0o600


def test_initialising_again_keeps_the_configuration_the_operator_wrote(tmp_path: Path) -> None:
    initialise_workspace(tmp_path, OPERATOR_HOME, force=False, example=None)
    workspace = Workspace(root=tmp_path)
    workspace.config_path.write_text("repos: {}\n")
    initialise_workspace(tmp_path, OPERATOR_HOME, force=True, example=None)
    assert workspace.config_path.read_text() == "repos: {}\n"


def test_the_init_command_prepares_a_workspace_that_opens(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    assert main(["--home", str(root), "init"]) == EXIT_OK
    assert open_workspace(root).root == root


def test_the_workspace_can_be_named_before_or_after_the_command(tmp_path: Path) -> None:
    before = tmp_path / "before"
    after = tmp_path / "after"
    assert main(["--home", str(before), "init"]) == EXIT_OK
    assert main(["init", "--home", str(after)]) == EXIT_OK
    assert open_workspace(before).root == before
    assert open_workspace(after).root == after
