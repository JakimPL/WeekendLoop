from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Final

from weekend_loop.allowance import UNTIMED_FIVE_HOUR_LIMIT, AllowanceVerdict, await_allowance
from weekend_loop.assessor import (
    AssessorContext,
    affordable,
    assess_issue,
    load_assessor_prompts,
)
from weekend_loop.backends import (
    BoardReader,
    board_operator_login,
    repository_token,
    with_foreign_activity,
)
from weekend_loop.briefing import read_briefing
from weekend_loop.claude_cli import agent_environment, read_oauth_token
from weekend_loop.models import (
    AssessmentOutcome,
    ClaudeOutcome,
    EligibilityDecision,
    EventType,
    Issue,
    Policy,
    RepoTarget,
    RunKind,
    RunPhase,
    RunState,
    SpecSignals,
    StopReason,
    Task,
    TaskStatus,
)
from weekend_loop.prefilter import evaluate_eligibility, spec_signals
from weekend_loop.records import write_record
from weekend_loop.report import render_triage_plan
from weekend_loop.runs import RunDirectory, RunProgress, append_event, save_run_state
from weekend_loop.supervision import STOP_DETAIL, Hold, RunSupervisor
from weekend_loop.workbench import prepare_checkout

ASSESSMENT_FILENAME: Final[str] = "assessment.json"
STOPPED_NOTE_TEMPLATE: Final[str] = "triage stopped early: {reason}"
DEADLINE_DETAIL: Final[str] = "the run's deadline passed while it was paused"
TRIAGE_STOPS: Final[dict[ClaudeOutcome, StopReason]] = {
    ClaudeOutcome.WEEKLY_LIMIT: StopReason.ALLOWANCE,
    ClaudeOutcome.CANCELLED: StopReason.OPERATOR,
}
BUDGET_NOTE: Final[str] = STOPPED_NOTE_TEMPLATE.format(reason=ClaudeOutcome.BUDGET.value)


def prefilter_issues(
    reader: BoardReader,
    policy: Policy,
    owner_login: str,
    now: datetime,
    limit: int,
) -> list[tuple[Issue, EligibilityDecision]]:
    decided: list[tuple[Issue, EligibilityDecision]] = []
    for issue in reader.open_issues(limit):
        decision = evaluate_eligibility(issue, policy.eligibility, policy.labels, owner_login, now)
        if not decision.eligible:
            decided.append((issue, decision))
            continue
        enriched = with_foreign_activity(reader, issue, owner_login)
        decided.append(
            (
                enriched,
                evaluate_eligibility(enriched, policy.eligibility, policy.labels, owner_login, now),
            )
        )
    return decided


def eligible_issues(decided: list[tuple[Issue, EligibilityDecision]]) -> list[Issue]:
    return [issue for issue, decision in decided if decision.eligible]


def collect_signals(issues: list[Issue], workbench: Path) -> dict[int, SpecSignals]:
    return {issue.number: spec_signals(issue, workbench) for issue in issues}


def rank_candidates(
    issues: list[Issue], signals: dict[int, SpecSignals]
) -> list[tuple[Issue, SpecSignals]]:
    ordered = sorted(
        issues,
        key=lambda issue: (
            not signals[issue.number].has_template,
            not signals[issue.number].has_acceptance_criteria,
            -len(signals[issue.number].resolved_paths),
            issue.number,
        ),
    )
    return [(issue, signals[issue.number]) for issue in ordered]


def build_task(
    issue: Issue,
    decision: EligibilityDecision,
    signals: SpecSignals | None,
    outcome: AssessmentOutcome | None,
) -> Task:
    if not decision.eligible:
        status = TaskStatus.INELIGIBLE
    elif outcome is None:
        status = TaskStatus.CANDIDATE
    else:
        status = TaskStatus.ASSESSED
    return Task(
        issue_number=issue.number,
        title=issue.title,
        status=status,
        eligibility=decision,
        spec_signals=signals,
        assessment=outcome.assessment if outcome is not None else None,
        delivery=None,
        gate=None,
        branch=None,
        pull_request_url=None,
        session_id=outcome.session_id if outcome is not None else None,
        attempts=1 if outcome is not None else 0,
        cost_usd=outcome.cost_usd if outcome is not None else 0.0,
    )


def record_assessment(run_directory: RunDirectory, outcome: AssessmentOutcome) -> None:
    directory = run_directory.task_directory(outcome.issue_number)
    directory.mkdir(parents=True, exist_ok=True)
    write_record(outcome, directory / ASSESSMENT_FILENAME)
    append_event(
        run_directory,
        EventType.ASSESSMENT_FINISHED,
        f"{outcome.assessment.verdict.value} "
        f"({outcome.assessment.effort.value}/{outcome.assessment.risk.value}) "
        f"for ${outcome.cost_usd:.2f} [{outcome.outcome.value}]",
        outcome.issue_number,
    )


def assessed_task(task: Task, outcome: AssessmentOutcome) -> Task:
    return task.model_copy(
        update={
            "status": TaskStatus.ASSESSED,
            "assessment": outcome.assessment,
            "session_id": outcome.session_id,
            "attempts": task.attempts + 1,
            "cost_usd": task.cost_usd + outcome.cost_usd,
        }
    )


def open_triage(
    policy: Policy,
    repo: RepoTarget,
    repo_key: str,
    reader: BoardReader,
    run_directory: RunDirectory,
    run_id: str,
    now: datetime,
    limit: int,
    kind: RunKind,
    deadline_at: datetime,
) -> tuple[RunState, dict[int, Issue], Path]:
    owner_login = board_operator_login(policy.identity, reader)
    append_event(run_directory, EventType.RUN_STARTED, f"{repo.slug} as {owner_login}", None)
    decided = prefilter_issues(reader, policy, owner_login, now, limit)
    survivors = eligible_issues(decided)
    append_event(
        run_directory,
        EventType.PREFILTER_FINISHED,
        f"{len(survivors)} of {len(decided)} issues survived the pre-filter",
        None,
    )
    workbench = prepare_checkout(repo, repo_key, policy.workspace, repository_token(repo))
    signals = collect_signals(survivors, workbench)
    state = RunState(
        run_id=run_id,
        repo_key=repo_key,
        mode=repo.mode,
        phase=RunPhase.TRIAGE,
        started_at=now,
        updated_at=now,
        heartbeat_at=now,
        envelope_usd=policy.budget.envelope_usd,
        spent_usd=0.0,
        usage=None,
        tasks=[
            build_task(issue, decision, signals.get(issue.number), None)
            for issue, decision in decided
        ],
        notes=[],
        kind=kind,
        deadline_at=deadline_at,
        repo_slug=repo.slug,
    )
    issues = {issue.number: issue for issue, _ in decided}
    return save_run_state(run_directory, state), issues, workbench


def assessor_context(
    policy: Policy, repo: RepoTarget, repo_key: str, workbench: Path
) -> AssessorContext:
    return AssessorContext(
        repo=repo,
        prompts=load_assessor_prompts(policy.workspace.prompts_dir),
        workbench=workbench,
        environment=agent_environment(
            policy.agent_home, read_oauth_token(policy.workspace.oauth_token_path), {}
        ),
        briefing=read_briefing(policy.state_dir, repo_key),
    )


def pending_candidates(
    state: RunState, issues: dict[int, Issue]
) -> list[tuple[Issue, SpecSignals]]:
    signals = {
        task.issue_number: task.spec_signals
        for task in state.tasks
        if task.status is TaskStatus.CANDIDATE
        and task.spec_signals is not None
        and task.issue_number in issues
    }
    return rank_candidates([issues[number] for number in signals], signals)


def assess_through_limits(
    policy: Policy,
    context: AssessorContext,
    issue: Issue,
    signals: SpecSignals,
    supervisor: RunSupervisor,
    progress: RunProgress,
    deadline: datetime,
    observed_costs: list[float],
) -> tuple[AssessmentOutcome, AllowanceVerdict | None]:
    spent = 0.0
    while True:
        outcome = assess_issue(policy, context, issue, signals, supervisor)
        spent += outcome.cost_usd
        if outcome.outcome is not ClaudeOutcome.WINDOW_LIMIT:
            return outcome.model_copy(update={"cost_usd": spent}), None
        verdict = await_allowance(
            policy,
            supervisor,
            context.workbench,
            context.environment,
            deadline,
            progress.state.usage,
            outcome.rejection if outcome.rejection is not None else UNTIMED_FIVE_HOUR_LIMIT,
            observed_costs,
        )
        progress.spend(verdict.spent_usd)
        progress.observe(verdict.reading)
        if not verdict.proceed:
            return outcome.model_copy(update={"cost_usd": spent}), verdict


def assessment_stop(
    outcome: AssessmentOutcome, verdict: AllowanceVerdict | None
) -> tuple[StopReason, str] | None:
    if verdict is not None:
        return verdict.stop_reason or StopReason.ALLOWANCE, verdict.detail
    stop_reason = TRIAGE_STOPS.get(outcome.outcome)
    if stop_reason is None:
        return None
    return stop_reason, STOPPED_NOTE_TEMPLATE.format(reason=outcome.outcome.value)


def hold_stop(hold: Hold) -> tuple[StopReason, str] | None:
    if hold is Hold.STOPPED:
        return StopReason.OPERATOR, STOP_DETAIL
    if hold is Hold.DEADLINE:
        return StopReason.WINDOW_CLOSED, DEADLINE_DETAIL
    return None


def assess_pending(
    policy: Policy,
    context: AssessorContext,
    issues: dict[int, Issue],
    progress: RunProgress,
    supervisor: RunSupervisor,
    deadline: datetime,
) -> RunState:
    observed_costs: list[float] = []
    for issue, signals in pending_candidates(progress.state, issues):
        halted = hold_stop(supervisor.hold_while_paused(deadline))
        if halted is None and supervisor.stop_requested():
            halted = StopReason.OPERATOR, STOP_DETAIL
        if halted is not None:
            progress.stop(*halted)
            progress.note(STOPPED_NOTE_TEMPLATE.format(reason=halted[1]))
            break
        if not affordable(policy, progress.state.spent_usd):
            progress.note(BUDGET_NOTE)
            append_event(progress.run_directory, EventType.BUDGET_EXHAUSTED, "assessor", None)
            break
        outcome, verdict = assess_through_limits(
            policy, context, issue, signals, supervisor, progress, deadline, observed_costs
        )
        progress.put_task(assessed_task(progress.task(issue.number), outcome))
        progress.spend(outcome.cost_usd)
        record_assessment(progress.run_directory, outcome)
        stopped = assessment_stop(outcome, verdict)
        if stopped is not None:
            progress.stop(*stopped)
            progress.note(STOPPED_NOTE_TEMPLATE.format(reason=stopped[1]))
        progress.save()
        if stopped is not None:
            break
    return progress.save()


def triage(
    policy: Policy,
    repo: RepoTarget,
    repo_key: str,
    reader: BoardReader,
    run_directory: RunDirectory,
    run_id: str,
    now: datetime,
    limit: int,
    supervisor: RunSupervisor,
    kind: RunKind,
    deadline_at: datetime,
) -> RunState:
    state, issues, workbench = open_triage(
        policy, repo, repo_key, reader, run_directory, run_id, now, limit, kind, deadline_at
    )
    return assess_pending(
        policy,
        assessor_context(policy, repo, repo_key, workbench),
        issues,
        RunProgress(run_directory, state),
        supervisor,
        deadline_at,
    )


def finish_triage(
    run_directory: RunDirectory, state: RunState, repo: RepoTarget, next_phase: RunPhase
) -> RunState:
    saved = save_run_state(run_directory, state.model_copy(update={"phase": next_phase}))
    run_directory.plan_path.write_text(render_triage_plan(saved, repo.slug))
    append_event(
        run_directory,
        EventType.RUN_FINISHED,
        f"{len(saved.tasks)} tasks, ${saved.spent_usd:.2f} spent",
        None,
    )
    return saved
