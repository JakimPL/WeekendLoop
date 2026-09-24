from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from weekend_loop.adopt import adopt_prepared, current_issues, refresh_assessments
from weekend_loop.backends import BoardReader, reader_for, repository_token, writer_for
from weekend_loop.execute import execute_run
from weekend_loop.intake import ingest_answers, record_intake, replied_issues
from weekend_loop.mailbox import stop_requested
from weekend_loop.models import (
    ActivityKind,
    EventType,
    Policy,
    RepoMode,
    RepoTarget,
    RunKind,
    RunPhase,
    RunState,
    StopReason,
)
from weekend_loop.notify import render_finish_alert, send_alert
from weekend_loop.publish import publish_run
from weekend_loop.report import render_digest
from weekend_loop.runs import (
    RunDirectory,
    RunProgress,
    append_event,
    create_run_directory,
    load_run_state,
    new_run_id,
    open_run_directory,
    open_runs,
    save_run_state,
)
from weekend_loop.supervision import PULSE_SECONDS, RunSupervisor
from weekend_loop.triage import assess_pending, assessor_context, finish_triage, triage
from weekend_loop.workbench import prepare_checkout

EXECUTION_PHASES: Final[tuple[RunPhase, ...]] = (RunPhase.EXECUTE, RunPhase.PARKED)


def work_may_follow(run_directory: RunDirectory, state: RunState) -> bool:
    return not stop_requested(run_directory.inbox) and state.stop_reason is None


def publishing_allowed(run_directory: RunDirectory, state: RunState) -> bool:
    return not stop_requested(run_directory.inbox) and state.stop_reason is not StopReason.OPERATOR


def run_weekend(
    policy: Policy,
    repo: RepoTarget,
    repo_key: str,
    limit: int,
    publish: bool,
    deadline: datetime,
) -> tuple[RunState, RunDirectory]:
    state, run_directory = perform_session(policy, repo, repo_key, limit, publish, deadline)
    send_alert(policy.workspace.alert_webhook_path, render_finish_alert(state))
    return state, run_directory


def perform_session(
    policy: Policy,
    repo: RepoTarget,
    repo_key: str,
    limit: int,
    publish: bool,
    deadline: datetime,
) -> tuple[RunState, RunDirectory]:
    now = datetime.now(UTC)
    reader = reader_for(repo, policy.state_dir)
    resumed = resume_open_run(policy, repo, repo_key, reader, limit)
    if resumed is None:
        run_directory, state, supervisor = open_session(
            policy, repo, repo_key, reader, limit, now, deadline
        )
    else:
        run_directory, state, supervisor = resumed
    return drive(
        policy, repo, repo_key, reader, run_directory, state, supervisor, limit, publish
    ), run_directory


def run_deadline_of(state: RunState) -> datetime:
    if state.deadline_at is None:
        raise ValueError(f"run {state.run_id} carries no deadline")
    return state.deadline_at


def drive(
    policy: Policy,
    repo: RepoTarget,
    repo_key: str,
    reader: BoardReader,
    run_directory: RunDirectory,
    state: RunState,
    supervisor: RunSupervisor,
    limit: int,
    publish: bool,
) -> RunState:
    if repo.mode is not RepoMode.EXECUTE:
        return finish_without_publishing(run_directory, state, repo)
    if state.phase in EXECUTION_PHASES:
        if not work_may_follow(run_directory, state):
            return finish_without_publishing(run_directory, state, repo)
        state = save_run_state(
            run_directory,
            execute_run(
                policy,
                repo,
                repo_key,
                reader,
                run_directory,
                state,
                limit,
                supervisor,
                run_deadline_of(state),
            ),
        )
    if not publish or not publishing_allowed(run_directory, state):
        return finish_without_publishing(run_directory, state, repo)
    supervisor.enter(ActivityKind.PUBLISHING, None, None, None)
    return save_run_state(
        run_directory,
        publish_run(
            policy,
            repo,
            repo_key,
            reader,
            writer_for(repo, policy.state_dir, policy.identity, policy.labels.namespace),
            run_directory,
            state,
            limit,
        ),
    )


def resume_open_run(
    policy: Policy, repo: RepoTarget, repo_key: str, reader: BoardReader, limit: int
) -> tuple[RunDirectory, RunState, RunSupervisor] | None:
    opened = open_runs(policy.state_dir, repo_key, repo.slug)
    if not opened:
        return None
    run_directory = open_run_directory(policy.state_dir, opened[-1])
    state = load_run_state(run_directory)
    supervisor = RunSupervisor(run_directory, PULSE_SECONDS)
    append_event(
        run_directory, EventType.RUN_RESUMED, f"resumed in phase {state.phase.value}", None
    )
    if state.phase is not RunPhase.TRIAGE:
        return run_directory, state, supervisor
    issues = current_issues(reader, limit)
    workbench = prepare_checkout(repo, repo_key, policy.workspace, repository_token(repo))
    assessed = assess_pending(
        policy,
        assessor_context(policy, repo, repo_key, workbench),
        issues,
        RunProgress(run_directory, state),
        supervisor,
        run_deadline_of(state),
    )
    return run_directory, finish_triage(run_directory, assessed, repo, RunPhase.EXECUTE), supervisor


def open_session(
    policy: Policy,
    repo: RepoTarget,
    repo_key: str,
    reader: BoardReader,
    limit: int,
    now: datetime,
    deadline: datetime,
) -> tuple[RunDirectory, RunState, RunSupervisor]:
    intake = ingest_answers(
        policy.state_dir, repo_key, reader, policy.identity, policy.labels.needs_input, limit
    )
    adopted = adopt_prepared(policy, repo_key, repo.slug, now, deadline)
    if adopted is None:
        run_id = new_run_id(policy.state_dir, repo_key, now)
        run_directory = create_run_directory(policy.state_dir, run_id)
        supervisor = RunSupervisor(run_directory, PULSE_SECONDS)
        record_intake(run_directory, intake)
        triaged = triage(
            policy,
            repo,
            repo_key,
            reader,
            run_directory,
            run_id,
            now,
            limit,
            supervisor,
            RunKind.WEEKEND,
            deadline,
        )
        finished = finish_triage(run_directory, triaged, repo, RunPhase.EXECUTE)
        return run_directory, finished, supervisor
    run_directory, state, prepared = adopted
    supervisor = RunSupervisor(run_directory, PULSE_SECONDS)
    record_intake(run_directory, intake)
    refreshed = refresh_assessments(
        policy,
        repo,
        repo_key,
        current_issues(reader, limit),
        run_directory,
        state,
        prepared.prepared_at,
        replied_issues(intake),
        supervisor,
        deadline,
    )
    return (
        run_directory,
        finish_triage(run_directory, refreshed, repo, RunPhase.EXECUTE),
        supervisor,
    )


def finish_without_publishing(
    run_directory: RunDirectory, state: RunState, repo: RepoTarget
) -> RunState:
    run_directory.digest_path.write_text(render_digest(state, repo.slug))
    return save_run_state(run_directory, state.model_copy(update={"phase": RunPhase.FINISHED}))
