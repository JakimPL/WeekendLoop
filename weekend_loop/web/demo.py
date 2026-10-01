from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo

from weekend_loop.adopt import ADOPTED_NOTE_TEMPLATE
from weekend_loop.execute import (
    CLOSED_ISSUE_REASON,
    CONSENT_REASON_TEMPLATE,
    DEADLINE_REASON,
    ENVELOPE_REASON,
    OUTSIDE_LIMITS_REASON,
    PROPOSAL_REASON,
    REVIEWER_SKIP_REASON,
)
from weekend_loop.models import (
    Consent,
    DeliveryStatus,
    EventType,
    IneligibilityReason,
    Record,
    RunEvent,
    RunState,
    Task,
    TaskStatus,
    Verdict,
)
from weekend_loop.status import Liveness, RunStatus, current_phrase
from weekend_loop.web.theme import browsable

CLOCK_FORMAT: Final[str] = "%H:%M:%S"
DETAIL_SEPARATOR: Final[str] = " · "
REASON_SEPARATOR: Final[str] = ", "
BRANCH_PREFIX: Final[str] = "branch "
QUESTIONS_COMMENT: Final[str] = "questions"
REVIEW_COMMENT: Final[str] = "review"
GATE_PASSED: Final[str] = "passed"
ACCEPTANCE_PASSED_EXIT: Final[int] = 0
ADOPTED_TEXT: Final[str] = "picks up the prepared triage"
ASSESSMENT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?P<verdict>\w+) \((?P<effort>\w+)/(?P<risk>\w+)\)"
)
PREFILTER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^(?P<eligible>\d+) of (?P<total>\d+) ")
TRIAGE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^(?P<count>\d+) tasks")
WORKER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^(?P<status>\w+) for ")
GATE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?P<verdict>\w+), (?P<lines>\d+) changed lines"
)
ACCEPTANCE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^exit (?P<code>-?\d+)")
TASK_FINISHED_PATTERN: Final[re.Pattern[str]] = re.compile(r"^(?P<status>\w+) on ")
PULL_REQUEST_PATTERN: Final[re.Pattern[str]] = re.compile(r"/pull/(?P<number>\d+)$")


class Tone(StrEnum):
    POSITIVE = "positive"
    ACTIVE = "active"
    ATTENTION = "attention"
    NEGATIVE = "negative"
    MUTED = "muted"
    PLAIN = "plain"


class Outcome(Record):
    text: str
    tone: Tone
    detail: str | None


class DemoStep(Record):
    time: str
    issue_number: int | None
    step: str
    outcome: Outcome


class DemoTask(Record):
    issue_number: int
    title: str
    outcome: Outcome
    link: str | None


class DemoView(Record):
    run_id: str
    liveness: Liveness
    activity: str | None
    steps: list[DemoStep]
    tasks: list[DemoTask]


def outcome(text: str, tone: Tone, detail: str | None) -> Outcome:
    return Outcome(text=text, tone=tone, detail=detail)


STEP_LABELS: Final[dict[EventType, str]] = {
    EventType.RUN_STARTED: "Run started",
    EventType.RUN_RESUMED: "Run resumed",
    EventType.PREFILTER_FINISHED: "Backlog filtered",
    EventType.ASSESSMENT_STARTED: "Re-assessing",
    EventType.ASSESSMENT_FINISHED: "Assessed",
    EventType.TASK_STARTED: "Work started",
    EventType.TASK_RESUMED: "Work resumed",
    EventType.TASK_SKIPPED: "Not picked up",
    EventType.WAVE_STARTED: "Wave started",
    EventType.OVERLAP_FOUND: "Merge with care",
    EventType.WORKER_FINISHED: "Agent finished",
    EventType.GATE_FINISHED: "Checks",
    EventType.ACCEPTANCE_FINISHED: "Hidden tests",
    EventType.TASK_FINISHED: "Task finished",
    EventType.TASK_FAILED: "Task failed",
    EventType.MEMORY_WAITED: "Waiting for memory",
    EventType.STACK_LINKED: "Stack linked",
    EventType.BASELINE_FINISHED: "Base branch checked",
    EventType.BRANCH_PUSHED: "Branch pushed",
    EventType.PULL_REQUEST_OPENED: "Draft PR opened",
    EventType.COMMENT_POSTED: "Comment posted",
    EventType.ANSWER_RECEIVED: "Reply read",
    EventType.LABEL_WRITTEN: "Label added",
    EventType.LABEL_REMOVED: "Label removed",
    EventType.DIGEST_POSTED: "Summary posted",
    EventType.BUDGET_EXHAUSTED: "Budget reached",
    EventType.LIMIT_REACHED: "Limit reached",
    EventType.USAGE_PARKED: "Waiting for allowance",
    EventType.RUN_FINISHED: "Triage finished",
    EventType.RUN_ABORTED: "Run stopped",
}
VERDICT_OUTCOMES: Final[dict[Verdict, tuple[str, Tone]]] = {
    Verdict.EXECUTE: ("do it", Tone.POSITIVE),
    Verdict.PROPOSE: ("propose", Tone.ACTIVE),
    Verdict.NEEDS_INPUT: ("ask first", Tone.ATTENTION),
    Verdict.SKIP: ("skip", Tone.MUTED),
}
DELIVERY_OUTCOMES: Final[dict[DeliveryStatus, tuple[str, Tone]]] = {
    DeliveryStatus.DONE: ("done", Tone.POSITIVE),
    DeliveryStatus.PARTIAL: ("partly done", Tone.ATTENTION),
    DeliveryStatus.ABANDONED: ("gave up", Tone.NEGATIVE),
    DeliveryStatus.NEEDS_INPUT: ("has a question", Tone.ATTENTION),
}
STATUS_OUTCOMES: Final[dict[TaskStatus, tuple[str, Tone]]] = {
    TaskStatus.CANDIDATE: ("waiting", Tone.MUTED),
    TaskStatus.INELIGIBLE: ("filtered out", Tone.MUTED),
    TaskStatus.ASSESSED: ("assessed", Tone.MUTED),
    TaskStatus.APPROVED: ("to do", Tone.ACTIVE),
    TaskStatus.WORKING: ("working", Tone.ACTIVE),
    TaskStatus.REVIEW: ("ready for review", Tone.POSITIVE),
    TaskStatus.NEEDS_INPUT: ("needs input", Tone.ATTENTION),
    TaskStatus.UNFINISHED: ("unfinished", Tone.ATTENTION),
    TaskStatus.ABANDONED: ("abandoned", Tone.NEGATIVE),
    TaskStatus.SKIPPED: ("skipped", Tone.MUTED),
}
ASSESSED_OUTCOMES: Final[dict[Verdict, tuple[str, Tone]]] = {
    Verdict.EXECUTE: ("to do", Tone.ACTIVE),
    Verdict.PROPOSE: ("to propose", Tone.ACTIVE),
    Verdict.NEEDS_INPUT: ("needs input", Tone.ATTENTION),
    Verdict.SKIP: ("skipped", Tone.MUTED),
}
INELIGIBILITY_PHRASES: Final[dict[IneligibilityReason, str]] = {
    IneligibilityReason.ASSIGNED_TO_SOMEONE_ELSE: "assigned to someone else",
    IneligibilityReason.NEVER_LABEL: "marked weekend:never",
    IneligibilityReason.BODY_TOO_SHORT: "description too short",
    IneligibilityReason.TITLE_PATTERN: "excluded by its title",
    IneligibilityReason.OPEN_LINKED_PULL_REQUEST: "a pull request is already open",
    IneligibilityReason.RECENT_FOREIGN_ACTIVITY: "someone else is active on it",
}
SKIP_PHRASES: Final[dict[str, str]] = {
    OUTSIDE_LIMITS_REASON: "outside the agent's limits",
    PROPOSAL_REASON: "waits for approval",
    CLOSED_ISSUE_REASON: "the issue was closed",
    REVIEWER_SKIP_REASON: "skipped by the reviewer",
    DEADLINE_REASON: "the weekend is over",
    ENVELOPE_REASON: "the budget is spent",
    CONSENT_REASON_TEMPLATE.format(consent=Consent.NEEDS_APPROVAL.value): (
        "waits for weekend:approved"
    ),
    CONSENT_REASON_TEMPLATE.format(consent=Consent.NEVER.value): "marked weekend:never",
}


def plain_outcome(detail: str) -> Outcome:
    return outcome(detail, Tone.PLAIN, None)


def cautionary_outcome(detail: str) -> Outcome:
    return outcome(detail, Tone.ATTENTION, None)


def stopping_outcome(detail: str) -> Outcome:
    return outcome(detail, Tone.NEGATIVE, None)


def silent_outcome(detail: str) -> Outcome:
    return outcome("", Tone.PLAIN, None)


def labelled(pair: tuple[str, Tone], detail: str | None) -> Outcome:
    text, tone = pair
    return outcome(text, tone, detail)


def assessment_outcome(detail: str) -> Outcome:
    match = ASSESSMENT_PATTERN.match(detail)
    if match is None or match["verdict"] not in Verdict:
        return plain_outcome(detail)
    size = DETAIL_SEPARATOR.join((match["effort"], match["risk"]))
    return labelled(VERDICT_OUTCOMES[Verdict(match["verdict"])], size)


def prefilter_outcome(detail: str) -> Outcome:
    match = PREFILTER_PATTERN.match(detail)
    if match is None:
        return plain_outcome(detail)
    return plain_outcome(f"{match['eligible']} of {match['total']} issues eligible")


def triage_outcome(detail: str) -> Outcome:
    match = TRIAGE_PATTERN.match(detail)
    if match is None:
        return plain_outcome(detail)
    return plain_outcome(f"{match['count']} issues reviewed")


def worker_outcome(detail: str) -> Outcome:
    match = WORKER_PATTERN.match(detail)
    if match is None or match["status"] not in DeliveryStatus:
        return plain_outcome(detail)
    return labelled(DELIVERY_OUTCOMES[DeliveryStatus(match["status"])], None)


def gate_outcome(detail: str) -> Outcome:
    match = GATE_PATTERN.match(detail)
    if match is None:
        return plain_outcome(detail)
    tone = Tone.POSITIVE if match["verdict"] == GATE_PASSED else Tone.NEGATIVE
    return outcome(match["verdict"], tone, f"{match['lines']} lines changed")


def acceptance_outcome(detail: str) -> Outcome:
    match = ACCEPTANCE_PATTERN.match(detail)
    if match is None:
        return plain_outcome(detail)
    if int(match["code"]) == ACCEPTANCE_PASSED_EXIT:
        return outcome("passed", Tone.POSITIVE, None)
    return outcome("failed", Tone.NEGATIVE, None)


def task_finished_outcome(detail: str) -> Outcome:
    match = TASK_FINISHED_PATTERN.match(detail)
    if match is None or match["status"] not in TaskStatus:
        return plain_outcome(detail)
    return labelled(STATUS_OUTCOMES[TaskStatus(match["status"])], None)


def branch_outcome(detail: str) -> Outcome:
    return plain_outcome(detail.removeprefix(BRANCH_PREFIX))


def pull_request_outcome(detail: str) -> Outcome:
    match = PULL_REQUEST_PATTERN.search(detail)
    if match is None:
        return outcome("opened", Tone.POSITIVE, None)
    return outcome(f"PR #{match['number']}", Tone.POSITIVE, None)


def comment_outcome(detail: str) -> Outcome:
    if detail == QUESTIONS_COMMENT:
        return outcome("questions", Tone.ATTENTION, None)
    if detail == REVIEW_COMMENT:
        return plain_outcome("link to the pull request")
    return plain_outcome(detail)


def skip_outcome(detail: str) -> Outcome:
    return plain_outcome(SKIP_PHRASES.get(detail, detail))


def started_outcome(detail: str, run_id: str) -> Outcome:
    if detail == ADOPTED_NOTE_TEMPLATE.format(run_id=run_id):
        return plain_outcome(ADOPTED_TEXT)
    return silent_outcome(detail)


OUTCOME_READERS: Final[dict[EventType, Callable[[str], Outcome]]] = {
    EventType.PREFILTER_FINISHED: prefilter_outcome,
    EventType.ASSESSMENT_FINISHED: assessment_outcome,
    EventType.TASK_STARTED: branch_outcome,
    EventType.TASK_SKIPPED: skip_outcome,
    EventType.OVERLAP_FOUND: cautionary_outcome,
    EventType.WORKER_FINISHED: worker_outcome,
    EventType.GATE_FINISHED: gate_outcome,
    EventType.ACCEPTANCE_FINISHED: acceptance_outcome,
    EventType.TASK_FINISHED: task_finished_outcome,
    EventType.BRANCH_PUSHED: branch_outcome,
    EventType.PULL_REQUEST_OPENED: pull_request_outcome,
    EventType.COMMENT_POSTED: comment_outcome,
    EventType.DIGEST_POSTED: silent_outcome,
    EventType.BUDGET_EXHAUSTED: cautionary_outcome,
    EventType.LIMIT_REACHED: cautionary_outcome,
    EventType.USAGE_PARKED: cautionary_outcome,
    EventType.RUN_FINISHED: triage_outcome,
    EventType.RUN_ABORTED: stopping_outcome,
}


def step_outcome(event: RunEvent, run_id: str) -> Outcome:
    if event.event is EventType.RUN_STARTED:
        return started_outcome(event.detail, run_id)
    reader = OUTCOME_READERS.get(event.event, plain_outcome)
    return reader(event.detail)


def local_clock(moment: datetime, zone: ZoneInfo) -> str:
    return moment.astimezone(zone).strftime(CLOCK_FORMAT)


def demo_step(event: RunEvent, run_id: str, zone: ZoneInfo) -> DemoStep:
    return DemoStep(
        time=local_clock(event.at, zone),
        issue_number=event.issue_number,
        step=STEP_LABELS[event.event],
        outcome=step_outcome(event, run_id),
    )


def ineligibility_phrase(task: Task) -> str | None:
    if task.eligibility is None or not task.eligibility.reasons:
        return None
    return REASON_SEPARATOR.join(
        INELIGIBILITY_PHRASES[reason] for reason in task.eligibility.reasons
    )


def task_outcome(task: Task) -> Outcome:
    if task.status is TaskStatus.INELIGIBLE:
        return labelled(STATUS_OUTCOMES[task.status], ineligibility_phrase(task))
    if task.status is TaskStatus.ASSESSED and task.assessment is not None:
        return labelled(ASSESSED_OUTCOMES[task.assessment.verdict], None)
    return labelled(STATUS_OUTCOMES[task.status], None)


def demo_task(task: Task) -> DemoTask:
    return DemoTask(
        issue_number=task.issue_number,
        title=task.title,
        outcome=task_outcome(task),
        link=browsable(task.pull_request_url),
    )


def build_demo_view(
    state: RunState, status: RunStatus, events: list[RunEvent], zone: ZoneInfo
) -> DemoView:
    return DemoView(
        run_id=state.run_id,
        liveness=status.liveness,
        activity=current_phrase(status) if status.liveness is Liveness.ALIVE else None,
        steps=[demo_step(event, state.run_id, zone) for event in events],
        tasks=[demo_task(task) for task in sorted(state.tasks, key=lambda task: task.issue_number)],
    )
