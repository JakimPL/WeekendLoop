from __future__ import annotations

import argparse
import os
import shlex
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from weekend_loop.agreement import (
    load_blind_labels,
    render_agreement,
    score_agreement,
    write_label_template,
)
from weekend_loop.backends import board_operator_login, reader_for, writer_for
from weekend_loop.config_errors import ConfigError
from weekend_loop.config_view import default_keys, reference_document, resolved_document
from weekend_loop.demo.board import build_demo, planned_demo, render_demo
from weekend_loop.demo.publish import publish_demo
from weekend_loop.demo.seed import DEFAULT_EXAMPLES
from weekend_loop.execute import execute_run
from weekend_loop.fences import NO_FORBIDDEN_PATHS, render_fences
from weekend_loop.github import reader_for as github_reader_for
from weekend_loop.intake import ingest_answers, render_intake
from weekend_loop.labels import (
    LABEL_NAME_POSITION,
    create_labels,
    label_commands,
    sync_local_labels,
)
from weekend_loop.lock import RunLockHeldError, run_lock
from weekend_loop.models import (
    Backend,
    Policy,
    RepoMode,
    RepoTarget,
    RunKind,
    RunPhase,
    RunState,
    ScheduledCommand,
    Workspace,
)
from weekend_loop.notify import render_exit_alert, send_alert
from weekend_loop.policy import load_policy, repo_target
from weekend_loop.preflight import (
    PREFLIGHT_FILENAME_TEMPLATE,
    probe_reading,
    render_preflight,
    run_preflight,
)
from weekend_loop.prepare import run_prepare
from weekend_loop.publish import publish_run
from weekend_loop.records import write_record
from weekend_loop.report import render_digest
from weekend_loop.runs import (
    RunDirectory,
    create_run_directory,
    latest_run_id,
    load_run_state,
    new_run_id,
    open_run_directory,
    open_runs,
    save_run_state,
)
from weekend_loop.schedule import (
    inside_window,
    preparation_deadline,
    render_crontab,
    weekend_deadline,
)
from weekend_loop.session import run_weekend
from weekend_loop.setup import messages as setup_messages
from weekend_loop.setup.outcomes import (
    Mark,
    closing_line,
    open_items,
    render_outcome,
    render_root_block,
)
from weekend_loop.setup.steps import run_setup, schedule_status, schedule_step
from weekend_loop.setup.timers import (
    cli_binary,
    installed_timers,
    missing_unit_binaries,
    rendered_units,
    stop_timers,
    user_manager_running,
    user_unit_directory,
)
from weekend_loop.setup.tokens import TerminalOperator
from weekend_loop.status import (
    STATUS_EVENT_COUNT,
    STATUS_TRANSCRIPT_LINES,
    current_status,
    render_status,
)
from weekend_loop.supervision import PULSE_SECONDS, RunSupervisor
from weekend_loop.systemd_units import (
    ALERT_EXIT_COMMAND,
    EXIT_CODE_VARIABLE,
    EXIT_STATUS_VARIABLE,
    SERVICE_RESULT_VARIABLE,
    SUCCESS_SERVICE_RESULT,
    write_units,
)
from weekend_loop.triage import eligible_issues, finish_triage, prefilter_issues, triage
from weekend_loop.watch import WATCH_POLL_SECONDS, follow_run
from weekend_loop.web.application import DEFAULT_HOST, DEFAULT_PORT, serve
from weekend_loop.workspace import WorkspaceError, home_directory, open_workspace
from weekend_loop.workspace_init import initialise_workspace

DEMO_UP: Final[str] = "up"
DEMO_RESET: Final[str] = "reset"
DEMO_PUBLISH: Final[str] = "publish"
MISSING_EXAMPLES_TEMPLATE: Final[str] = (
    "no example project at {examples}; run this from a Weekend Loop checkout "
    "or name one with --example"
)
READY_TEMPLATE: Final[str] = (
    "workspace ready at {root}\ndescribe your repository in {config}, then run `weekend-loop setup`"
)
UNITS_WRITTEN_TEMPLATE: Final[str] = (
    "`weekend-loop schedule on` installs the units in {directory} and turns the runs on"
)
SCHEDULE_ON: Final[str] = "on"
SCHEDULE_OFF: Final[str] = "off"
SCHEDULE_STATUS: Final[str] = "status"
DEMO_READY_TEMPLATE: Final[str] = (
    "workspace ready at {root}, configured for the example\n"
    "save a Claude token to {secret}, then:\n"
    "  weekend-loop demo up --repo-key demo --home {root}"
)
DEFAULT_ISSUE_LIMIT: Final[int] = 200
DRYRUN_DIRECTORY_NAME: Final[str] = "dryrun"
LABELS_FILENAME_TEMPLATE: Final[str] = "{repo_key}-labels.csv"
LABEL_REFUSED_TEMPLATE: Final[str] = "GitHub refused the label {label}: {reason}"
AGREEMENT_FILENAME: Final[str] = "agreement.json"
EXIT_OK: Final[int] = 0
EXIT_BLOCKED: Final[int] = 3
EXIT_REFUSED: Final[int] = 2
UNREPORTED_VALUE: Final[str] = "unreported"
OUTSIDE_WINDOW_MESSAGE: Final[str] = (
    "outside the weekend window in policy.yaml; pass --ignore-window to start anyway"
)


def workspace_of(options: argparse.Namespace) -> Workspace:
    return open_workspace(home_directory(options.home, os.environ, Path.home()))


def prepared_policy(options: argparse.Namespace) -> Policy:
    workspace = workspace_of(options)
    render_fences(workspace, Path.home(), NO_FORBIDDEN_PATHS)
    return load_policy(workspace)


def command_config(options: argparse.Namespace) -> int:
    if options.reference:
        print(reference_document(), end="")
        return EXIT_OK
    workspace = workspace_of(options)
    if options.defaults:
        for key in default_keys(load_policy(workspace)):
            print(key)
        return EXIT_OK
    if options.resolved:
        print(resolved_document(load_policy(workspace)), end="")
        return EXIT_OK
    print(workspace.config_path.read_text(), end="")
    return EXIT_OK


def command_demo(options: argparse.Namespace) -> int:
    policy = prepared_policy(options)
    examples = options.example.resolve()
    if not examples.is_dir():
        print(MISSING_EXAMPLES_TEMPLATE.format(examples=examples), file=sys.stderr)
        return EXIT_REFUSED
    if options.demo_command == DEMO_PUBLISH:
        return publish_demo(examples, policy, options.repo_key, sys.stdin.isatty())
    reset = options.demo_command == DEMO_RESET
    if options.dry_run:
        outcome = planned_demo(examples, policy, options.repo_key, reset)
        print(render_demo(outcome, planned=True))
        return EXIT_OK
    print(render_demo(build_demo(examples, policy, options.repo_key, reset), planned=False))
    return EXIT_OK


def command_labels(options: argparse.Namespace) -> int:
    policy = prepared_policy(options)
    repo = repo_target(policy, options.repo_key)
    if repo.backend is Backend.LOCAL:
        for name in sync_local_labels(policy, repo):
            print(f"wrote {name}")
        return EXIT_OK
    commands = label_commands(policy, repo)
    reader = github_reader_for(repo, policy.state_dir)
    if options.dry_run:
        for arguments in commands:
            print(shlex.join([reader.binary, *arguments]))
        return EXIT_OK
    refusal = create_labels(reader, commands)
    if refusal is not None:
        print(
            LABEL_REFUSED_TEMPLATE.format(label=refusal.label, reason=refusal.reason),
            file=sys.stderr,
        )
        return EXIT_BLOCKED
    for arguments in commands:
        print(f"wrote {arguments[LABEL_NAME_POSITION]}")
    return EXIT_OK


def command_init(options: argparse.Namespace) -> int:
    root = home_directory(options.home, os.environ, Path.home())
    example = options.example.resolve() if options.demo else None
    if example is not None and not example.is_dir():
        print(MISSING_EXAMPLES_TEMPLATE.format(examples=example), file=sys.stderr)
        return EXIT_REFUSED
    for path in initialise_workspace(root, Path.home(), options.force, example):
        print(f"wrote {path}")
    workspace = Workspace(root=root)
    if example is not None:
        print(DEMO_READY_TEMPLATE.format(root=root, secret=workspace.oauth_token_path))
    else:
        print(READY_TEMPLATE.format(root=root, config=workspace.config_path))
    return EXIT_OK


def with_lock(policy: Policy, action: Callable[[], int]) -> int:
    try:
        with run_lock(policy.state_dir):
            return action()
    except RunLockHeldError as error:
        print(error)
        return EXIT_BLOCKED


def labels_path(state_directory: Path, repo_key: str) -> Path:
    return (
        state_directory / DRYRUN_DIRECTORY_NAME / LABELS_FILENAME_TEMPLATE.format(repo_key=repo_key)
    )


def command_preflight(options: argparse.Namespace) -> int:
    policy = prepared_policy(options)
    report = run_preflight(policy, options.repo_key, datetime.now(UTC), probe_reading(policy))
    write_record(
        report,
        policy.state_dir / PREFLIGHT_FILENAME_TEMPLATE.format(repo_key=options.repo_key),
    )
    print(render_preflight(report))
    return EXIT_OK if report.clear_to_run else EXIT_BLOCKED


def command_crontab(options: argparse.Namespace) -> int:
    policy = prepared_policy(options)
    print(
        render_crontab(policy.schedule, policy.scheduled_repo_key, policy.workspace, cli_binary()),
        end="",
    )
    return EXIT_OK


def command_systemd(options: argparse.Namespace) -> int:
    policy = load_policy(workspace_of(options))
    missing = missing_unit_binaries()
    if missing:
        print(setup_messages.SCHEDULE_MISSING_BINARIES.format(names=", ".join(missing)))
        return EXIT_BLOCKED
    for path in write_units(options.output_dir, rendered_units(policy, EXIT_BLOCKED)):
        print(f"wrote {path}")
    print(UNITS_WRITTEN_TEMPLATE.format(directory=user_unit_directory()))
    return EXIT_OK


def command_setup(options: argparse.Namespace) -> int:
    operator = TerminalOperator(can_ask=sys.stdin.isatty(), assume_yes=options.yes)
    if not operator.can_ask and not options.yes:
        print(setup_messages.NEEDS_TERMINAL, file=sys.stderr)
        return EXIT_REFUSED
    root = home_directory(options.home, os.environ, Path.home())
    result = run_setup(
        root,
        options.repo_key,
        not options.no_schedule,
        operator,
        EXIT_BLOCKED,
        datetime.now(UTC),
        print,
    )
    if result.declined:
        print(setup_messages.DECLINED)
        return EXIT_OK
    for line in render_root_block(result.root_commands):
        print(line)
    print()
    print(closing_line(result.outcomes, result.next_run))
    return EXIT_OK if open_items(result.outcomes) == 0 else EXIT_BLOCKED


def command_schedule(options: argparse.Namespace) -> int:
    policy = load_policy(workspace_of(options))
    if not user_manager_running():
        print(setup_messages.NO_SYSTEMD)
        return EXIT_BLOCKED
    now = datetime.now(UTC)
    if options.action == SCHEDULE_ON:
        outcome, _ = schedule_step(policy, now, EXIT_BLOCKED)
        if outcome.mark is not Mark.DONE:
            print(render_outcome(outcome))
        if outcome.mark is Mark.FAILED:
            return EXIT_BLOCKED
    if options.action == SCHEDULE_OFF:
        failure = stop_timers(installed_timers(user_unit_directory()))
        if failure is not None:
            print(
                setup_messages.SCHEDULE_FAILED.format(
                    command=failure.command, reason=failure.reason
                )
            )
            return EXIT_BLOCKED
        print(setup_messages.RUNS_TURNED_OFF)
        return EXIT_OK
    for line in schedule_status(policy, now):
        print(line)
    return EXIT_OK


def exit_deserves_alert(service_result: str, exit_status: str) -> bool:
    return service_result != SUCCESS_SERVICE_RESULT and exit_status != str(EXIT_BLOCKED)


def command_alert_exit(options: argparse.Namespace) -> int:
    service_result = os.environ.get(SERVICE_RESULT_VARIABLE, UNREPORTED_VALUE)
    exit_code = os.environ.get(EXIT_CODE_VARIABLE, UNREPORTED_VALUE)
    exit_status = os.environ.get(EXIT_STATUS_VARIABLE, UNREPORTED_VALUE)
    if not exit_deserves_alert(service_result, exit_status):
        return EXIT_OK
    text = render_exit_alert(options.unit, service_result, exit_code, exit_status)
    print(text)
    send_alert(load_policy(workspace_of(options)).workspace.alert_webhook_path, text)
    return EXIT_OK


def command_web(options: argparse.Namespace) -> int:
    policy = load_policy(workspace_of(options))
    print(f"weekend-loop web on http://{options.host}:{options.port}")
    serve(policy, options.host, options.port)
    return EXIT_OK


def command_candidates(options: argparse.Namespace) -> int:
    policy = prepared_policy(options)
    repo = repo_target(policy, options.repo_key)
    reader = reader_for(repo, policy.state_dir)
    operator = board_operator_login(policy.identity, reader)
    decided = prefilter_issues(reader, policy, operator, datetime.now(UTC), options.limit)
    survivors = eligible_issues(decided)
    sheet = (
        options.output
        if options.output is not None
        else labels_path(policy.state_dir, options.repo_key)
    )
    write_label_template(sheet, survivors)
    for issue in survivors:
        print(f"#{issue.number:>5}  {issue.title}")
    print(f"{len(survivors)} of {len(decided)} open issues survived the pre-filter")
    print(f"blind-label them in {sheet}")
    return EXIT_OK


def command_triage(options: argparse.Namespace) -> int:
    policy = prepared_policy(options)
    repo = repo_target(policy, options.repo_key)
    now = datetime.now(UTC)
    report = run_preflight(policy, options.repo_key, now, probe_reading(policy))
    print(render_preflight(report))
    if not report.clear_to_run:
        return EXIT_BLOCKED
    return with_lock(policy, lambda: perform_triage(policy, repo, options, now))


def perform_triage(
    policy: Policy, repo: RepoTarget, options: argparse.Namespace, now: datetime
) -> int:
    run_id = new_run_id(policy.state_dir, options.repo_key, now)
    run_directory = create_run_directory(policy.state_dir, run_id)
    state = triage(
        policy,
        repo,
        options.repo_key,
        reader_for(repo, policy.state_dir),
        run_directory,
        run_id,
        now,
        options.limit,
        RunSupervisor(run_directory, PULSE_SECONDS),
        RunKind.TRIAGE,
        preparation_deadline(policy.schedule, now),
    )
    saved = finish_triage(run_directory, state, repo, RunPhase.FINISHED)
    print(f"run {saved.run_id}: {len(saved.tasks)} tasks, ${saved.spent_usd:.2f} spent")
    print(f"plan written to {run_directory.plan_path}")
    return EXIT_OK


def command_prepare(options: argparse.Namespace) -> int:
    policy = prepared_policy(options)
    repo = repo_target(policy, options.repo_key)
    now = datetime.now(UTC)
    report = run_preflight(policy, options.repo_key, now, probe_reading(policy))
    print(render_preflight(report))
    if not report.clear_to_run:
        return EXIT_BLOCKED
    return with_lock(policy, lambda: perform_prepare(policy, repo, options, now))


def perform_prepare(
    policy: Policy, repo: RepoTarget, options: argparse.Namespace, now: datetime
) -> int:
    state, run_directory = run_prepare(policy, repo, options.repo_key, options.limit, now)
    questions = sum(
        len(task.assessment.questions) for task in state.tasks if task.assessment is not None
    )
    print(f"run {state.run_id}: {len(state.tasks)} tasks, {questions} question(s) open")
    print(f"plan written to {run_directory.plan_path}")
    return EXIT_OK


def command_execute(options: argparse.Namespace) -> int:
    policy = prepared_policy(options)
    repo = repo_target(policy, options.repo_key)
    report = run_preflight(policy, options.repo_key, datetime.now(UTC), probe_reading(policy))
    print(render_preflight(report))
    if not report.clear_to_run:
        return EXIT_BLOCKED
    if repo.mode is RepoMode.DRY_RUN:
        print(f"{options.repo_key} is a dry-run repository; execution stays off")
        return EXIT_BLOCKED
    return with_lock(policy, lambda: perform_execute(policy, repo, options))


def perform_execute(policy: Policy, repo: RepoTarget, options: argparse.Namespace) -> int:
    run_id = options.run_id if options.run_id is not None else latest_run_id(policy.state_dir)
    run_directory = open_run_directory(policy.state_dir, run_id)
    state = load_run_state(run_directory)
    if state.repo_key != options.repo_key:
        print(f"run {run_id} belongs to {state.repo_key}, not {options.repo_key}")
        return EXIT_BLOCKED
    saved = save_run_state(
        run_directory,
        execute_run(
            policy,
            repo,
            options.repo_key,
            reader_for(repo, policy.state_dir),
            run_directory,
            state,
            options.limit,
            RunSupervisor(run_directory, PULSE_SECONDS),
            execution_deadline(policy, state),
        ),
    )
    for task in saved.tasks:
        if task.branch is not None:
            print(f"#{task.issue_number} {task.status.value} on {task.branch}")
    print(f"run {saved.run_id}: ${saved.spent_usd:.2f} spent of ${saved.envelope_usd:.2f}")
    return EXIT_OK


def command_publish(options: argparse.Namespace) -> int:
    policy = prepared_policy(options)
    repo = repo_target(policy, options.repo_key)
    report = run_preflight(policy, options.repo_key, datetime.now(UTC), None)
    print(render_preflight(report))
    if not report.clear_to_run:
        return EXIT_BLOCKED
    if repo.mode is RepoMode.DRY_RUN:
        print(f"{options.repo_key} is a dry-run repository; publishing stays off")
        return EXIT_BLOCKED
    return with_lock(policy, lambda: perform_publish(policy, repo, options))


def perform_publish(policy: Policy, repo: RepoTarget, options: argparse.Namespace) -> int:
    run_id = options.run_id if options.run_id is not None else latest_run_id(policy.state_dir)
    run_directory = open_run_directory(policy.state_dir, run_id)
    state = load_run_state(run_directory)
    if state.repo_key != options.repo_key:
        print(f"run {run_id} belongs to {state.repo_key}, not {options.repo_key}")
        return EXIT_BLOCKED
    saved = save_run_state(
        run_directory,
        publish_run(
            policy,
            repo,
            options.repo_key,
            reader_for(repo, policy.state_dir),
            writer_for(repo, policy.state_dir, policy.identity, policy.labels.namespace),
            run_directory,
            state,
            options.limit,
        ),
    )
    for task in saved.tasks:
        if task.pull_request_url is not None:
            print(f"#{task.issue_number} {task.pull_request_url}")
    print(f"digest written to {run_directory.digest_path}")
    return EXIT_OK


def command_digest(options: argparse.Namespace) -> int:
    policy = prepared_policy(options)
    run_id = options.run_id if options.run_id is not None else latest_run_id(policy.state_dir)
    run_directory = open_run_directory(policy.state_dir, run_id)
    state = load_run_state(run_directory)
    body = render_digest(state, repo_target(policy, state.repo_key).slug)
    run_directory.digest_path.write_text(body)
    print(body)
    return EXIT_OK


def execution_deadline(policy: Policy, state: RunState) -> datetime:
    if state.deadline_at is not None:
        return state.deadline_at
    return weekend_deadline(policy.schedule, policy.budget.weekly_reset_at, datetime.now(UTC))


def command_weekend(options: argparse.Namespace) -> int:
    policy = prepared_policy(options)
    repo = repo_target(policy, options.repo_key)
    now = datetime.now(UTC)
    starts_fresh = not open_runs(policy.state_dir, options.repo_key, repo.slug)
    if starts_fresh and not options.ignore_window and not inside_window(policy.schedule, now):
        print(OUTSIDE_WINDOW_MESSAGE)
        return EXIT_BLOCKED
    report = run_preflight(policy, options.repo_key, now, probe_reading(policy))
    print(render_preflight(report))
    if not report.clear_to_run:
        return EXIT_BLOCKED
    deadline = weekend_deadline(policy.schedule, policy.budget.weekly_reset_at, now)
    return with_lock(policy, lambda: perform_weekend(policy, repo, options, deadline))


def perform_weekend(
    policy: Policy, repo: RepoTarget, options: argparse.Namespace, deadline: datetime
) -> int:
    state, run_directory = run_weekend(
        policy, repo, options.repo_key, options.limit, not options.no_publish, deadline
    )
    for task in state.tasks:
        if task.branch is not None:
            landing = task.pull_request_url if task.pull_request_url is not None else task.branch
            print(f"#{task.issue_number} {task.status.value} {landing}")
    print(f"run {state.run_id}: ${state.spent_usd:.2f} spent of ${state.envelope_usd:.2f}")
    print(f"digest at {run_directory.digest_path}")
    for note in state.notes:
        print(f"note: {note}")
    return EXIT_OK


def command_intake(options: argparse.Namespace) -> int:
    policy = prepared_policy(options)
    repo = repo_target(policy, options.repo_key)
    return with_lock(policy, lambda: perform_intake(policy, repo, options))


def perform_intake(policy: Policy, repo: RepoTarget, options: argparse.Namespace) -> int:
    intake = ingest_answers(
        policy.state_dir,
        options.repo_key,
        reader_for(repo, policy.state_dir),
        policy.identity,
        policy.labels.needs_input,
        options.limit,
    )
    print(render_intake(intake))
    return EXIT_OK


def command_agreement(options: argparse.Namespace) -> int:
    policy = prepared_policy(options)
    run_id = options.run_id if options.run_id is not None else latest_run_id(policy.state_dir)
    run_directory = open_run_directory(policy.state_dir, run_id)
    state = load_run_state(run_directory)
    sheet = (
        options.labels
        if options.labels is not None
        else labels_path(policy.state_dir, state.repo_key)
    )
    report = score_agreement(state, load_blind_labels(sheet))
    write_record(report, run_directory.outbox / AGREEMENT_FILENAME)
    print(render_agreement(report))
    return EXIT_OK


def watched_run(policy: Policy, run_id: str | None) -> RunDirectory:
    chosen = run_id if run_id is not None else latest_run_id(policy.state_dir)
    return open_run_directory(policy.state_dir, chosen)


def command_status(options: argparse.Namespace) -> int:
    policy = load_policy(workspace_of(options))
    run_directory = watched_run(policy, options.run_id)
    print(render_status(current_status(run_directory, options.lines, STATUS_EVENT_COUNT)))
    return EXIT_OK


def print_now(line: str) -> None:
    print(line, flush=True)


def command_watch(options: argparse.Namespace) -> int:
    policy = load_policy(workspace_of(options))
    run_directory = watched_run(policy, options.run_id)
    try:
        follow_run(run_directory, WATCH_POLL_SECONDS, print_now)
    except KeyboardInterrupt:
        return EXIT_OK
    return EXIT_OK


COMMANDS: Final[dict[str, Callable[[argparse.Namespace], int]]] = {
    "init": command_init,
    "demo": command_demo,
    "labels": command_labels,
    "config": command_config,
    "preflight": command_preflight,
    "candidates": command_candidates,
    "triage": command_triage,
    "prepare": command_prepare,
    "intake": command_intake,
    "execute": command_execute,
    "publish": command_publish,
    "digest": command_digest,
    "weekend": command_weekend,
    "agreement": command_agreement,
    "crontab": command_crontab,
    "systemd": command_systemd,
    ALERT_EXIT_COMMAND: command_alert_exit,
    "web": command_web,
    "setup": command_setup,
    "schedule": command_schedule,
    "status": command_status,
    "watch": command_watch,
}


def add_repository_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-key", required=True, help="repository key defined in policy.yaml")
    parser.add_argument("--limit", type=int, default=DEFAULT_ISSUE_LIMIT)


class SubParser(argparse.ArgumentParser):
    def __init__(self, **keywords: Any) -> None:  # noqa: ANN401
        keywords["parents"] = [*keywords.get("parents", []), workspace_parser()]
        super().__init__(**keywords)


def workspace_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--home", type=Path, default=argparse.SUPPRESS, help="the operator workspace"
    )
    return parser


def parse_arguments(arguments: list[str]) -> argparse.Namespace:
    shared = workspace_parser()
    parser = argparse.ArgumentParser(
        prog="weekend-loop", description="The Weekend Loop orchestrator", parents=[shared]
    )
    parser.set_defaults(home=None)
    subparsers = parser.add_subparsers(dest="command", required=True, parser_class=SubParser)

    initialise = subparsers.add_parser("init", help="create the operator workspace")
    initialise.add_argument(
        "--demo", action="store_true", help="write the example configuration instead of a starter"
    )
    initialise.add_argument("--example", type=Path, default=DEFAULT_EXAMPLES)
    initialise.add_argument(
        "--force", action="store_true", help="rewrite the files Weekend Loop generates"
    )

    demo = subparsers.add_parser("demo", help="build, reset or publish the example board")
    demo.add_argument("demo_command", choices=[DEMO_UP, DEMO_RESET, DEMO_PUBLISH])
    demo.add_argument("--repo-key", required=True)
    demo.add_argument("--example", type=Path, default=DEFAULT_EXAMPLES)
    demo.add_argument("--dry-run", action="store_true", help="print the plan and write nothing")

    labels = subparsers.add_parser("labels", help="create the weekend labels on a repository")
    labels.add_argument("--repo-key", required=True)
    labels.add_argument("--dry-run", action="store_true", help="print the commands and run none")

    configuration = subparsers.add_parser("config", help="show the configuration a run obeys")
    configuration.add_argument(
        "--resolved", action="store_true", help="print it with every default filled in"
    )
    configuration.add_argument(
        "--defaults", action="store_true", help="list the keys this workspace leaves to the default"
    )
    configuration.add_argument(
        "--reference", action="store_true", help="print every key with its default"
    )

    preflight = subparsers.add_parser("preflight", help="check the environment and the fences")
    preflight.add_argument("--repo-key", required=True)

    candidates = subparsers.add_parser("candidates", help="list pre-filter survivors for labelling")
    add_repository_arguments(candidates)
    candidates.add_argument("--output", type=Path, default=None)

    triage_parser = subparsers.add_parser("triage", help="assess the survivors and write the plan")
    add_repository_arguments(triage_parser)

    prepare = subparsers.add_parser(
        "prepare", help="assess ahead of the weekend and ask the operator"
    )
    add_repository_arguments(prepare)

    intake = subparsers.add_parser(
        "intake", help="read the operator's replies on the issues into the briefing"
    )
    add_repository_arguments(intake)

    execute = subparsers.add_parser("execute", help="work the approved tasks on local branches")
    add_repository_arguments(execute)
    execute.add_argument("--run-id", default=None)

    publish = subparsers.add_parser("publish", help="push branches, open draft pull requests")
    add_repository_arguments(publish)
    publish.add_argument("--run-id", default=None)

    weekend = subparsers.add_parser(
        "weekend", help="triage, work and publish in one unattended run"
    )
    add_repository_arguments(weekend)
    weekend.add_argument("--no-publish", action="store_true")
    weekend.add_argument(
        "--ignore-window", action="store_true", help="start outside the weekend window"
    )

    digest = subparsers.add_parser("digest", help="render the run digest without publishing it")
    digest.add_argument("--run-id", default=None)

    agreement = subparsers.add_parser("agreement", help="score a run against the blind labels")
    agreement.add_argument("--run-id", default=None)
    agreement.add_argument("--labels", type=Path, default=None)

    subparsers.add_parser("crontab", help="render the crontab for this checkout")

    setup = subparsers.add_parser(
        "setup", help="set everything up for a repository, asking once before it changes anything"
    )
    setup.add_argument("--repo-key", default=None, help="defaults to schedule.repo_key")
    setup.add_argument("--yes", action="store_true", help="go ahead without asking")
    setup.add_argument(
        "--no-schedule", action="store_true", help="leave the scheduled runs as they are"
    )

    schedule = subparsers.add_parser(
        "schedule", help="show when runs start, or turn the scheduled runs on or off"
    )
    schedule.add_argument(
        "action",
        nargs="?",
        default=SCHEDULE_STATUS,
        choices=[SCHEDULE_STATUS, SCHEDULE_ON, SCHEDULE_OFF],
    )

    systemd = subparsers.add_parser(
        "systemd", help="write the systemd units to a directory, for inspection"
    )
    systemd.add_argument("--output-dir", type=Path, required=True)

    alert_exit = subparsers.add_parser(
        ALERT_EXIT_COMMAND, help="alert the operator when a systemd run ends abnormally"
    )
    alert_exit.add_argument(
        "--unit", type=ScheduledCommand, choices=list(ScheduledCommand), required=True
    )

    web = subparsers.add_parser("web", help="serve the operator's web application")
    web.add_argument("--host", default=DEFAULT_HOST)
    web.add_argument("--port", type=int, default=DEFAULT_PORT)

    status = subparsers.add_parser("status", help="show what a run is doing right now")
    status.add_argument("--run-id", default=None)
    status.add_argument("--lines", type=int, default=STATUS_TRANSCRIPT_LINES)

    watch = subparsers.add_parser("watch", help="follow a run live until it finishes")
    watch.add_argument("--run-id", default=None)

    return parser.parse_args(arguments)


def main(arguments: list[str]) -> int:
    options = parse_arguments(arguments)
    try:
        return COMMANDS[options.command](options)
    except (ConfigError, WorkspaceError) as error:
        print(error, file=sys.stderr)
        return EXIT_REFUSED


def entry_point() -> int:
    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(entry_point())
