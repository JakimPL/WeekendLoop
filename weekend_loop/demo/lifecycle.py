from __future__ import annotations

import shlex
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from weekend_loop.demo import messages
from weekend_loop.demo.board import build_demo, prepare_local_board, removable_paths, remove
from weekend_loop.demo.marker import is_demo, write_marker
from weekend_loop.demo.publish import OperatorActionRequiredError
from weekend_loop.fences import NO_FORBIDDEN_PATHS, render_fences
from weekend_loop.labels import last_line
from weekend_loop.local_github.commands import REPOSITORY_VARIABLE
from weekend_loop.local_github.paths import wrapper_path
from weekend_loop.local_github.store import open_board
from weekend_loop.lock import RunLockHeldError, run_lock
from weekend_loop.models import Policy, Workspace
from weekend_loop.policy import load_policy, repo_target
from weekend_loop.preflight import GH_CHECK
from weekend_loop.setup import messages as setup_messages
from weekend_loop.setup.outcomes import Mark, StepOutcome, done, failed
from weekend_loop.setup.steps import (
    Coverage,
    Echo,
    SetupResult,
    SetupRun,
    checks_reported,
    preflight_step,
    step_coverage,
)
from weekend_loop.setup.system import sandbox_steps, socket_filter_steps
from weekend_loop.setup.tokens import Operator, claude_token_step
from weekend_loop.workspace import DEFAULT_HOME_NAME, HOME_VARIABLE, open_workspace
from weekend_loop.workspace_init import initialise_workspace


class DemoRefusalError(Exception):
    pass


def demo_workspace(root: Path) -> Workspace:
    workspace = Workspace(root=root)
    if not is_demo(workspace):
        raise DemoRefusalError(messages.NOT_A_DEMO.format(root=root))
    return workspace


def workspace_step(root: Path, examples: Path, now: datetime) -> StepOutcome:
    workspace = Workspace(root=root)
    if workspace.config_path.is_file():
        if is_demo(workspace):
            return done(messages.WORKSPACE, messages.WORKSPACE_FOUND)
        return failed(messages.WORKSPACE, messages.WORKSPACE_OF_YOUR_OWN.format(root=root))
    initialise_workspace(root, Path.home(), False, examples)
    write_marker(workspace, examples, now)
    return done(messages.WORKSPACE, messages.WORKSPACE_CREATED)


def board_step(policy: Policy, repo_key: str, examples: Path) -> StepOutcome:
    repo = repo_target(policy, repo_key)
    board = open_board(policy.state_dir, repo.slug)
    if board.exists():
        prepare_local_board(policy, repo)
        return done(messages.BOARD, messages.BOARD_KEPT.format(count=len(board.open_issues())))
    try:
        outcome = build_demo(examples, policy, repo_key)
    except OperatorActionRequiredError as error:
        return failed(messages.BOARD, str(error))
    except subprocess.CalledProcessError as error:
        return failed(messages.BOARD, last_line(error))
    tested = ", ".join(f"#{number}" for number in sorted(outcome.acceptance_tests, key=int))
    linked = ", ".join(f"#{number}" for number in outcome.linked_issue_numbers)
    detail = messages.BOARD_SEEDED.format(
        issues=len(outcome.issue_numbers), linked=linked, tested=tested
    )
    return done(messages.BOARD, detail)


def demo_coverage(policy: Policy, repo_key: str) -> Coverage:
    coverage = step_coverage(repo_key, repo_target(policy, repo_key))
    return {**coverage, messages.BOARD: {GH_CHECK, *coverage[setup_messages.GITHUB_TOKEN]}}


def demo_steps(
    policy: Policy,
    repo_key: str,
    examples: Path,
    operator: Operator,
    now: datetime,
    run: SetupRun,
) -> SetupResult:
    render_fences(policy.workspace, Path.home(), NO_FORBIDDEN_PATHS)
    run.record(claude_token_step(policy.workspace.oauth_token_path, operator))
    run.record(board_step(policy, repo_key, examples))
    for steps in (sandbox_steps(policy.workspace), socket_filter_steps(False)):
        for outcome in steps.outcomes:
            run.record(outcome)
        run.root_commands.extend(steps.root_commands)
    reported = checks_reported(demo_coverage(policy, repo_key), run.outcomes)
    run.record(preflight_step(policy, repo_key, now, reported))
    return run.result(None, False)


def demo_up(
    root: Path, examples: Path, repo_key: str, operator: Operator, now: datetime, echo: Echo
) -> SetupResult:
    echo(messages.HEADER.format(root=root))
    run = SetupRun(echo)
    workspace = run.record(workspace_step(root, examples, now))
    if workspace.mark is not Mark.DONE:
        return run.result(None, False)
    policy = load_policy(open_workspace(root))
    return demo_steps(policy, repo_key, examples, operator, now, run)


def demo_reset(
    root: Path, examples: Path, repo_key: str, operator: Operator, now: datetime, echo: Echo
) -> SetupResult:
    demo_workspace(root)
    policy = load_policy(open_workspace(root))
    repo = repo_target(policy, repo_key)
    try:
        with run_lock(policy.state_dir):
            echo(messages.HEADER.format(root=root))
            run = SetupRun(echo)
            removed = remove(removable_paths(policy, repo, repo_key))
            run.record(done(messages.RESET, messages.RESET_DONE.format(count=len(removed))))
            run.record(done(messages.WORKSPACE, messages.WORKSPACE_FOUND))
            return demo_steps(policy, repo_key, examples, operator, now, run)
    except RunLockHeldError as error:
        raise DemoRefusalError(messages.RUN_GOING.format(root=root)) from error


def demo_remove(root: Path, operator: Operator, user_home: Path) -> str:
    workspace = demo_workspace(root)
    if root == user_home / DEFAULT_HOME_NAME:
        raise DemoRefusalError(messages.YOUR_OWN_HOME.format(root=root))
    if not operator.agree(messages.REMOVE_QUESTION.format(root=root)):
        return messages.REMOVE_DECLINED
    try:
        with run_lock(workspace.state_dir):
            shutil.rmtree(root)
    except RunLockHeldError as error:
        raise DemoRefusalError(messages.RUN_GOING.format(root=root)) from error
    return messages.REMOVED.format(root=root)


def demo_environment(root: Path, repo_key: str) -> list[str]:
    demo_workspace(root)
    policy = load_policy(open_workspace(root))
    repo = repo_target(policy, repo_key)
    directory = wrapper_path(policy.state_dir).parent
    return [
        messages.EXPORT.format(name=HOME_VARIABLE, value=shlex.quote(str(root))),
        messages.EXPORT.format(name=REPOSITORY_VARIABLE, value=shlex.quote(repo.slug)),
        messages.PATH_EXPORT.format(directory=shlex.quote(str(directory))),
    ]
