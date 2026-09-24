from __future__ import annotations

from datetime import datetime
from typing import Final

from weekend_loop.briefing import RepoBriefing, answers_of, question_key
from weekend_loop.models import Inbox, Record, RunState, Task, TaskStatus, Verdict

UNSET: Final[str] = "—"
APPROVABLE_VERDICTS: Final[tuple[Verdict, ...]] = (Verdict.EXECUTE, Verdict.PROPOSE)
REVIEW_STATUSES: Final[tuple[TaskStatus, ...]] = (TaskStatus.REVIEW,)
ANSWER_SOURCE_RUN: Final[str] = "run"
ANSWER_SOURCE_BRIEFING: Final[str] = "briefing"
ANSWER_SOURCE_NONE: Final[str] = ""
LIVE_TRANSCRIPT_LINES: Final[int] = 30
LIVE_EVENT_COUNT: Final[int] = 10
NO_TRANSCRIPT_TEXT: Final[str] = "no transcript yet"
NO_EVENT_TEXT: Final[str] = "no event yet"


class TaskRow(Record):
    issue_number: int
    title: str
    status: str
    verdict: str
    effort: str
    risk: str
    branch: str
    cost_usd: float
    diff_lines: int


class QuestionRow(Record):
    issue_number: int
    title: str
    question_index: int
    question: str
    answer: str
    source: str


class RunView(Record):
    run_id: str
    repo_key: str
    mode: str
    phase: str
    spent_usd: float
    envelope_usd: float
    budget_fraction: float
    heartbeat_age_seconds: float
    counts: dict[str, int]
    tasks: list[TaskRow]
    review: list[TaskRow]
    questions: list[QuestionRow]
    approvals: list[TaskRow]
    notes: list[str]
    messages: list[str]
    stop: bool
    pause: bool


def task_row(task: Task) -> TaskRow:
    assessment = task.assessment
    return TaskRow(
        issue_number=task.issue_number,
        title=task.title,
        status=task.status.value,
        verdict=assessment.verdict.value if assessment is not None else UNSET,
        effort=assessment.effort.value if assessment is not None else UNSET,
        risk=assessment.risk.value if assessment is not None else UNSET,
        branch=task.branch if task.branch is not None else UNSET,
        cost_usd=task.cost_usd,
        diff_lines=task.gate.diff_lines if task.gate is not None else 0,
    )


def status_counts(state: RunState) -> dict[str, int]:
    counts: dict[str, int] = {}
    for task in state.tasks:
        counts[task.status.value] = counts.get(task.status.value, 0) + 1
    return dict(sorted(counts.items()))


def budget_fraction(state: RunState) -> float:
    if state.envelope_usd <= 0:
        return 0.0
    return min(state.spent_usd / state.envelope_usd, 1.0)


def heartbeat_age_seconds(state: RunState, now: datetime) -> float:
    return max((now - state.heartbeat_at).total_seconds(), 0.0)


def answered_indices(inbox: Inbox, issue_number: int) -> dict[int, str]:
    return {
        answer.question_index: answer.text
        for answer in inbox.answers
        if answer.issue_number == issue_number
    }


def questions_of(task: Task) -> list[str]:
    if task.delivery is not None and task.delivery.questions:
        return task.delivery.questions
    return task.assessment.questions if task.assessment is not None else []


def question_row(
    task: Task, index: int, question: str, live: dict[int, str], standing: dict[str, str]
) -> QuestionRow:
    answer = live.get(index, standing.get(question_key(question), ""))
    if index in live:
        source = ANSWER_SOURCE_RUN
    elif answer:
        source = ANSWER_SOURCE_BRIEFING
    else:
        source = ANSWER_SOURCE_NONE
    return QuestionRow(
        issue_number=task.issue_number,
        title=task.title,
        question_index=index,
        question=question,
        answer=answer,
        source=source,
    )


def open_questions(state: RunState, inbox: Inbox, briefing: RepoBriefing) -> list[QuestionRow]:
    rows: list[QuestionRow] = []
    for task in state.tasks:
        live = answered_indices(inbox, task.issue_number)
        standing = {
            key: answer.text for key, answer in answers_of(briefing, task.issue_number).items()
        }
        for index, question in enumerate(questions_of(task)):
            rows.append(question_row(task, index, question, live, standing))
    return rows


def awaiting_approval(state: RunState, inbox: Inbox) -> list[TaskRow]:
    return [
        task_row(task)
        for task in state.tasks
        if task.status is TaskStatus.ASSESSED
        and task.assessment is not None
        and task.assessment.verdict in APPROVABLE_VERDICTS
        and task.issue_number not in inbox.approvals
        and task.issue_number not in inbox.skips
    ]


def build_view(state: RunState, inbox: Inbox, briefing: RepoBriefing, now: datetime) -> RunView:
    rows = [task_row(task) for task in state.tasks]
    return RunView(
        run_id=state.run_id,
        repo_key=state.repo_key,
        mode=state.mode.value,
        phase=state.phase.value,
        spent_usd=state.spent_usd,
        envelope_usd=state.envelope_usd,
        budget_fraction=budget_fraction(state),
        heartbeat_age_seconds=heartbeat_age_seconds(state, now),
        counts=status_counts(state),
        tasks=rows,
        review=[row for row in rows if row.status in {status.value for status in REVIEW_STATUSES}],
        questions=open_questions(state, inbox, briefing),
        approvals=awaiting_approval(state, inbox),
        notes=state.notes,
        messages=inbox.messages,
        stop=inbox.stop,
        pause=inbox.pause,
    )
