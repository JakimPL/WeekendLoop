import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
import yaml

from tests.support.fakes import install_socket_filter
from tests.support.operators import ScriptedOperator
from tests.unit.conftest import base_policy, write_policy
from weekend_loop.briefing import briefing_directory, prepared_path, write_prepared
from weekend_loop.cli import EXIT_OK, EXIT_REFUSED, main
from weekend_loop.demo import messages
from weekend_loop.demo.lifecycle import (
    DemoRefusalError,
    demo_environment,
    demo_remove,
    demo_reset,
    demo_up,
)
from weekend_loop.demo.marker import is_demo, write_marker
from weekend_loop.demo.playground import git
from weekend_loop.local_github.paths import local_repository_path, wrapper_path
from weekend_loop.local_github.store import open_board
from weekend_loop.lock import run_lock
from weekend_loop.models import Backend, PreparedSession, Workspace
from weekend_loop.policy import policy_at, repo_target
from weekend_loop.runs import ledger_path
from weekend_loop.setup.outcomes import Mark
from weekend_loop.setup.steps import SetupResult
from weekend_loop.workspace import DEFAULT_HOME_NAME

EXAMPLES: Final[Path] = Path(__file__).resolve().parents[2] / "examples"
REPO_KEY: Final[str] = "demo"
CLAUDE_TOKEN: Final[str] = "sk-ant-oat01-example"
NOW: Final[datetime] = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
SECRET_MODE: Final[int] = 0o600
SEEDED_ISSUES: Final[list[int]] = [1, 2, 3, 4, 5, 6, 7]


@pytest.fixture
def operator_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def demo_root(fake_binaries: Path, operator_home: Path) -> Path:
    install_socket_filter(fake_binaries)
    return operator_home / ".weekend-loop-demo"


def up(root: Path, operator: ScriptedOperator) -> SetupResult:
    lines: list[str] = []
    return demo_up(root, EXAMPLES, REPO_KEY, operator, NOW, lines.append)


def marks(result: SetupResult) -> dict[str, Mark]:
    return {outcome.name: outcome.mark for outcome in result.outcomes}


def open_numbers(root: Path) -> list[int]:
    policy = policy_at(root)
    board = open_board(policy.state_dir, repo_target(policy, REPO_KEY).slug)
    return [issue.number for issue in board.open_issues()]


def main_commit(root: Path) -> str:
    policy = policy_at(root)
    repository = local_repository_path(policy.state_dir, repo_target(policy, REPO_KEY).slug)
    return git(["rev-parse", "main"], cwd=repository).strip()


class TestPocketchatLifecycle:
    def test_up_builds_everything_from_nothing(self, demo_root: Path) -> None:
        operator = ScriptedOperator([CLAUDE_TOKEN], True)
        result = up(demo_root, operator)
        assert set(marks(result).values()) == {Mark.DONE}, result.outcomes
        workspace = Workspace(root=demo_root)
        assert is_demo(workspace)
        assert stat.S_IMODE(workspace.oauth_token_path.stat().st_mode) == SECRET_MODE
        assert open_numbers(demo_root) == SEEDED_ISSUES
        assert sorted(path.name for path in workspace.acceptance_dir.glob("test_*.py")) == [
            "test_new_chat.py",
            "test_readme.py",
        ]
        assert wrapper_path(workspace.state_dir).is_file()
        assert operator.questions == []

    def test_a_second_up_asks_nothing_and_keeps_the_board(self, demo_root: Path) -> None:
        up(demo_root, ScriptedOperator([CLAUDE_TOKEN], True))
        again = ScriptedOperator([], True)
        result = up(demo_root, again)
        assert again.prompts == []
        board = next(outcome for outcome in result.outcomes if outcome.name == messages.BOARD)
        assert board.detail == messages.BOARD_KEPT.format(count=len(SEEDED_ISSUES))

    def test_reset_brings_back_the_same_board_and_commits(self, demo_root: Path) -> None:
        up(demo_root, ScriptedOperator([CLAUDE_TOKEN], True))
        policy = policy_at(demo_root)
        before = main_commit(demo_root)
        workbench = policy.workspace.workbench_path(REPO_KEY)
        workbench.mkdir(parents=True)
        runs = policy.state_dir / "runs" / "20261001-210000-demo"
        runs.mkdir(parents=True)
        ledger_path(policy.state_dir).write_text('{"run_id": "20261001-210000-demo"}\n')
        prepared = PreparedSession(
            run_id="20261001-210000-demo", repo_key=REPO_KEY, prepared_at=NOW, question_count=2
        )
        write_prepared(policy.state_dir, prepared)
        briefing_directory(policy.state_dir, REPO_KEY).mkdir(parents=True)
        lines: list[str] = []
        operator = ScriptedOperator([], True)
        result = demo_reset(demo_root, EXAMPLES, REPO_KEY, operator, NOW, lines.append)
        assert set(marks(result).values()) == {Mark.DONE}
        assert open_numbers(demo_root) == SEEDED_ISSUES
        assert main_commit(demo_root) == before
        for leftover in (
            workbench,
            runs,
            ledger_path(policy.state_dir),
            prepared_path(policy.state_dir, REPO_KEY),
            briefing_directory(policy.state_dir, REPO_KEY),
        ):
            assert not leftover.exists(), leftover

    def test_reset_waits_for_a_run_to_end(self, demo_root: Path) -> None:
        up(demo_root, ScriptedOperator([CLAUDE_TOKEN], True))
        operator = ScriptedOperator([], True)
        with run_lock(Workspace(root=demo_root).state_dir), pytest.raises(DemoRefusalError):
            demo_reset(demo_root, EXAMPLES, REPO_KEY, operator, NOW, print)

    def test_remove_asks_and_deletes_the_whole_workspace(
        self, demo_root: Path, operator_home: Path
    ) -> None:
        up(demo_root, ScriptedOperator([CLAUDE_TOKEN], True))
        declined = demo_remove(demo_root, ScriptedOperator([], False), operator_home)
        assert declined == messages.REMOVE_DECLINED and demo_root.is_dir()
        removed = demo_remove(demo_root, ScriptedOperator([], True), operator_home)
        assert removed == messages.REMOVED.format(root=demo_root)
        assert not demo_root.exists()

    def test_env_points_gh_and_weekend_loop_at_the_example(self, demo_root: Path) -> None:
        up(demo_root, ScriptedOperator([CLAUDE_TOKEN], True))
        exports = demo_environment(demo_root, REPO_KEY)
        state = Workspace(root=demo_root).state_dir
        assert exports == [
            f"export WEEKEND_LOOP_HOME={demo_root}",
            "export GH_REPO=example-org/example-board",
            f'export PATH={wrapper_path(state).parent}:"$PATH"',
        ]

    def test_the_example_stays_off_the_schedule(self, demo_root: Path) -> None:
        up(demo_root, ScriptedOperator([CLAUDE_TOKEN], True))
        assert main(["--home", str(demo_root), "schedule", "on"]) == EXIT_REFUSED


def own_workspace(tmp_path: Path) -> Path:
    raw = base_policy(tmp_path)
    raw["repos"]["demo"]["backend"] = Backend.LOCAL.value
    return write_policy(tmp_path, raw)


def test_up_leaves_a_workspace_of_your_own_alone(
    tmp_path: Path, fake_binaries: Path, operator_home: Path
) -> None:
    root = own_workspace(tmp_path)
    before = Workspace(root=root).config_path.read_text()
    result = up(root, ScriptedOperator([], True))
    assert [outcome.mark for outcome in result.outcomes] == [Mark.FAILED]
    assert Workspace(root=root).config_path.read_text() == before


def test_reset_and_remove_refuse_a_workspace_of_your_own(
    tmp_path: Path, fake_binaries: Path, operator_home: Path
) -> None:
    root = own_workspace(tmp_path)
    with pytest.raises(DemoRefusalError):
        demo_reset(root, EXAMPLES, REPO_KEY, ScriptedOperator([], True), NOW, print)
    with pytest.raises(DemoRefusalError):
        demo_remove(root, ScriptedOperator([], True), operator_home)
    assert Workspace(root=root).config_path.is_file()


def test_remove_refuses_the_default_home_even_when_marked(
    tmp_path: Path, operator_home: Path
) -> None:
    root = operator_home / DEFAULT_HOME_NAME
    workspace = Workspace(root=root)
    workspace.config_path.parent.mkdir(parents=True)
    workspace.config_path.write_text(yaml.safe_dump({}))
    write_marker(workspace, EXAMPLES, NOW)
    with pytest.raises(DemoRefusalError):
        demo_remove(root, ScriptedOperator([], True), operator_home)
    assert root.is_dir()


def test_init_demo_marks_a_new_workspace_and_refuses_your_own(
    tmp_path: Path, operator_home: Path
) -> None:
    fresh = tmp_path / "fresh"
    example = ["init", "--demo", "--example", str(EXAMPLES)]
    assert main(["--home", str(fresh), *example]) == EXIT_OK
    assert is_demo(Workspace(root=fresh))
    own = own_workspace(tmp_path / "own")
    assert main(["--home", str(own), *example]) == EXIT_REFUSED
    assert not is_demo(Workspace(root=own))
