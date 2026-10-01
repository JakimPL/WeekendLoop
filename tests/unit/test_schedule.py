from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from weekend_loop.models import (
    Policy,
    ScheduledCommand,
    ScheduledRun,
    SchedulePolicy,
    Weekday,
    WeekendWindow,
    WeeklyMoment,
    Workspace,
)
from weekend_loop.schedule import (
    cron_expression,
    next_occurrence,
    render_crontab,
    upcoming_runs,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIG = REPOSITORY_ROOT / "examples" / "config.yaml"
WORKSPACE = Workspace(root=Path("/operator-home/.weekend-loop"))
CLI = Path("/operator-home/.local/bin/weekend-loop")
WEEKEND = WeekendWindow(
    opens=WeeklyMoment(day_of_week=Weekday.FRIDAY, hour=18, minute=0),
    closes=WeeklyMoment(day_of_week=Weekday.SUNDAY, hour=23, minute=59),
)


def test_weekly_moment_renders_as_five_cron_fields() -> None:
    run = ScheduledRun(day_of_week=Weekday.FRIDAY, hour=21, minute=5)
    assert cron_expression(run) == "5 21 * * fri"


def test_rendered_crontab_carries_the_timezone_the_repository_and_the_command() -> None:
    schedule = SchedulePolicy(
        repo_key="demo",
        timezone="Europe/Warsaw",
        window=WEEKEND,
        runs=[ScheduledRun(day_of_week=Weekday.SATURDAY, hour=10, minute=0)],
    )
    rendered = render_crontab(schedule, "demo", WORKSPACE, CLI)
    assert "CRON_TZ=Europe/Warsaw" in rendered
    assert "TZ=Europe/Warsaw" in rendered
    assert "WEEKEND_LOOP_REPO=demo" in rendered
    assert rendered.endswith(
        f"0 10 * * sat flock --nonblock {WORKSPACE.state_dir}/cron.lock {CLI} "
        f"--home {WORKSPACE.root} weekend --repo-key demo "
        f">> {WORKSPACE.state_dir}/logs/demo-weekend.log 2>&1\n"
    )


def test_the_example_config_renders_a_crontab_pointing_at_the_workspace(
    workspace_policy: Policy,
) -> None:
    policy = workspace_policy
    rendered = render_crontab(policy.schedule, policy.scheduled_repo_key, policy.workspace, CLI)
    assert str(CLI) in rendered
    assert str(policy.workspace.root) in rendered
    assert len(policy.schedule.runs) == 3


def test_schedule_naming_an_unknown_repository_is_rejected(tmp_path: Path) -> None:
    raw = yaml.safe_load(EXAMPLE_CONFIG.read_text())
    raw["workspace"] = {"root": str(tmp_path)}
    raw["schedule"]["repo_key"] = "nope"
    with pytest.raises(ValidationError, match="schedule.repo_key"):
        Policy.model_validate(raw)


def test_hour_outside_the_day_is_rejected() -> None:
    with pytest.raises(ValidationError, match="hour"):
        ScheduledRun(day_of_week=Weekday.FRIDAY, hour=24, minute=0)


def test_each_entry_names_the_command_cron_should_run() -> None:
    schedule = SchedulePolicy(
        repo_key="demo",
        timezone="Europe/Warsaw",
        window=WEEKEND,
        runs=[
            ScheduledRun(
                day_of_week=Weekday.THURSDAY, hour=20, minute=0, command=ScheduledCommand.PREPARE
            ),
            ScheduledRun(day_of_week=Weekday.FRIDAY, hour=21, minute=0),
        ],
    )

    rendered = render_crontab(schedule, "demo", WORKSPACE, CLI)

    assert "0 20 * * thu flock" in rendered and "prepare --repo-key demo" in rendered
    assert "0 21 * * fri flock" in rendered and "weekend --repo-key demo" in rendered


def test_the_example_config_asks_before_it_works(workspace_policy: Policy) -> None:
    runs = workspace_policy.schedule.runs

    assert runs[0].command is ScheduledCommand.PREPARE
    assert all(run.command is ScheduledCommand.WEEKEND for run in runs[1:])
    assert runs[0].day_of_week is Weekday.THURSDAY


def test_a_run_comes_next_this_week_until_its_moment_has_passed() -> None:
    thursday = ScheduledRun(day_of_week=Weekday.THURSDAY, hour=20, minute=0)
    before = datetime(2026, 10, 1, 17, 0, tzinfo=UTC)
    after = datetime(2026, 10, 1, 19, 0, tzinfo=UTC)
    assert next_occurrence(thursday, "Europe/Warsaw", before).isoformat() == (
        "2026-10-01T20:00:00+02:00"
    )
    assert next_occurrence(thursday, "Europe/Warsaw", after).isoformat() == (
        "2026-10-08T20:00:00+02:00"
    )


def test_the_upcoming_runs_come_in_the_order_they_start() -> None:
    schedule = SchedulePolicy(
        repo_key="demo",
        timezone="UTC",
        runs=[
            ScheduledRun(day_of_week=Weekday.FRIDAY, hour=21, minute=0),
            ScheduledRun(
                day_of_week=Weekday.THURSDAY, hour=20, minute=0, command=ScheduledCommand.PREPARE
            ),
        ],
    )
    upcoming = upcoming_runs(schedule, datetime(2026, 10, 1, 12, 0, tzinfo=UTC))
    assert [run.command for run, _ in upcoming] == [
        ScheduledCommand.PREPARE,
        ScheduledCommand.WEEKEND,
    ]
