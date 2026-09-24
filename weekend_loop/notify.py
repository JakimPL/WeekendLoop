from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Final

from weekend_loop.models import RunState, ScheduledCommand, Task, TaskStatus
from weekend_loop.systemd_units import (
    EXITED_CODE,
    RESTART_DELAY_SECONDS,
    SIGNAL_CODES,
    restarts_after_failure,
    service_name,
    unit_name,
)

SLACK_HOST: Final[str] = "hooks.slack.com"
SLACK_PAYLOAD_KEY: Final[str] = "text"
WEBHOOK_PAYLOAD_KEY: Final[str] = "content"
ALERT_TIMEOUT_SECONDS: Final[float] = 10.0
CONTENT_TYPE: Final[str] = "application/json"
NO_QUESTIONS_TEXT: Final[str] = "No question is open."
ANSWER_HINT_TEXT: Final[str] = "Answer in a comment on each issue; the next run reads the replies."
REVIEW_STATUSES: Final[tuple[TaskStatus, ...]] = (TaskStatus.REVIEW,)
UNFINISHED_STATUSES: Final[tuple[TaskStatus, ...]] = (TaskStatus.UNFINISHED,)
QUESTION_STATUSES: Final[tuple[TaskStatus, ...]] = (TaskStatus.NEEDS_INPUT,)
EXIT_RESTART_TEXT: Final[str] = "systemd restarts it in {seconds}s and the run resumes from disk."
EXIT_STAYS_DOWN_TEXT: Final[str] = (
    "it waits for its next timer or for `systemctl --user start {service}`."
)
EXIT_LOG_TEXT: Final[str] = "Log: journalctl --user -u {unit}"


def read_webhook_url(path: Path) -> str | None:
    if not path.is_file():
        return None
    url = path.read_text().strip()
    return url if url else None


def payload_key(webhook_url: str) -> str:
    host = urllib.parse.urlparse(webhook_url).hostname
    return SLACK_PAYLOAD_KEY if host == SLACK_HOST else WEBHOOK_PAYLOAD_KEY


def questions_of(task: Task) -> list[str]:
    if task.delivery is not None and task.delivery.questions:
        return task.delivery.questions
    return task.assessment.questions if task.assessment is not None else []


def question_lines(state: RunState) -> list[str]:
    lines: list[str] = []
    for task in state.tasks:
        for question in questions_of(task):
            lines.append(f"• #{task.issue_number} {task.title} — {question}")
    return lines


def render_question_alert(state: RunState) -> str:
    lines = question_lines(state)
    headline = (
        f"Weekend Loop has {len(lines)} question(s) before the next session "
        f"({state.repo_key}, run {state.run_id})."
    )
    body = "\n".join(lines) if lines else NO_QUESTIONS_TEXT
    return "\n".join([headline, "", body, "", ANSWER_HINT_TEXT])


def tasks_with_status(state: RunState, statuses: tuple[TaskStatus, ...]) -> list[Task]:
    return [task for task in state.tasks if task.status in statuses]


def render_finish_alert(state: RunState) -> str:
    delivered = tasks_with_status(state, REVIEW_STATUSES)
    unfinished = tasks_with_status(state, UNFINISHED_STATUSES)
    waiting = tasks_with_status(state, QUESTION_STATUSES)
    headline = (
        f"Weekend Loop run {state.run_id} finished in phase {state.phase.value}: "
        f"{len(delivered)} ready for review, {len(unfinished)} unfinished, "
        f"{len(waiting)} waiting on an answer, "
        f"${state.spent_usd:.2f} of ${state.envelope_usd:.2f} spent."
    )
    links = [
        f"• #{task.issue_number} {task.pull_request_url}"
        for task in delivered
        if task.pull_request_url is not None
    ]
    return "\n".join([headline, *links, *state.notes])


def process_ending(exit_code: str, exit_status: str) -> str:
    if exit_code == EXITED_CODE:
        return f"exited with status {exit_status}"
    if exit_code in SIGNAL_CODES:
        return f"ended with signal {exit_status}"
    return "ended"


def exit_follow_up(unit: ScheduledCommand) -> str:
    if restarts_after_failure(unit):
        return EXIT_RESTART_TEXT.format(seconds=RESTART_DELAY_SECONDS)
    return EXIT_STAYS_DOWN_TEXT.format(service=service_name(unit))


def render_exit_alert(
    unit: ScheduledCommand, service_result: str, exit_code: str, exit_status: str
) -> str:
    headline = (
        f"Weekend Loop {unit.value} run {process_ending(exit_code, exit_status)} "
        f"({service_result}); {exit_follow_up(unit)}"
    )
    return "\n".join([headline, EXIT_LOG_TEXT.format(unit=unit_name(unit))])


def post_webhook(webhook_url: str, payload: dict[str, str]) -> int:
    request = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": CONTENT_TYPE},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=ALERT_TIMEOUT_SECONDS) as response:
        return int(response.status)


def send_alert(webhook: Path, text: str) -> bool:
    webhook_url = read_webhook_url(webhook)
    if webhook_url is None:
        return False
    try:
        post_webhook(webhook_url, {payload_key(webhook_url): text})
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        print(f"alert not delivered: {error}")
        return False
    return True
