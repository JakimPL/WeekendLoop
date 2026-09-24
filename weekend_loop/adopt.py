from __future__ import annotations

from datetime import datetime
from typing import Final

from weekend_loop.backends import BoardReader, repository_token
from weekend_loop.briefing import (
    RepoBriefing,
    clear_prepared,
    prepared_is_fresh,
    questions_answered,
    read_briefing,
    read_prepared,
)
from weekend_loop.mailbox import PAUSE_FILENAME, STOP_FILENAME, clear_request
from weekend_loop.models import (
    EventType,
    Issue,
    Policy,
    PreparedSession,
    RepoTarget,
    RunKind,
    RunPhase,
    RunState,
    SpecSignals,
    Task,
    TaskStatus,
)
from weekend_loop.runs import (
    RunDirectory,
    RunProgress,
    append_event,
    load_run_state,
    open_run_directory,
    save_run_state,
)
from weekend_loop.supervision import RunSupervisor
from weekend_loop.triage import assess_pending, assessor_context, collect_signals
from weekend_loop.workbench import prepare_checkout

ADOPTED_NOTE_TEMPLATE: Final[str] = "adopted the triage prepared as run {run_id}"
REFRESH_NOTE_TEMPLATE: Final[str] = "carried {carried} assessment(s), re-assessed {refreshed}"
ANSWERED_REASON: Final[str] = "the operator answered its questions"
EDITED_REASON: Final[str] = "the issue changed after the prepare pass"
REPLIED_REASON: Final[str] = "the operator replied on the issue"


def adopt_prepared(
    policy: Policy, repo_key: str, repo_slug: str, now: datetime, deadline_at: datetime
) -> tuple[RunDirectory, RunState, PreparedSession] | None:
    prepared = read_prepared(policy.state_dir, repo_key)
    if prepared is None or not prepared_is_fresh(prepared, now):
        return None
    run_directory = open_run_directory(policy.state_dir, prepared.run_id)
    state = load_run_state(run_directory)
    if state.repo_key != repo_key:
        return None
    append_event(
        run_directory,
        EventType.RUN_STARTED,
        ADOPTED_NOTE_TEMPLATE.format(run_id=state.run_id),
        None,
    )
    for request in (STOP_FILENAME, PAUSE_FILENAME):
        clear_request(run_directory.inbox, request)
    adopted = state.model_copy(
        update={
            "phase": RunPhase.TRIAGE,
            "kind": RunKind.WEEKEND,
            "deadline_at": deadline_at,
            "repo_slug": repo_slug,
            "started_at": now,
            "notes": [*state.notes, ADOPTED_NOTE_TEMPLATE.format(run_id=state.run_id)],
        }
    )
    saved = save_run_state(run_directory, adopted)
    clear_prepared(policy.state_dir, repo_key)
    return run_directory, saved, prepared


def stale_reason(
    task: Task,
    issue: Issue | None,
    assessed_at: datetime,
    briefing: RepoBriefing,
    replied: set[int],
) -> str | None:
    if task.assessment is None or task.status is TaskStatus.INELIGIBLE or issue is None:
        return None
    if questions_answered(briefing, task):
        return ANSWERED_REASON
    if task.issue_number in replied:
        return REPLIED_REASON
    if issue.updated_at > assessed_at:
        return EDITED_REASON
    return None


def stale_reasons(
    tasks: list[Task],
    issues: dict[int, Issue],
    assessed_at: datetime,
    briefing: RepoBriefing,
    replied: set[int],
) -> dict[int, str]:
    reasons: dict[int, str] = {}
    for task in tasks:
        reason = stale_reason(task, issues.get(task.issue_number), assessed_at, briefing, replied)
        if reason is not None:
            reasons[task.issue_number] = reason
    return reasons


def mark_stale(tasks: list[Task], signals: dict[int, SpecSignals]) -> list[Task]:
    return [
        task.model_copy(
            update={"status": TaskStatus.CANDIDATE, "spec_signals": signals[task.issue_number]}
        )
        if task.issue_number in signals
        else task
        for task in tasks
    ]


def refresh_assessments(
    policy: Policy,
    repo: RepoTarget,
    repo_key: str,
    issues: dict[int, Issue],
    run_directory: RunDirectory,
    state: RunState,
    assessed_at: datetime,
    replied: set[int],
    supervisor: RunSupervisor,
    deadline: datetime,
) -> RunState:
    briefing = read_briefing(policy.state_dir, repo_key)
    reasons = stale_reasons(state.tasks, issues, assessed_at, briefing, replied)
    if not reasons:
        return state
    for issue_number, reason in reasons.items():
        append_event(run_directory, EventType.ASSESSMENT_STARTED, reason, issue_number)
    workbench = prepare_checkout(repo, repo_key, policy.workspace, repository_token(repo))
    signals = collect_signals([issues[number] for number in reasons], workbench)
    progress = RunProgress(
        run_directory, state.model_copy(update={"tasks": mark_stale(state.tasks, signals)})
    )
    progress.save()
    assess_pending(
        policy,
        assessor_context(policy, repo, repo_key, workbench),
        issues,
        progress,
        supervisor,
        deadline,
    )
    refreshed = sum(
        1
        for task in progress.state.tasks
        if task.issue_number in reasons and task.status is TaskStatus.ASSESSED
    )
    progress.note(
        REFRESH_NOTE_TEMPLATE.format(
            carried=len(progress.state.tasks) - refreshed, refreshed=refreshed
        )
    )
    return progress.save()


def current_issues(reader: BoardReader, limit: int) -> dict[int, Issue]:
    return {issue.number: issue for issue in reader.open_issues(limit)}
