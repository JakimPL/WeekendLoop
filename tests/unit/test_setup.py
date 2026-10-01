from __future__ import annotations

import json
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
import yaml

from tests.support.fakes import FAKE_NPM, FAKE_SANDBOX_TOOL, install_fake
from tests.unit.conftest import OPERATOR_HOME, base_policy, write_github_data
from weekend_loop.cli import EXIT_BLOCKED, EXIT_OK, EXIT_REFUSED, main
from weekend_loop.github import GH_BINARY
from weekend_loop.local_github.wrapper import install_wrapper
from weekend_loop.models import Workspace
from weekend_loop.policy import policy_at
from weekend_loop.setup import messages
from weekend_loop.setup.outcomes import Mark, StepOutcome
from weekend_loop.setup.steps import SetupResult, run_setup
from weekend_loop.setup.system import sandbox_steps
from weekend_loop.setup.timers import locate_binaries, scheduled_timers
from weekend_loop.workspace_init import initialise_workspace

NOW: Final[datetime] = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
CLAUDE_TOKEN: Final[str] = "sk-ant-oat01-example"
GITHUB_TOKEN: Final[str] = "github_pat_example"
BLOCKED: Final[int] = 3
SECRET_MODE: Final[int] = 0o600


class ScriptedOperator:
    def __init__(self, secrets: list[str], agrees: bool) -> None:
        self.can_ask = True
        self.secrets = secrets
        self.agrees = agrees
        self.prompts: list[str] = []
        self.questions: list[str] = []

    def secret(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.secrets.pop(0)

    def agree(self, question: str) -> bool:
        self.questions.append(question)
        return self.agrees


@pytest.fixture
def operator_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def described(tmp_path: Path, fake_binaries: Path, operator_home: Path) -> Path:
    root = tmp_path / "workspace"
    initialise_workspace(root, OPERATOR_HOME, force=True, example=None)
    Workspace(root=root).config_path.write_text(yaml.safe_dump(base_policy(tmp_path)))
    install_fake(fake_binaries, "npm", FAKE_NPM)
    install_fake(fake_binaries, "uv", FAKE_SANDBOX_TOOL)
    write_github_data(fake_binaries, issues=[], pull_requests=[], comments={}, push=True)
    return root


def set_up(root: Path, operator: ScriptedOperator) -> SetupResult:
    lines: list[str] = []
    return run_setup(root, "demo", True, operator, BLOCKED, NOW, lines.append)


def outcome_of(result: SetupResult, name: str) -> StepOutcome:
    return next(outcome for outcome in result.outcomes if outcome.name == name)


def calls(fake_binaries: Path, tool: str) -> list[list[str]]:
    log = fake_binaries / f"{tool}-calls.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.is_file() else []


def test_a_first_setup_creates_the_workspace_and_asks_for_the_repository(
    tmp_path: Path, fake_binaries: Path, operator_home: Path
) -> None:
    operator = ScriptedOperator([], True)
    lines: list[str] = []
    result = run_setup(tmp_path / "workspace", None, True, operator, BLOCKED, NOW, lines.append)
    assert [outcome.mark for outcome in result.outcomes] == [Mark.DONE, Mark.TODO]
    assert result.outcomes[1].detail.startswith("describe your repository in")
    assert (tmp_path / "workspace" / "config.yaml").is_file()
    assert operator.questions == [] and operator.prompts == []


def test_setup_saves_the_tokens_creates_the_labels_and_turns_the_runs_on(
    described: Path, fake_binaries: Path, operator_home: Path
) -> None:
    operator = ScriptedOperator([CLAUDE_TOKEN, GITHUB_TOKEN], True)
    result = set_up(described, operator)
    for name in ("Claude token", "GitHub token", "labels", "socket filter", "schedule"):
        assert outcome_of(result, name).mark is Mark.DONE, outcome_of(result, name)
    policy = policy_at(described)
    for path, value in (
        (policy.workspace.oauth_token_path, CLAUDE_TOKEN),
        (policy.workspace.repository_token_path("demo"), GITHUB_TOKEN),
    ):
        assert path.read_text().strip() == value
        assert stat.S_IMODE(path.stat().st_mode) == SECRET_MODE
    created = [call for call in calls(fake_binaries, "gh") if call[:2] == ["label", "create"]]
    assert len(created) == 6
    units = operator_home / ".config" / "systemd" / "user"
    assert (units / "weekend-loop.slice").is_file()
    enabled = json.loads((fake_binaries / "systemctl-enabled.json").read_text())
    assert sorted(enabled) == sorted(scheduled_timers(policy))
    assert any(call[0] == "enable-linger" for call in calls(fake_binaries, "loginctl"))
    assert result.next_run is not None
    assert outcome_of(result, "schedule").detail == f"on; next run {result.next_run}"
    assert "schedules runs for" in operator.questions[0]


def test_a_second_setup_keeps_what_the_first_one_saved_and_asks_for_nothing(
    described: Path, fake_binaries: Path, operator_home: Path
) -> None:
    set_up(described, ScriptedOperator([CLAUDE_TOKEN, GITHUB_TOKEN], True))
    again = ScriptedOperator([], True)
    result = set_up(described, again)
    assert outcome_of(result, "Claude token").detail == messages.TOKEN_KEPT
    assert outcome_of(result, "GitHub token").detail == "can push to example-org/example-board"
    assert outcome_of(result, "socket filter").detail == messages.SOCKET_FILTER_READY
    assert again.prompts == []


def test_a_declined_setup_changes_nothing(
    described: Path, fake_binaries: Path, operator_home: Path
) -> None:
    result = set_up(described, ScriptedOperator([], False))
    assert result.declined
    assert not policy_at(described).workspace.oauth_token_path.exists()
    assert not (operator_home / ".config" / "systemd").exists()
    assert calls(fake_binaries, "gh") == []


def test_a_pasted_dialogue_is_refused_and_not_kept(
    described: Path, fake_binaries: Path, operator_home: Path
) -> None:
    dialogue = f"Your OAuth token: {CLAUDE_TOKEN} Store this token somewhere safe."
    result = set_up(described, ScriptedOperator([dialogue, GITHUB_TOKEN], True))
    assert outcome_of(result, "Claude token").mark is Mark.TODO
    assert "holds more than the token" in outcome_of(result, "Claude token").detail
    assert not policy_at(described).workspace.oauth_token_path.exists()


def test_a_session_that_may_not_linger_runs_only_while_you_are_logged_in(
    described: Path, fake_binaries: Path, operator_home: Path
) -> None:
    (fake_binaries / "loginctl-refuses").write_text("")
    result = set_up(described, ScriptedOperator([CLAUDE_TOKEN, GITHUB_TOKEN], True))
    schedule = outcome_of(result, "schedule")
    assert schedule.mark is Mark.TODO
    assert schedule.detail.startswith("on, but runs start only while you are logged in")


def test_a_machine_without_a_systemd_user_session_is_pointed_to_cron(
    described: Path, fake_binaries: Path, operator_home: Path
) -> None:
    (fake_binaries / "systemctl-absent").write_text("")
    result = set_up(described, ScriptedOperator([CLAUDE_TOKEN, GITHUB_TOKEN], True))
    assert outcome_of(result, "schedule").detail == messages.SCHEDULE_NO_SYSTEMD


def test_a_run_dropped_from_the_schedule_loses_its_timer(
    described: Path, fake_binaries: Path, operator_home: Path
) -> None:
    units = operator_home / ".config" / "systemd" / "user"
    units.mkdir(parents=True)
    (units / "weekend-loop-weekend-sun-0900.timer").write_text("[Timer]\n")
    set_up(described, ScriptedOperator([CLAUDE_TOKEN, GITHUB_TOKEN], True))
    assert not (units / "weekend-loop-weekend-sun-0900.timer").exists()
    disabled = [call for call in calls(fake_binaries, "systemctl") if call[1:2] == ["disable"]]
    assert any("weekend-loop-weekend-sun-0900.timer" in call for call in disabled)


def test_a_local_boards_gh_never_reaches_the_timers(
    tmp_path: Path, fake_binaries: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrapper = install_wrapper(tmp_path / "demo-state")
    monkeypatch.setenv("PATH", f"{wrapper.parent}:{fake_binaries}")
    assert locate_binaries((GH_BINARY,)) == {GH_BINARY: fake_binaries / GH_BINARY}


def test_a_sandbox_without_its_packages_names_the_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    steps = sandbox_steps(Workspace(root=tmp_path))
    assert [outcome.mark for outcome in steps.outcomes] == [Mark.TODO]
    assert steps.root_commands == ["sudo <your package manager> install bubblewrap socat"]


def test_a_sandbox_the_kernel_refuses_gets_the_apparmor_profile(
    tmp_path: Path, fake_binaries: Path
) -> None:
    install_fake(
        fake_binaries, "bwrap", "#!/bin/sh\necho 'bwrap: setting up uid map' >&2\nexit 1\n"
    )
    workspace = Workspace(root=tmp_path)
    steps = sandbox_steps(workspace)
    assert "userns," in workspace.apparmor_profile_path.read_text()
    assert steps.root_commands == [
        f"sudo install -m 644 {workspace.apparmor_profile_path} /etc/apparmor.d/bwrap",
        "sudo apparmor_parser -r /etc/apparmor.d/bwrap",
    ]


def test_setup_asks_before_it_changes_anything_unless_told_to_go_ahead(
    described: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--home", str(described), "setup"]) == EXIT_REFUSED
    assert messages.NEEDS_TERMINAL in capsys.readouterr().err


def test_an_unattended_setup_reports_each_step_and_what_is_left(
    described: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--home", str(described), "setup", "--repo-key", "demo", "--yes"])
    printed = capsys.readouterr().out
    assert code == EXIT_BLOCKED
    assert "Setting up Weekend Loop for example-org/example-board" in printed
    assert "  todo  Claude token: missing; save it to" in printed
    assert "  ok    preflight: clear once the items above are done" in printed
    assert "3 things are left" in printed
    assert printed.rstrip().endswith("run setup again once they are done.")


def test_the_schedule_turns_on_reports_the_next_runs_and_turns_off(
    described: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = ["--home", str(described)]
    assert main([*home, "schedule"]) == EXIT_OK
    assert capsys.readouterr().out.startswith(messages.RUNS_OFF)
    assert main([*home, "schedule", "on"]) == EXIT_OK
    shown = capsys.readouterr().out.splitlines()
    assert shown[0] == messages.RUNS_ON
    assert any(line.strip().startswith("weekend  every Fri 21:00") for line in shown)
    assert main([*home, "schedule", "off"]) == EXIT_OK
    assert capsys.readouterr().out.strip() == messages.RUNS_TURNED_OFF
    assert main([*home, "schedule"]) == EXIT_OK
    assert capsys.readouterr().out.startswith(messages.RUNS_OFF)
