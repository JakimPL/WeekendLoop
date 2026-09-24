import shlex
from pathlib import Path
from typing import Final

from weekend_loop.models import ScheduledCommand, ScheduledRun, SchedulePolicy
from weekend_loop.workspace import HOME_VARIABLE

UNIT_PREFIX: Final[str] = "weekend-loop"
SERVICE_SUFFIX: Final[str] = ".service"
TIMER_SUFFIX: Final[str] = ".timer"
CLI_NAME: Final[str] = "weekend-loop"
ALERT_EXIT_COMMAND: Final[str] = "alert-exit"
ENVIRONMENT_FILE: Final[str] = "run.env"
SYSTEM_PATH_DIRECTORIES: Final[tuple[str, ...]] = ("/usr/local/bin", "/usr/bin", "/bin")
USER_UNIT_DIRECTORY: Final[Path] = Path(".config") / "systemd" / "user"
RESTARTED_COMMANDS: Final[frozenset[ScheduledCommand]] = frozenset({ScheduledCommand.WEEKEND})
RESTART_DELAY_SECONDS: Final[int] = 90
STOP_TIMEOUT_SECONDS: Final[int] = 60
START_LIMIT_INTERVAL: Final[str] = "6h"
START_LIMIT_BURST: Final[int] = 4
SERVICE_RESULT_VARIABLE: Final[str] = "SERVICE_RESULT"
EXIT_CODE_VARIABLE: Final[str] = "EXIT_CODE"
EXIT_STATUS_VARIABLE: Final[str] = "EXIT_STATUS"
SUCCESS_SERVICE_RESULT: Final[str] = "success"
EXITED_CODE: Final[str] = "exited"
SIGNAL_CODES: Final[tuple[str, ...]] = ("killed", "dumped")


def unit_name(command: ScheduledCommand) -> str:
    return f"{UNIT_PREFIX}-{command.value}"


def service_name(command: ScheduledCommand) -> str:
    return f"{unit_name(command)}{SERVICE_SUFFIX}"


def timer_name(run: ScheduledRun) -> str:
    moment = f"{run.day_of_week.value}-{run.hour:02d}{run.minute:02d}"
    return f"{unit_name(run.command)}-{moment}{TIMER_SUFFIX}"


def calendar_expression(run: ScheduledRun, timezone: str) -> str:
    weekday = run.day_of_week.value.capitalize()
    return f"{weekday} *-*-* {run.hour:02d}:{run.minute:02d}:00 {timezone}"


def restarts_after_failure(command: ScheduledCommand) -> bool:
    return command in RESTARTED_COMMANDS


def specifiers_escaped(value: str) -> str:
    return value.replace("%", "%%")


def quoted_when_spaced(value: str) -> str:
    if not any(character.isspace() for character in value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def command_word(word: str) -> str:
    return quoted_when_spaced(specifiers_escaped(word).replace("$", "$$"))


def command_line(words: list[str]) -> str:
    return " ".join(command_word(word) for word in words)


def unit_path_variable(binaries: list[Path]) -> str:
    directories = [str(binary.parent) for binary in binaries]
    extra = [directory for directory in directories if directory not in SYSTEM_PATH_DIRECTORIES]
    return ":".join(dict.fromkeys([*extra, *SYSTEM_PATH_DIRECTORIES]))


def render_sections(sections: dict[str, list[str]]) -> str:
    blocks = ["\n".join([f"[{name}]", *lines]) for name, lines in sections.items()]
    return "\n\n".join(blocks) + "\n"


def environment_lines(workspace_root: Path, path_variable: str) -> list[str]:
    return [
        f"WorkingDirectory={specifiers_escaped(str(workspace_root))}",
        f"Environment={quoted_when_spaced(specifiers_escaped(f'PATH={path_variable}'))}",
        f"Environment={quoted_when_spaced(specifiers_escaped(f'{HOME_VARIABLE}={workspace_root}'))}",
        f"EnvironmentFile=-{specifiers_escaped(str(workspace_root / ENVIRONMENT_FILE))}",
    ]


def restart_lines(command: ScheduledCommand, blocked_exit_status: int) -> list[str]:
    if not restarts_after_failure(command):
        return ["Restart=no"]
    return [
        "Restart=on-failure",
        f"RestartSec={RESTART_DELAY_SECONDS}",
        f"RestartPreventExitStatus={blocked_exit_status}",
    ]


def render_service(
    command: ScheduledCommand,
    repo_key: str,
    invocation: list[str],
    environment: list[str],
    blocked_exit_status: int,
) -> str:
    start = [*invocation, command.value, "--repo-key", repo_key]
    alert = [*invocation, ALERT_EXIT_COMMAND, "--unit", command.value]
    return render_sections(
        {
            "Unit": [
                f"Description=Weekend Loop {command.value} run for {repo_key}",
                f"StartLimitIntervalSec={START_LIMIT_INTERVAL}",
                f"StartLimitBurst={START_LIMIT_BURST}",
            ],
            "Service": [
                "Type=exec",
                *environment,
                f"ExecStart={command_line(start)}",
                f"ExecStopPost=-{command_line(alert)}",
                *restart_lines(command, blocked_exit_status),
                "KillMode=control-group",
                "OOMPolicy=continue",
                f"TimeoutStopSec={STOP_TIMEOUT_SECONDS}",
            ],
        }
    )


def render_timer(run: ScheduledRun, timezone: str) -> str:
    calendar = calendar_expression(run, timezone)
    return render_sections(
        {
            "Unit": [f"Description=Weekend Loop {run.command.value} run on {calendar}"],
            "Timer": [
                f"OnCalendar={calendar}",
                "Persistent=true",
                f"Unit={service_name(run.command)}",
            ],
            "Install": ["WantedBy=timers.target"],
        }
    )


def render_systemd_units(
    schedule: SchedulePolicy,
    repo_key: str,
    workspace_root: Path,
    cli_binary: Path,
    path_variable: str,
    blocked_exit_status: int,
) -> dict[str, str]:
    invocation = [str(cli_binary), "--home", str(workspace_root)]
    environment = environment_lines(workspace_root, path_variable)
    services = {
        service_name(command): render_service(
            command, repo_key, invocation, environment, blocked_exit_status
        )
        for command in ScheduledCommand
    }
    timers = {timer_name(run): render_timer(run, schedule.timezone) for run in schedule.runs}
    return {**services, **timers}


def install_step(output_directory: Path, user_unit_directory: Path) -> list[str]:
    if output_directory.resolve() == user_unit_directory.resolve():
        return []
    source = shlex.quote(str(output_directory))
    destination = shlex.quote(str(user_unit_directory))
    return [
        f"mkdir -p {destination}",
        f"cp {source}/{UNIT_PREFIX}-*{SERVICE_SUFFIX} {source}/{UNIT_PREFIX}-*{TIMER_SUFFIX} "
        f"{destination}/",
    ]


def render_systemd_steps(
    timer_names: list[str], output_directory: Path, user_unit_directory: Path
) -> str:
    weekend = unit_name(ScheduledCommand.WEEKEND)
    weekend_service = service_name(ScheduledCommand.WEEKEND)
    sections = {
        "then enable the timers:": [
            *install_step(output_directory, user_unit_directory),
            "loginctl enable-linger $USER",
            "systemctl --user daemon-reload",
            f"systemctl --user enable --now {' '.join(timer_names)}",
        ],
        "start a run by hand:": [f"systemctl --user start {weekend_service}"],
        "follow its log:": [f"journalctl --user -u {weekend} -f"],
        "check on it and on the timers:": [
            f"systemctl --user status {weekend}",
            f"systemctl --user list-timers '{UNIT_PREFIX}-*'",
        ],
    }
    lines = [line for title, steps in sections.items() for line in [title, *indented(steps)]]
    return "\n".join(lines)


def indented(lines: list[str]) -> list[str]:
    return [f"  {line}" for line in lines]


def write_units(output_directory: Path, units: dict[str, str]) -> list[Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, content in units.items():
        path = output_directory / name
        path.write_text(content)
        written.append(path)
    return written
