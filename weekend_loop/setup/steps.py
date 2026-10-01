from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from weekend_loop.config_view import EXAMPLE_EMAIL, EXAMPLE_SLUG
from weekend_loop.fences import NO_FORBIDDEN_PATHS, render_fences
from weekend_loop.github import reader_for
from weekend_loop.labels import create_labels, label_commands
from weekend_loop.models import Backend, CheckOutcome, Policy, Record, RepoTarget, Workspace
from weekend_loop.policy import load_policy, repo_target
from weekend_loop.preflight import (
    CLAUDE_TOKEN_CHECK,
    GITHUB_TOKEN_CHECK_TEMPLATE,
    ISSUE_DEPENDENCIES_CHECK,
    PREFLIGHT_FILENAME_TEMPLATE,
    REPOSITORY_ACCESS_CHECK_TEMPLATE,
    SANDBOX_BINARIES,
    SANDBOX_BINARY_CHECK_TEMPLATE,
    SANDBOX_NAMESPACE_CHECK,
    probe_reading,
    run_preflight,
)
from weekend_loop.records import write_record
from weekend_loop.schedule import upcoming_runs
from weekend_loop.setup import messages
from weekend_loop.setup.outcomes import Mark, StepOutcome, done, failed, render_outcome, todo
from weekend_loop.setup.system import sandbox_steps, socket_filter_steps
from weekend_loop.setup.timers import (
    commands_running,
    install_units,
    lingering_failure,
    missing_unit_binaries,
    rendered_units,
    scheduled_timers,
    start_timers,
    timers_enabled,
    user_manager_running,
    user_unit_directory,
)
from weekend_loop.setup.tokens import Operator, claude_token_step, github_token_step
from weekend_loop.workspace import open_workspace
from weekend_loop.workspace_init import initialise_workspace

type Echo = Callable[[str], None]


class SetupResult(Record):
    outcomes: list[StepOutcome]
    root_commands: list[str]
    next_run: str | None
    declined: bool


def next_run_text(policy: Policy, now: datetime) -> str:
    run, moment = upcoming_runs(policy.schedule, now)[0]
    return f"{moment.strftime(messages.NEXT_FORMAT)} ({run.command.value})"


def run_moments(policy: Policy, now: datetime) -> str:
    moments = [
        f"{moment.strftime(messages.MOMENT_FORMAT)} ({run.command.value})"
        for run, moment in upcoming_runs(policy.schedule, now)
    ]
    return ", ".join(moments)


def described(policy: Policy, repo: RepoTarget) -> bool:
    return repo.slug != EXAMPLE_SLUG and policy.identity.git_author_email != EXAMPLE_EMAIL


def labels_step(policy: Policy, repo: RepoTarget) -> StepOutcome:
    commands = label_commands(policy, repo)
    refusal = create_labels(reader_for(repo, policy.state_dir), commands)
    if refusal is not None:
        detail = messages.LABELS_REFUSED.format(label=refusal.label, reason=refusal.reason)
        return failed(messages.LABELS, detail)
    return done(messages.LABELS, messages.LABELS_DONE.format(count=len(commands), slug=repo.slug))


def schedule_step(
    policy: Policy, now: datetime, blocked_exit_status: int
) -> tuple[StepOutcome, str | None]:
    if not user_manager_running():
        return todo(messages.SCHEDULE, messages.SCHEDULE_NO_SYSTEMD), None
    missing = missing_unit_binaries()
    if missing:
        detail = messages.SCHEDULE_MISSING_BINARIES.format(names=", ".join(missing))
        return failed(messages.SCHEDULE, detail), None
    units = rendered_units(policy, blocked_exit_status)
    failure = install_units(user_unit_directory(), units) or start_timers(scheduled_timers(policy))
    if failure is not None:
        detail = messages.SCHEDULE_FAILED.format(command=failure.command, reason=failure.reason)
        return failed(messages.SCHEDULE, detail), None
    next_run = next_run_text(policy, now)
    lingering = lingering_failure()
    if lingering is not None:
        detail = messages.SCHEDULE_ON_WHILE_LOGGED_IN.format(reason=lingering.reason)
        return todo(messages.SCHEDULE, detail), next_run
    return done(messages.SCHEDULE, messages.SCHEDULE_ON.format(next=next_run)), next_run


def checks_reported(repo_key: str, repo: RepoTarget, outcomes: list[StepOutcome]) -> set[str]:
    open_steps = {outcome.name for outcome in outcomes if outcome.mark is not Mark.DONE}
    covered = {
        messages.CLAUDE_TOKEN: {CLAUDE_TOKEN_CHECK},
        messages.GITHUB_TOKEN: {
            GITHUB_TOKEN_CHECK_TEMPLATE.format(repo_key=repo_key),
            REPOSITORY_ACCESS_CHECK_TEMPLATE.format(slug=repo.slug),
            ISSUE_DEPENDENCIES_CHECK,
        },
        messages.SANDBOX: {
            SANDBOX_NAMESPACE_CHECK,
            *(SANDBOX_BINARY_CHECK_TEMPLATE.format(binary=binary) for binary in SANDBOX_BINARIES),
        },
    }
    return {check for step, checks in covered.items() if step in open_steps for check in checks}


def preflight_step(policy: Policy, repo_key: str, now: datetime, reported: set[str]) -> StepOutcome:
    report = run_preflight(policy, repo_key, now, probe_reading(policy))
    write_record(report, policy.state_dir / PREFLIGHT_FILENAME_TEMPLATE.format(repo_key=repo_key))
    failing = [
        check.name
        for check in report.checks
        if check.outcome is CheckOutcome.FAILED and check.required
    ]
    remaining = [name for name in failing if name not in reported]
    if remaining:
        return failed(
            messages.PREFLIGHT, messages.PREFLIGHT_BLOCKED.format(checks=", ".join(remaining))
        )
    detail = messages.PREFLIGHT_AFTER_ITEMS if failing else messages.PREFLIGHT_CLEAR
    return done(messages.PREFLIGHT, detail)


def workspace_step(root: Path) -> StepOutcome:
    if Workspace(root=root).config_path.is_file():
        return done(messages.WORKSPACE, messages.WORKSPACE_FOUND.format(root=root))
    initialise_workspace(root, Path.home(), False, None)
    return done(messages.WORKSPACE, messages.WORKSPACE_CREATED.format(root=root))


def confirmation(policy: Policy, repo: RepoTarget, schedule: bool, now: datetime) -> str:
    planned = (
        messages.CONFIRM_SCHEDULE.format(moments=run_moments(policy, now))
        if schedule
        else messages.CONFIRM_NO_SCHEDULE
    )
    return messages.CONFIRM.format(slug=repo.slug, schedule=planned)


class SetupRun:
    def __init__(self, echo: Echo) -> None:
        self.echo = echo
        self.outcomes: list[StepOutcome] = []
        self.root_commands: list[str] = []

    def record(self, outcome: StepOutcome) -> StepOutcome:
        self.outcomes.append(outcome)
        self.echo(render_outcome(outcome))
        return outcome

    def result(self, next_run: str | None, declined: bool) -> SetupResult:
        return SetupResult(
            outcomes=self.outcomes,
            root_commands=self.root_commands,
            next_run=next_run,
            declined=declined,
        )


def run_setup(
    root: Path,
    repo_key: str | None,
    schedule: bool,
    operator: Operator,
    blocked_exit_status: int,
    now: datetime,
    echo: Echo,
) -> SetupResult:
    workspace = workspace_step(root)
    policy = load_policy(open_workspace(root))
    key = repo_key if repo_key is not None else policy.scheduled_repo_key
    repo = repo_target(policy, key)
    echo(messages.HEADER.format(slug=repo.slug))
    run = SetupRun(echo)
    run.record(workspace)
    if not described(policy, repo):
        config = policy.workspace.config_path
        run.record(todo(messages.WORKSPACE, messages.DESCRIBE_REPOSITORY.format(config=config)))
        return run.result(None, False)
    if not operator.agree(confirmation(policy, repo, schedule, now)):
        return run.result(None, True)
    return set_up(policy, key, repo, schedule, operator, blocked_exit_status, now, run)


def set_up(
    policy: Policy,
    repo_key: str,
    repo: RepoTarget,
    schedule: bool,
    operator: Operator,
    blocked_exit_status: int,
    now: datetime,
    run: SetupRun,
) -> SetupResult:
    run.record(claude_token_step(policy.workspace.oauth_token_path, operator))
    if repo.backend is Backend.GITHUB:
        token = run.record(github_token_step(repo, policy.state_dir, operator))
        if token.mark is Mark.DONE:
            run.record(labels_step(policy, repo))
        else:
            run.record(todo(messages.LABELS, messages.LABELS_WAIT))
    render_fences(policy.workspace, Path.home(), NO_FORBIDDEN_PATHS)
    for steps in (sandbox_steps(policy.workspace), socket_filter_steps()):
        for outcome in steps.outcomes:
            run.record(outcome)
        run.root_commands.extend(steps.root_commands)
    next_run: str | None = None
    if schedule:
        outcome, next_run = schedule_step(policy, now, blocked_exit_status)
        run.record(outcome)
    else:
        run.record(done(messages.SCHEDULE, messages.SCHEDULE_LEFT))
    reported = checks_reported(repo_key, repo, run.outcomes)
    run.record(preflight_step(policy, repo_key, now, reported))
    return run.result(next_run, False)


def schedule_status(policy: Policy, now: datetime) -> list[str]:
    if not timers_enabled(scheduled_timers(policy)):
        lines = [messages.RUNS_OFF]
    else:
        lines = [messages.RUNS_ON]
        lines.extend(
            messages.RUN_LINE.format(
                command=run.command.value,
                moment=moment.strftime(messages.MOMENT_FORMAT),
                next=moment.strftime(messages.NEXT_FORMAT),
            )
            for run, moment in upcoming_runs(policy.schedule, now)
        )
    lines.extend(messages.RUN_GOING.format(command=command.value) for command in commands_running())
    return lines
