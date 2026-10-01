from pathlib import Path
from typing import Final

import pytest

from tests.support.fakes import FAKE_SANDBOX_TOOL, install_fake
from tests.unit.conftest import write_test_policy
from weekend_loop.cli import EXIT_BLOCKED, EXIT_OK, main
from weekend_loop.models import (
    ResourcesPolicy,
    ScheduledCommand,
    ScheduledRun,
    SchedulePolicy,
    Weekday,
    WeekendWindow,
    WeeklyMoment,
)
from weekend_loop.systemd_units import render_systemd_units, unit_path_variable

WORKSPACE: Final[Path] = Path("/operator-home/.weekend-loop")
CLI: Final[Path] = Path("/operator-home/.local/bin/weekend-loop")
PATH_VARIABLE: Final[str] = "/operator-home/.local/bin:/usr/local/bin:/usr/bin:/bin"
WEEKEND_SERVICE: Final[str] = "weekend-loop-weekend.service"
PREPARE_SERVICE: Final[str] = "weekend-loop-prepare.service"


def build_schedule() -> SchedulePolicy:
    return SchedulePolicy(
        repo_key="demo",
        timezone="Europe/Warsaw",
        window=WeekendWindow(
            opens=WeeklyMoment(day_of_week=Weekday.FRIDAY, hour=18, minute=0),
            closes=WeeklyMoment(day_of_week=Weekday.SUNDAY, hour=23, minute=59),
        ),
        runs=[
            ScheduledRun(
                day_of_week=Weekday.THURSDAY, hour=20, minute=0, command=ScheduledCommand.PREPARE
            ),
            ScheduledRun(day_of_week=Weekday.FRIDAY, hour=21, minute=0),
            ScheduledRun(day_of_week=Weekday.SATURDAY, hour=10, minute=5),
        ],
    )


def render(workspace_root: Path, cli_binary: Path) -> dict[str, str]:
    return render_systemd_units(
        build_schedule(),
        "demo",
        workspace_root,
        cli_binary,
        PATH_VARIABLE,
        EXIT_BLOCKED,
        ResourcesPolicy(memory_pool_gb=24.0),
    )


def directives(unit: str) -> list[str]:
    return [line for line in unit.splitlines() if line and not line.startswith("[")]


def test_the_weekend_service_restarts_after_a_crash_and_stays_down_when_blocked() -> None:
    service = directives(render(WORKSPACE, CLI)[WEEKEND_SERVICE])

    assert "Restart=on-failure" in service
    assert "RestartSec=90" in service
    assert f"RestartPreventExitStatus={EXIT_BLOCKED}" in service
    assert "StartLimitIntervalSec=6h" in service
    assert "StartLimitBurst=4" in service


def test_a_killed_worker_leaves_the_run_standing_and_a_stop_ends_the_whole_tree() -> None:
    service = directives(render(WORKSPACE, CLI)[WEEKEND_SERVICE])

    assert "OOMPolicy=continue" in service
    assert "KillMode=control-group" in service
    assert "TimeoutStopSec=60" in service
    assert "Type=exec" in service


def test_the_services_run_the_policy_and_repository_they_were_rendered_for() -> None:
    units = render(WORKSPACE, CLI)
    weekend = directives(units[WEEKEND_SERVICE])
    prepare = directives(units[PREPARE_SERVICE])

    invocation = f"{CLI} --home {WORKSPACE}"
    assert f"ExecStart={invocation} weekend --repo-key demo" in weekend
    assert f"ExecStopPost=-{invocation} alert-exit --unit weekend" in weekend
    assert f"ExecStart={invocation} prepare --repo-key demo" in prepare
    assert f"ExecStopPost=-{invocation} alert-exit --unit prepare" in prepare
    assert "Restart=no" in prepare
    assert f"WorkingDirectory={WORKSPACE}" in weekend
    assert f"Environment=PATH={PATH_VARIABLE}" in weekend
    assert f"Environment=WEEKEND_LOOP_HOME={WORKSPACE}" in weekend
    assert f"EnvironmentFile=-{WORKSPACE}/run.env" in weekend


def test_each_scheduled_run_gets_a_persistent_timer_in_the_schedule_timezone() -> None:
    units = render(WORKSPACE, CLI)

    saturday = directives(units["weekend-loop-weekend-sat-1005.timer"])
    assert "OnCalendar=Sat *-*-* 10:05:00 Europe/Warsaw" in saturday
    assert "Persistent=true" in saturday
    assert f"Unit={WEEKEND_SERVICE}" in saturday
    assert "WantedBy=timers.target" in saturday
    thursday = directives(units["weekend-loop-prepare-thu-2000.timer"])
    assert "OnCalendar=Thu *-*-* 20:00:00 Europe/Warsaw" in thursday
    assert f"Unit={PREPARE_SERVICE}" in thursday
    assert sorted(units) == [
        "weekend-loop-prepare-thu-2000.timer",
        PREPARE_SERVICE,
        "weekend-loop-weekend-fri-2100.timer",
        "weekend-loop-weekend-sat-1005.timer",
        WEEKEND_SERVICE,
        "weekend-loop.slice",
    ]


def test_spaces_and_percent_signs_in_paths_survive_the_unit_syntax() -> None:
    root = Path("/operator-home/my 100% agent")
    service = directives(render(root, root / "bin" / "weekend-loop")[WEEKEND_SERVICE])

    quoted_binary = '"/operator-home/my 100%% agent/bin/weekend-loop"'
    quoted_root = '"/operator-home/my 100%% agent"'
    expected = f"ExecStart={quoted_binary} --home {quoted_root} weekend"
    assert any(line.startswith(expected) for line in service)
    assert "WorkingDirectory=/operator-home/my 100%% agent" in service


def test_the_unit_path_puts_the_operator_binaries_before_the_system_directories() -> None:
    binaries = [
        Path("/operator-home/.local/bin/uv"),
        Path("/operator-home/.local/bin/claude"),
        Path("/usr/bin/git"),
    ]

    assert unit_path_variable(binaries) == PATH_VARIABLE


def test_the_systemd_command_writes_every_unit_and_prints_the_steps(
    tmp_path: Path, fake_binaries: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install_fake(fake_binaries, "uv", FAKE_SANDBOX_TOOL)
    install_fake(fake_binaries, "weekend-loop", FAKE_SANDBOX_TOOL)
    root = write_test_policy(tmp_path, None)
    output_directory = tmp_path / "units"

    code = main(["--home", str(root), "systemd", "--output-dir", str(output_directory)])

    assert code == EXIT_OK
    written = sorted(path.name for path in output_directory.iterdir())
    assert WEEKEND_SERVICE in written
    assert len(written) == 6
    service = (output_directory / WEEKEND_SERVICE).read_text()
    invocation = f"{fake_binaries / 'weekend-loop'} --home {root}"
    assert f"ExecStart={invocation} weekend --repo-key demo" in service
    assert f"Environment=PATH={fake_binaries}:/usr/local/bin:/usr/bin:/bin" in service
    printed = capsys.readouterr().out
    assert "loginctl enable-linger $USER" in printed
    assert "systemctl --user daemon-reload" in printed
    assert "weekend-loop-weekend-fri-2100.timer" in printed
    assert "systemctl --user start weekend-loop-weekend.service" in printed
    assert "journalctl --user -u weekend-loop-weekend -f" in printed


def test_the_systemd_command_needs_uv_on_the_path(
    tmp_path: Path, fake_binaries: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(fake_binaries))
    policy_path = write_test_policy(tmp_path, None)
    output_directory = tmp_path / "units"

    code = main(["--home", str(policy_path), "systemd", "--output-dir", str(output_directory)])

    assert code == EXIT_BLOCKED
    assert not output_directory.exists()


def test_every_service_runs_inside_one_memory_pool_and_stops_its_task_scopes() -> None:
    units = render(WORKSPACE, CLI)
    pool = directives(units["weekend-loop.slice"])
    assert f"MemoryMax={24 * 1024**3}" in pool
    assert "MemorySwapMax=0" in pool
    for name in (WEEKEND_SERVICE, PREPARE_SERVICE):
        service = directives(units[name])
        assert "Slice=weekend-loop.slice" in service
        assert "ExecStopPost=-systemctl --user stop weekend-loop-tasks.slice" in service


def test_a_pool_left_to_the_default_takes_half_of_the_machine() -> None:
    units = render_systemd_units(
        build_schedule(), "demo", WORKSPACE, CLI, PATH_VARIABLE, EXIT_BLOCKED, ResourcesPolicy()
    )
    assert "MemoryMax=50%" in directives(units["weekend-loop.slice"])
