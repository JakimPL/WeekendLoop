from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from weekend_loop.acceptance import acceptance_map_path, load_acceptance_map, run_acceptance_test
from weekend_loop.allowance import await_allowance
from weekend_loop.attempt import BenchKind, TaskBench, drive_worker, task_prompt, work_on
from weekend_loop.backends import BoardReader, repository_token
from weekend_loop.briefing import guidance_for, read_briefing
from weekend_loop.claude_cli import agent_environment, read_oauth_token
from weekend_loop.commands import command_environment
from weekend_loop.fences import render_fences
from weekend_loop.gate import changed_python_files, evaluate_gate, parse_numstat, run_gate_commands
from weekend_loop.mailbox import read_inbox
from weekend_loop.models import (
    ActivityKind,
    ClaudeOutcome,
    CommandResult,
    Consent,
    DeliveryStatus,
    EventType,
    GateResult,
    Halt,
    Inbox,
    Issue,
    LabelPolicy,
    LedgerEntry,
    Policy,
    RepoTarget,
    RunPhase,
    RunState,
    StopReason,
    Task,
    TaskStatus,
    Verdict,
    WorkerOutcome,
)
from weekend_loop.publish import publishable
from weekend_loop.records import append_record, write_record
from weekend_loop.report import EFFORT_ORDER
from weekend_loop.runs import RunDirectory, RunProgress, append_event, ledger_path
from weekend_loop.supervision import STOP_DETAIL, Hold, RunSupervisor
from weekend_loop.waves import Wave, overlaps_within, sibling_paths, waves_of
from weekend_loop.workbench import (
    add_worktree,
    base_reference,
    branch_name,
    changed_files_numstat,
    commit_changes,
    commit_count,
    current_branch,
    diff_text,
    discard_changes,
    fetch_base,
    git_environment,
    prepare_checkout,
    prune_worktrees,
    registered_worktrees,
    remove_worktree,
    run_setup_commands,
    write_askpass_script,
)
from weekend_loop.worker import TaskGuidance, abandoned_delivery, load_worker_prompts

DELIVERY_FILENAME: Final[str] = "delivery.json"
GATE_FILENAME: Final[str] = "gate.json"
ACCEPTANCE_FILENAME: Final[str] = "acceptance.json"
DIFF_FILENAME: Final[str] = "diff.patch"
CONSENT_TO_WORK_ON: Final[tuple[Consent, ...]] = (Consent.AUTO, Consent.APPROVED)
WORKED_STATUSES: Final[tuple[TaskStatus, ...]] = (
    TaskStatus.REVIEW,
    TaskStatus.UNFINISHED,
    TaskStatus.NEEDS_INPUT,
    TaskStatus.ABANDONED,
)
OUTCOME_STOPS: Final[dict[ClaudeOutcome, StopReason]] = {
    ClaudeOutcome.WEEKLY_LIMIT: StopReason.ALLOWANCE,
    ClaudeOutcome.CANCELLED: StopReason.OPERATOR,
}
WORKER_ENVIRONMENT: Final[dict[str, str]] = {"UV_NO_SYNC": "1", "CUDA_VISIBLE_DEVICES": ""}
BUDGET_NOTE_TEMPLATE: Final[str] = "execution stopped early: {reason}"
DEADLINE_REASON: Final[str] = "the run's deadline has passed"
ENVELOPE_REASON: Final[str] = "the envelope cannot cover another task"
MAX_TASK_RESUMES: Final[int] = 2
CLOSED_ISSUE_REASON: Final[str] = "the issue is no longer open"
REVIEWER_SKIP_REASON: Final[str] = "the reviewer skipped it"
CONSENT_REASON_TEMPLATE: Final[str] = "consent is {consent}"
OUTSIDE_LIMITS_REASON: Final[str] = (
    "the assessment is outside the worker's effort, risk or blocker limits"
)
SETUP_FAILED_TEMPLATE: Final[str] = "setup command failed: {command}"
WAVE_DETAIL_TEMPLATE: Final[str] = "wave {number}: {issues}"
OVERLAP_DETAIL_TEMPLATE: Final[str] = "#{first} and #{second} both changed {paths}"


class ResumeAction(StrEnum):
    SETTLE = "settle"
    RESUME_SESSION = "resume_session"
    START_OVER = "start_over"
    ABANDON = "abandon"


def consent_for(issue: Issue, labels: LabelPolicy) -> Consent:
    if labels.never in issue.labels:
        return Consent.NEVER
    if labels.auto in issue.labels:
        return Consent.AUTO
    if labels.approved in issue.labels:
        return Consent.APPROVED
    return Consent.NEEDS_APPROVAL


def within_worker_limits(task: Task, policy: Policy) -> bool:
    assessment = task.assessment
    if assessment is None or assessment.verdict is not Verdict.EXECUTE:
        return False
    return (
        assessment.effort in policy.worker.allowed_effort
        and assessment.risk in policy.worker.allowed_risk
        and not assessment.blockers
    )


def selectable(task: Task, issue: Issue | None, policy: Policy, inbox: Inbox) -> tuple[bool, str]:
    if task.status is not TaskStatus.ASSESSED:
        return False, f"the task is already {task.status.value}"
    if issue is None:
        return False, CLOSED_ISSUE_REASON
    if task.issue_number in inbox.skips:
        return False, REVIEWER_SKIP_REASON
    consent = consent_for(issue, policy.labels)
    if consent not in CONSENT_TO_WORK_ON and task.issue_number not in inbox.approvals:
        return False, CONSENT_REASON_TEMPLATE.format(consent=consent.value)
    if not within_worker_limits(task, policy):
        return False, OUTSIDE_LIMITS_REASON
    return True, ""


def effort_then_number(task: Task) -> tuple[int, int]:
    rank = (
        EFFORT_ORDER[task.assessment.effort] if task.assessment is not None else len(EFFORT_ORDER)
    )
    return rank, task.issue_number


def execution_order(tasks: list[Task]) -> list[Task]:
    return sorted(tasks, key=effort_then_number)


def delivered_status(
    gate_passed: bool, worker_status: DeliveryStatus, outcome: ClaudeOutcome
) -> TaskStatus:
    if worker_status is DeliveryStatus.NEEDS_INPUT:
        return TaskStatus.NEEDS_INPUT
    if not gate_passed:
        return TaskStatus.ABANDONED
    if worker_status is DeliveryStatus.DONE and outcome is ClaudeOutcome.OK:
        return TaskStatus.REVIEW
    return TaskStatus.UNFINISHED


def record_attempt(
    run_directory: RunDirectory,
    outcome: WorkerOutcome,
    diff: str,
    acceptance: CommandResult | None,
) -> None:
    directory = run_directory.task_directory(outcome.issue_number)
    directory.mkdir(parents=True, exist_ok=True)
    write_record(outcome, directory / DELIVERY_FILENAME)
    (directory / DIFF_FILENAME).write_text(diff)
    if acceptance is not None:
        write_record(acceptance, directory / ACCEPTANCE_FILENAME)


def run_task(
    policy: Policy,
    bench: TaskBench,
    task: Task,
    issue: Issue,
    guidance: TaskGuidance,
    acceptance_tests: dict[int, Path],
    supervisor: RunSupervisor,
    progress: RunProgress,
    deadline: datetime,
    observed_costs: list[float],
) -> tuple[Task, WorkerOutcome, Halt | None]:
    outcome, halt = work_on(
        policy, bench, task, issue, guidance, supervisor, progress, deadline, observed_costs
    )
    return settle(
        policy, bench, task.issue_number, outcome, halt, acceptance_tests, supervisor, progress
    )


def settle(
    policy: Policy,
    bench: TaskBench,
    issue_number: int,
    outcome: WorkerOutcome,
    halt: Halt | None,
    acceptance_tests: dict[int, Path],
    supervisor: RunSupervisor,
    progress: RunProgress,
) -> tuple[Task, WorkerOutcome, Halt | None]:
    worked = progress.task(issue_number).model_copy(update={"delivery": outcome.delivery})
    progress.put_task(worked)
    record_attempt(progress.run_directory, outcome, "", None)
    progress.save()
    if outcome.delivery.status is DeliveryStatus.NEEDS_INPUT:
        discard_changes(bench.workbench, bench.git_settings)
        return finish_task(worked, outcome, None, None), outcome, halt
    supervisor.enter(ActivityKind.GATING, issue_number, None, None)
    gate, diff = judge_branch(policy, bench, outcome, worked, progress)
    acceptance = acceptance_result(
        bench.repo, worked, bench.workbench, acceptance_tests, gate.passed
    )
    if acceptance is not None:
        append_event(
            progress.run_directory,
            EventType.ACCEPTANCE_FINISHED,
            f"exit {acceptance.exit_code}",
            issue_number,
        )
    supervisor.leave(issue_number)
    record_attempt(progress.run_directory, outcome, diff, acceptance)
    write_record(gate, progress.run_directory.task_directory(issue_number) / GATE_FILENAME)
    return finish_task(worked, outcome, gate, acceptance), outcome, halt


def judge_branch(
    policy: Policy, bench: TaskBench, outcome: WorkerOutcome, task: Task, progress: RunProgress
) -> tuple[GateResult, str]:
    workbench = bench.workbench
    git_settings = bench.git_settings
    commit_changes(workbench, policy.identity, commit_subject(outcome, task.title), git_settings)
    base = base_reference(bench.repo)
    files = parse_numstat(changed_files_numstat(workbench, base, git_settings))
    diff = diff_text(workbench, base, git_settings)
    gate = evaluate_gate(
        files,
        diff,
        run_gate_commands(
            bench.repo.gate_commands,
            changed_python_files(files),
            workbench,
            command_environment(WORKER_ENVIRONMENT),
        ),
        commit_count(workbench, base, git_settings),
        policy.worker.max_diff_lines,
        bench.repo.forbidden_paths,
    )
    append_event(
        progress.run_directory,
        EventType.GATE_FINISHED,
        f"{'passed' if gate.passed else 'failed'}, {gate.diff_lines} changed lines",
        task.issue_number,
    )
    return gate, diff


def acceptance_result(
    repo: RepoTarget,
    task: Task,
    workbench: Path,
    acceptance_tests: dict[int, Path],
    gate_passed: bool,
) -> CommandResult | None:
    test_file = acceptance_tests.get(task.issue_number)
    if test_file is None or not gate_passed:
        return None
    return run_acceptance_test(repo, test_file, workbench, command_environment(WORKER_ENVIRONMENT))


def commit_subject(outcome: WorkerOutcome, title: str) -> str:
    subject = outcome.delivery.commit_subject.strip()
    return subject if subject else f"fix: {title.lower()}"


def finish_task(
    task: Task,
    outcome: WorkerOutcome,
    gate: GateResult | None,
    acceptance: CommandResult | None,
) -> Task:
    passed = (gate is not None and gate.passed) and (
        acceptance is None or acceptance.exit_code == 0
    )
    return task.model_copy(
        update={
            "status": delivered_status(passed, outcome.delivery.status, outcome.outcome),
            "delivery": outcome.delivery,
            "gate": gate,
            "attempts": task.attempts + 1,
        }
    )


def ledger_entry(state: RunState, task: Task, policy: Policy) -> LedgerEntry:
    return LedgerEntry(
        run_id=state.run_id,
        repo_key=state.repo_key,
        issue_number=task.issue_number,
        estimate_usd=policy.budget.per_task_usd,
        actual_usd=task.cost_usd,
        outcome=task.status,
        session_id=task.session_id,
        finished_at=datetime.now(UTC),
    )


def prepare_execution(
    policy: Policy, repo: RepoTarget, repo_key: str
) -> tuple[Path, dict[str, str], list[CommandResult]]:
    render_fences(policy.workspace, Path.home(), repo.forbidden_paths)
    token = repository_token(repo)
    workbench = prepare_checkout(repo, repo_key, policy.workspace, token)
    git_settings = git_environment(token, write_askpass_script(policy.state_dir))
    prune_worktrees(workbench, policy.workspace.worktrees_path(repo_key), git_settings)
    return workbench, git_settings, run_setup_commands(repo, workbench, git_settings)


def current_issues(reader: BoardReader, limit: int) -> dict[int, Issue]:
    return {issue.number: issue for issue in reader.open_issues(limit)}


def approved_tasks(
    policy: Policy, state: RunState, issues: dict[int, Issue], run_directory: RunDirectory
) -> list[Task]:
    inbox = read_inbox(run_directory.inbox)
    candidates = [task for task in state.tasks if task.assessment is not None]
    approved: list[Task] = []
    for task in execution_order(candidates):
        allowed, reason = selectable(task, issues.get(task.issue_number), policy, inbox)
        if allowed:
            approved.append(task)
        elif task.status is TaskStatus.ASSESSED:
            append_event(run_directory, EventType.TASK_SKIPPED, reason, task.issue_number)
    return approved


def execute_run(
    policy: Policy,
    repo: RepoTarget,
    repo_key: str,
    reader: BoardReader,
    run_directory: RunDirectory,
    state: RunState,
    limit: int,
    supervisor: RunSupervisor,
    deadline: datetime,
) -> RunState:
    progress = RunProgress(run_directory, state)
    progress.enter_phase(RunPhase.EXECUTE)
    progress.save()
    issues = current_issues(reader, limit)
    acceptance_tests = load_acceptance_map(
        acceptance_map_path(policy.state_dir), policy.workspace.acceptance_dir
    )
    halt = finish_interrupted_tasks(
        policy, repo, repo_key, issues, acceptance_tests, supervisor, progress, deadline
    )
    if halt is not None:
        return close_execution(progress, halt, [], 0)
    approved = approved_tasks(policy, progress.state, issues, run_directory)
    workbench, git_settings, setup = prepare_execution(policy, repo, repo_key)
    failed_setup = [result for result in setup if result.exit_code != 0]
    if failed_setup:
        note = f"setup command failed: {failed_setup[0].command}"
        append_event(run_directory, EventType.RUN_ABORTED, note, None)
        progress.note(note)
        progress.stop(StopReason.SETUP_FAILED, note)
        progress.enter_phase(RunPhase.ABORTED)
        return progress.save()
    bench = task_bench(policy, repo, workbench, git_settings, BenchKind.CHECKOUT)
    return work_through(
        policy, bench, repo_key, approved, issues, progress, acceptance_tests, supervisor, deadline
    )


def task_bench(
    policy: Policy, repo: RepoTarget, workbench: Path, git_settings: dict[str, str], kind: BenchKind
) -> TaskBench:
    return TaskBench(
        repo=repo,
        prompts=load_worker_prompts(repo, policy.workspace.prompts_dir),
        workbench=workbench,
        git_settings=git_settings,
        environment=agent_environment(
            policy.agent_home,
            read_oauth_token(policy.workspace.oauth_token_path),
            WORKER_ENVIRONMENT,
        ),
        kind=kind,
    )


def worktree_for(policy: Policy, bench: TaskBench, repo_key: str, issue: Issue) -> TaskBench:
    branch = branch_name(policy.worker.branch_prefix, issue.number, issue.title)
    worktree = policy.workspace.worktree_path(repo_key, branch)
    add_worktree(bench.workbench, worktree, branch, base_reference(bench.repo), bench.git_settings)
    return bench.model_copy(update={"workbench": worktree, "kind": BenchKind.WORKTREE})


def worktree_in_use(workbench: Path, worktree: Path, git_settings: dict[str, str]) -> bool:
    if not (workbench / ".git").is_dir() or not worktree.is_dir():
        return False
    registered = {path.resolve() for path in registered_worktrees(workbench, git_settings)}
    return worktree.resolve() in registered


def resume_action(
    task: Task, on_task_branch: bool, calls_allowed: bool, budget_left_usd: float
) -> ResumeAction:
    can_call = calls_allowed and budget_left_usd > 0 and task.resumes < MAX_TASK_RESUMES
    if on_task_branch and (task.delivery is not None or not can_call):
        return ResumeAction.SETTLE
    if not can_call:
        return ResumeAction.ABANDON
    return ResumeAction.RESUME_SESSION if on_task_branch else ResumeAction.START_OVER


def recorded_outcome(run_directory: RunDirectory, task: Task) -> WorkerOutcome:
    record = run_directory.task_directory(task.issue_number) / DELIVERY_FILENAME
    if task.delivery is not None and record.is_file():
        return WorkerOutcome.model_validate_json(record.read_text())
    return WorkerOutcome(
        issue_number=task.issue_number,
        delivery=abandoned_delivery(ClaudeOutcome.KILLED),
        outcome=ClaudeOutcome.KILLED,
        cost_usd=0.0,
        session_id=task.session_id,
        permission_denials=[],
    )


def interrupted_bench(
    policy: Policy, repo: RepoTarget, repo_key: str, task: Task
) -> tuple[TaskBench, bool]:
    git_settings = git_environment(repository_token(repo), write_askpass_script(policy.state_dir))
    workbench = policy.workspace.workbench_path(repo_key)
    if task.branch is not None:
        worktree = policy.workspace.worktree_path(repo_key, task.branch)
        if (
            worktree_in_use(workbench, worktree, git_settings)
            and current_branch(worktree, git_settings) == task.branch
        ):
            return task_bench(policy, repo, worktree, git_settings, BenchKind.WORKTREE), True
    on_task_branch = (workbench / ".git").is_dir() and current_branch(
        workbench, git_settings
    ) == task.branch
    return task_bench(policy, repo, workbench, git_settings, BenchKind.CHECKOUT), on_task_branch


def finish_interrupted_tasks(
    policy: Policy,
    repo: RepoTarget,
    repo_key: str,
    issues: dict[int, Issue],
    acceptance_tests: dict[int, Path],
    supervisor: RunSupervisor,
    progress: RunProgress,
    deadline: datetime,
) -> Halt | None:
    working = [task for task in progress.state.tasks if task.status is TaskStatus.WORKING]
    for task in working:
        halt = finish_interrupted_task(
            policy, repo, repo_key, task, issues, acceptance_tests, supervisor, progress, deadline
        )
        if halt is not None:
            return halt
    return None


def release_bench(policy: Policy, repo_key: str, bench: TaskBench) -> None:
    if bench.kind is BenchKind.WORKTREE:
        workbench = policy.workspace.workbench_path(repo_key)
        remove_worktree(workbench, bench.workbench, bench.git_settings)


def finish_interrupted_task(
    policy: Policy,
    repo: RepoTarget,
    repo_key: str,
    task: Task,
    issues: dict[int, Issue],
    acceptance_tests: dict[int, Path],
    supervisor: RunSupervisor,
    progress: RunProgress,
    deadline: datetime,
) -> Halt | None:
    bench, on_task_branch = interrupted_bench(policy, repo, repo_key, task)
    issue = issues.get(task.issue_number)
    calls_allowed = (
        issue is not None and datetime.now(UTC) < deadline and not supervisor.stop_requested()
    )
    action = resume_action(
        task, on_task_branch, calls_allowed, policy.budget.per_task_usd - task.worker_cost_usd
    )
    append_event(
        progress.run_directory,
        EventType.TASK_RESUMED,
        f"interrupted task: {action.value}",
        task.issue_number,
    )
    if action is ResumeAction.ABANDON:
        progress.put_task(task.model_copy(update={"status": TaskStatus.ABANDONED}))
        progress.save()
        return None
    observed_costs: list[float] = []
    if action is ResumeAction.SETTLE:
        outcome = recorded_outcome(progress.run_directory, task)
        updated, outcome, halt = settle(
            policy, bench, task.issue_number, outcome, None, acceptance_tests, supervisor, progress
        )
        record_finished(policy, progress, updated)
        release_bench(policy, repo_key, bench)
        return halt
    if issue is None:
        raise ValueError(f"issue #{task.issue_number} needs its issue to carry on")
    guidance = TaskGuidance(
        answers=guidance_for(
            read_inbox(progress.run_directory.inbox),
            read_briefing(policy.state_dir, progress.state.repo_key),
            task,
        ),
        wave_paths=[],
    )
    if action is ResumeAction.START_OVER:
        updated, outcome, halt = run_task(
            policy,
            bench,
            task,
            issue,
            guidance,
            acceptance_tests,
            supervisor,
            progress,
            deadline,
            observed_costs,
        )
    else:
        resumed = task.model_copy(update={"resumes": task.resumes + 1})
        progress.put_task(resumed)
        progress.save()
        worker_outcome, worker_halt = drive_worker(
            policy,
            bench,
            resumed,
            issue,
            task_prompt(policy, bench, resumed, issue, guidance),
            True,
            supervisor,
            progress,
            deadline,
            observed_costs,
        )
        updated, outcome, halt = settle(
            policy,
            bench,
            task.issue_number,
            worker_outcome,
            worker_halt,
            acceptance_tests,
            supervisor,
            progress,
        )
    record_finished(policy, progress, updated)
    release_bench(policy, repo_key, bench)
    return halt if halt is not None else outcome_halt(outcome.outcome)


def record_finished(policy: Policy, progress: RunProgress, updated: Task) -> None:
    progress.put_task(updated)
    progress.save()
    append_event(
        progress.run_directory,
        EventType.TASK_FINISHED,
        f"{updated.status.value} on {updated.branch}",
        updated.issue_number,
    )
    append_record(ledger_entry(progress.state, updated, policy), ledger_path(policy.state_dir))


def before_task(
    policy: Policy, progress: RunProgress, supervisor: RunSupervisor, deadline: datetime
) -> Halt | None:
    run_directory = progress.run_directory
    if datetime.now(UTC) >= deadline:
        append_event(run_directory, EventType.RUN_ABORTED, DEADLINE_REASON, None)
        return Halt(reason=StopReason.WINDOW_CLOSED, detail=DEADLINE_REASON)
    hold = supervisor.hold_while_paused(deadline)
    if hold is Hold.DEADLINE:
        append_event(run_directory, EventType.RUN_ABORTED, DEADLINE_REASON, None)
        return Halt(reason=StopReason.WINDOW_CLOSED, detail=DEADLINE_REASON)
    if hold is Hold.STOPPED or supervisor.stop_requested():
        append_event(run_directory, EventType.RUN_ABORTED, STOP_DETAIL, None)
        return Halt(reason=StopReason.OPERATOR, detail=STOP_DETAIL)
    if progress.state.spent_usd + policy.budget.per_task_usd > policy.budget.envelope_usd:
        append_event(run_directory, EventType.BUDGET_EXHAUSTED, "envelope", None)
        return Halt(reason=StopReason.ENVELOPE, detail=ENVELOPE_REASON)
    return None


def outcome_halt(outcome: ClaudeOutcome) -> Halt | None:
    reason = OUTCOME_STOPS.get(outcome)
    if reason is None:
        return None
    detail = STOP_DETAIL if reason is StopReason.OPERATOR else outcome.value
    return Halt(reason=reason, detail=detail)


def worked_count(state: RunState) -> int:
    return sum(1 for task in state.tasks if task.status in WORKED_STATUSES)


def work_task(
    policy: Policy,
    bench: TaskBench,
    task: Task,
    issue: Issue,
    guidance: TaskGuidance,
    acceptance_tests: dict[int, Path],
    supervisor: RunSupervisor,
    progress: RunProgress,
    deadline: datetime,
    observed_costs: list[float],
) -> tuple[Task, Halt | None]:
    updated, outcome, halt = run_task(
        policy,
        bench,
        task,
        issue,
        guidance,
        acceptance_tests,
        supervisor,
        progress,
        deadline,
        observed_costs,
    )
    record_finished(policy, progress, updated)
    return updated, halt if halt is not None else outcome_halt(outcome.outcome)


def allowance_halt(
    policy: Policy,
    bench: TaskBench,
    progress: RunProgress,
    supervisor: RunSupervisor,
    deadline: datetime,
    observed_costs: list[float],
) -> Halt | None:
    verdict = await_allowance(
        policy,
        supervisor,
        bench.workbench,
        bench.environment,
        deadline,
        progress.state.usage,
        None,
        observed_costs,
    )
    progress.spend(verdict.spent_usd)
    progress.observe(verdict.reading)
    if verdict.proceed:
        return None
    append_event(progress.run_directory, EventType.LIMIT_REACHED, verdict.detail, None)
    return Halt(reason=verdict.stop_reason or StopReason.ALLOWANCE, detail=verdict.detail)


def work_in_turn(
    policy: Policy,
    bench: TaskBench,
    queue: list[Task],
    issues: dict[int, Issue],
    progress: RunProgress,
    acceptance_tests: dict[int, Path],
    supervisor: RunSupervisor,
    deadline: datetime,
) -> Halt | None:
    observed_costs: list[float] = []
    for task in queue:
        halt = before_task(policy, progress, supervisor, deadline) or allowance_halt(
            policy, bench, progress, supervisor, deadline, observed_costs
        )
        if halt is not None:
            return halt
        inbox = read_inbox(progress.run_directory.inbox)
        briefing = read_briefing(policy.state_dir, progress.state.repo_key)
        _, halt = work_task(
            policy,
            bench,
            task,
            issues[task.issue_number],
            TaskGuidance(answers=guidance_for(inbox, briefing, task), wave_paths=[]),
            acceptance_tests,
            supervisor,
            progress,
            deadline,
            observed_costs,
        )
        if halt is not None:
            append_event(progress.run_directory, EventType.LIMIT_REACHED, halt.detail, None)
            return halt
    return None


def affordable_task_count(policy: Policy, spent_usd: float) -> int:
    return int((policy.budget.envelope_usd - spent_usd) // policy.budget.per_task_usd)


def next_wave(policy: Policy, spent_usd: float, queue: list[Task]) -> Wave:
    wave = waves_of(queue, policy.worker.shared_paths)[0]
    size = min(len(wave.tasks), policy.worker.parallel, affordable_task_count(policy, spent_usd))
    return wave.model_copy(update={"tasks": wave.tasks[:size]})


def open_worktrees(
    policy: Policy,
    bench: TaskBench,
    repo_key: str,
    tasks: list[Task],
    issues: dict[int, Issue],
    progress: RunProgress,
) -> tuple[list[TaskBench], Halt | None]:
    fetch_base(bench.repo, bench.workbench, bench.git_settings)
    benches: list[TaskBench] = []
    for task in tasks:
        worktree_bench = worktree_for(policy, bench, repo_key, issues[task.issue_number])
        benches.append(worktree_bench)
        setup = run_setup_commands(bench.repo, worktree_bench.workbench, bench.git_settings)
        failed = [result for result in setup if result.exit_code != 0]
        if failed:
            note = SETUP_FAILED_TEMPLATE.format(command=failed[0].command)
            append_event(progress.run_directory, EventType.RUN_ABORTED, note, task.issue_number)
            progress.note(note)
            close_worktrees(policy, bench, repo_key)
            return [], Halt(reason=StopReason.SETUP_FAILED, detail=note)
    return benches, None


def close_worktrees(policy: Policy, bench: TaskBench, repo_key: str) -> None:
    prune_worktrees(bench.workbench, policy.workspace.worktrees_path(repo_key), bench.git_settings)


def changed_paths_of(task: Task) -> list[str]:
    return task.gate.changed_paths if task.gate is not None else []


def note_overlaps(policy: Policy, progress: RunProgress, tasks: list[Task]) -> None:
    finished = [progress.task(task.issue_number) for task in tasks]
    changed = {task.issue_number: changed_paths_of(task) for task in finished if publishable(task)}
    found = overlaps_within(changed, policy.worker.shared_paths)
    for task in finished:
        if task.issue_number in found:
            progress.put_task(task.model_copy(update={"overlaps": found[task.issue_number]}))
    if found:
        progress.save()
    for number, overlaps in found.items():
        for overlap in overlaps:
            if number < overlap.issue_number:
                detail = OVERLAP_DETAIL_TEMPLATE.format(
                    first=number, second=overlap.issue_number, paths=", ".join(overlap.paths)
                )
                append_event(progress.run_directory, EventType.OVERLAP_FOUND, detail, number)


def wave_detail(number: int, tasks: list[Task]) -> str:
    issues = ", ".join(f"#{task.issue_number}" for task in tasks)
    return WAVE_DETAIL_TEMPLATE.format(number=number, issues=issues)


def run_wave(
    policy: Policy,
    bench: TaskBench,
    repo_key: str,
    wave: Wave,
    number: int,
    issues: dict[int, Issue],
    progress: RunProgress,
    acceptance_tests: dict[int, Path],
    supervisor: RunSupervisor,
    deadline: datetime,
    observed_costs: list[float],
) -> Halt | None:
    tasks = [
        task.model_copy(update={"wave": number, "solo_reason": wave.solo_reason})
        for task in wave.tasks
    ]
    for task in tasks:
        progress.put_task(task)
    progress.save()
    append_event(progress.run_directory, EventType.WAVE_STARTED, wave_detail(number, tasks), None)
    benches, halt = open_worktrees(policy, bench, repo_key, tasks, issues, progress)
    if halt is not None:
        return halt
    inbox = read_inbox(progress.run_directory.inbox)
    briefing = read_briefing(policy.state_dir, progress.state.repo_key)
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = [
            pool.submit(
                work_task,
                policy,
                worktree_bench,
                task,
                issues[task.issue_number],
                TaskGuidance(
                    answers=guidance_for(inbox, briefing, task),
                    wave_paths=sibling_paths(tasks, task),
                ),
                acceptance_tests,
                supervisor,
                progress,
                deadline,
                observed_costs,
            )
            for task, worktree_bench in zip(tasks, benches, strict=True)
        ]
        halts = [halt for _, halt in (future.result() for future in futures) if halt is not None]
    note_overlaps(policy, progress, tasks)
    close_worktrees(policy, bench, repo_key)
    if halts:
        append_event(progress.run_directory, EventType.LIMIT_REACHED, halts[0].detail, None)
        return halts[0]
    return None


def work_in_waves(
    policy: Policy,
    bench: TaskBench,
    repo_key: str,
    queue: list[Task],
    issues: dict[int, Issue],
    progress: RunProgress,
    acceptance_tests: dict[int, Path],
    supervisor: RunSupervisor,
    deadline: datetime,
) -> Halt | None:
    observed_costs: list[float] = []
    number = 0
    while queue:
        halt = before_task(policy, progress, supervisor, deadline) or allowance_halt(
            policy, bench, progress, supervisor, deadline, observed_costs
        )
        if halt is not None:
            return halt
        number += 1
        wave = next_wave(policy, progress.state.spent_usd, queue)
        queue = queue[len(wave.tasks) :]
        halt = run_wave(
            policy,
            bench,
            repo_key,
            wave,
            number,
            issues,
            progress,
            acceptance_tests,
            supervisor,
            deadline,
            observed_costs,
        )
        if halt is not None:
            return halt
    return None


def work_through(
    policy: Policy,
    bench: TaskBench,
    repo_key: str,
    approved: list[Task],
    issues: dict[int, Issue],
    progress: RunProgress,
    acceptance_tests: dict[int, Path],
    supervisor: RunSupervisor,
    deadline: datetime,
) -> RunState:
    capacity = max(policy.budget.max_tasks - worked_count(progress.state), 0)
    queue = approved[:capacity]
    if policy.worker.parallel > 1:
        halt = work_in_waves(
            policy, bench, repo_key, queue, issues, progress, acceptance_tests, supervisor, deadline
        )
    else:
        halt = work_in_turn(
            policy, bench, queue, issues, progress, acceptance_tests, supervisor, deadline
        )
    return close_execution(progress, halt, approved, capacity)


def close_execution(
    progress: RunProgress, halt: Halt | None, approved: list[Task], capacity: int
) -> RunState:
    if halt is not None:
        progress.stop(halt.reason, halt.detail)
        progress.note(BUDGET_NOTE_TEMPLATE.format(reason=halt.detail))
    if len(approved) > capacity:
        progress.note(f"{len(approved) - capacity} approved tasks left for next time")
    progress.enter_phase(RunPhase.PUBLISH)
    return progress.save()
