from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from weekend_loop.allowance import UNTIMED_FIVE_HOUR_LIMIT, await_allowance
from weekend_loop.models import (
    ActivityKind,
    ClaudeOutcome,
    EventType,
    Halt,
    Issue,
    Policy,
    Record,
    RepoTarget,
    StopReason,
    Task,
    TaskStatus,
    WorkerOutcome,
)
from weekend_loop.runs import RunProgress, append_event
from weekend_loop.supervision import RunSupervisor
from weekend_loop.workbench import branch_name, create_task_branch, reset_to_base
from weekend_loop.worker import WorkerCall, WorkerPrompts, render_task, run_worker

TASK_FILENAME: Final[str] = "TASK.md"
WORKER_TRANSCRIPT_ROLE: Final[str] = "worker"
MAX_STALL_RESUMES: Final[int] = 1


class WorkerStep(StrEnum):
    FINISH = "finish"
    WAIT_AND_RESUME = "wait_and_resume"
    RESUME = "resume"
    START_FRESH = "start_fresh"


class TaskBench(Record):
    repo: RepoTarget
    prompts: WorkerPrompts
    workbench: Path
    git_settings: dict[str, str]
    environment: dict[str, str]


def next_worker_step(outcome: ClaudeOutcome, stall_resumes: int, fresh_used: bool) -> WorkerStep:
    if outcome is ClaudeOutcome.WINDOW_LIMIT:
        return WorkerStep.WAIT_AND_RESUME
    if outcome is ClaudeOutcome.STALLED and stall_resumes < MAX_STALL_RESUMES:
        return WorkerStep.RESUME
    if outcome is ClaudeOutcome.SESSION_MISSING and not fresh_used:
        return WorkerStep.START_FRESH
    return WorkerStep.FINISH


def worker_cost_after(previous_usd: float, reported_usd: float) -> float:
    # A resumed call reports its own cost only (checked against claude 2.1.276).
    return previous_usd + reported_usd


def session_of(task: Task) -> str:
    if task.session_id is None:
        raise ValueError(f"issue #{task.issue_number} is working without a session")
    return task.session_id


def branch_of(task: Task) -> str:
    if task.branch is None:
        raise ValueError(f"issue #{task.issue_number} is working without a branch")
    return task.branch


def start_branch(
    policy: Policy, bench: TaskBench, task: Task, issue: Issue, progress: RunProgress
) -> Task:
    branch = branch_name(policy.worker.branch_prefix, issue.number, issue.title)
    reset_to_base(bench.repo, bench.workbench, bench.git_settings)
    create_task_branch(bench.workbench, branch, bench.git_settings)
    started = task.model_copy(
        update={"status": TaskStatus.WORKING, "branch": branch, "session_id": str(uuid.uuid4())}
    )
    progress.put_task(started)
    progress.save()
    append_event(progress.run_directory, EventType.TASK_STARTED, f"branch {branch}", issue.number)
    return started


def call_worker(
    policy: Policy,
    bench: TaskBench,
    prompt: str,
    task: Task,
    resume: bool,
    supervisor: RunSupervisor,
    progress: RunProgress,
) -> WorkerOutcome:
    transcript = supervisor.transcript_for(WORKER_TRANSCRIPT_ROLE, task.issue_number)
    supervisor.enter(ActivityKind.WORKING, task.issue_number, transcript, None)
    call = WorkerCall(
        session_id=session_of(task),
        resume=resume,
        budget_usd=policy.budget.per_task_usd - task.worker_cost_usd,
        transcript=transcript,
    )
    outcome = run_worker(
        policy,
        bench.prompts,
        prompt,
        bench.workbench,
        bench.environment,
        call,
        supervisor,
        task.issue_number,
    )
    progress.put_task(
        task.model_copy(
            update={
                "worker_cost_usd": worker_cost_after(task.worker_cost_usd, outcome.cost_usd),
                "cost_usd": task.cost_usd + outcome.cost_usd,
                "session_id": outcome.session_id,
            }
        )
    )
    progress.spend(outcome.cost_usd)
    progress.save()
    append_event(
        progress.run_directory,
        EventType.WORKER_FINISHED,
        f"{outcome.delivery.status.value} for ${outcome.cost_usd:.2f} "
        f"[{outcome.outcome.value}], {len(outcome.permission_denials)} denials",
        task.issue_number,
    )
    return outcome


def wait_for_allowance(
    policy: Policy,
    bench: TaskBench,
    outcome: WorkerOutcome,
    supervisor: RunSupervisor,
    progress: RunProgress,
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
        outcome.rejection if outcome.rejection is not None else UNTIMED_FIVE_HOUR_LIMIT,
        observed_costs,
    )
    progress.spend(verdict.spent_usd)
    progress.observe(verdict.reading)
    progress.save()
    if verdict.proceed:
        return None
    return Halt(reason=verdict.stop_reason or StopReason.ALLOWANCE, detail=verdict.detail)


def write_task_record(progress: RunProgress, issue_number: int, prompt: str) -> None:
    record = progress.run_directory.task_directory(issue_number) / TASK_FILENAME
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(prompt)


def task_prompt(
    policy: Policy, bench: TaskBench, task: Task, issue: Issue, answers: list[str]
) -> str:
    assessment = task.assessment
    if assessment is None:
        raise ValueError(f"issue #{task.issue_number} has no assessment to execute")
    return render_task(
        bench.prompts.task_template,
        issue,
        assessment,
        bench.repo,
        branch_of(task),
        policy.worker.max_diff_lines,
        answers,
    )


def work_on(
    policy: Policy,
    bench: TaskBench,
    task: Task,
    issue: Issue,
    answers: list[str],
    supervisor: RunSupervisor,
    progress: RunProgress,
    deadline: datetime,
    observed_costs: list[float],
) -> tuple[WorkerOutcome, Halt | None]:
    working = start_branch(policy, bench, task, issue, progress)
    prompt = task_prompt(policy, bench, working, issue, answers)
    write_task_record(progress, issue.number, prompt)
    return drive_worker(
        policy, bench, working, issue, prompt, False, supervisor, progress, deadline, observed_costs
    )


def drive_worker(
    policy: Policy,
    bench: TaskBench,
    task: Task,
    issue: Issue,
    prompt: str,
    resume: bool,
    supervisor: RunSupervisor,
    progress: RunProgress,
    deadline: datetime,
    observed_costs: list[float],
) -> tuple[WorkerOutcome, Halt | None]:
    working = task
    stall_resumes = 0
    fresh_used = False
    while True:
        outcome = call_worker(policy, bench, prompt, working, resume, supervisor, progress)
        working = progress.task(issue.number)
        step = next_worker_step(outcome.outcome, stall_resumes, fresh_used)
        if step is WorkerStep.FINISH or working.worker_cost_usd >= policy.budget.per_task_usd:
            return outcome, None
        if step is WorkerStep.WAIT_AND_RESUME:
            halt = wait_for_allowance(
                policy, bench, outcome, supervisor, progress, deadline, observed_costs
            )
            if halt is not None:
                return outcome, halt
        if step is WorkerStep.START_FRESH:
            fresh_used = True
            working = start_branch(policy, bench, working, issue, progress)
            resume = False
            continue
        stall_resumes += int(step is WorkerStep.RESUME)
        resume = True
        append_event(
            progress.run_directory,
            EventType.TASK_RESUMED,
            f"resuming session {session_of(working)} after {outcome.outcome.value}",
            issue.number,
        )
