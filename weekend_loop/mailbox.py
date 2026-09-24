from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from weekend_loop.models import Inbox, InboxAnswer, InboxMessage
from weekend_loop.records import write_record

STOP_FILENAME: Final[str] = "stop.json"
PAUSE_FILENAME: Final[str] = "pause.json"
APPROVE_TEMPLATE: Final[str] = "approve-{issue_number}.json"
SKIP_TEMPLATE: Final[str] = "skip-{issue_number}.json"
ANSWER_TEMPLATE: Final[str] = "answer-{issue_number}-{question_index}.json"
MESSAGE_TEMPLATE: Final[str] = "message-{stamp}.json"
MESSAGE_STAMP_FORMAT: Final[str] = "%Y%m%d-%H%M%S-%f"
APPROVE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^approve-(\d+)\.json$")
SKIP_PATTERN: Final[re.Pattern[str]] = re.compile(r"^skip-(\d+)\.json$")
ANSWER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^answer-(\d+)-(\d+)\.json$")
MESSAGE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^message-.+\.json$")


def write_message(inbox: Path, filename: str, message: InboxMessage) -> Path:
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / filename
    write_record(message, path)
    return path


def new_message(text: str) -> InboxMessage:
    return InboxMessage(text=text, written_at=datetime.now(UTC))


def request_stop(inbox: Path, reason: str) -> Path:
    return write_message(inbox, STOP_FILENAME, new_message(reason))


def request_pause(inbox: Path, reason: str) -> Path:
    return write_message(inbox, PAUSE_FILENAME, new_message(reason))


def clear_request(inbox: Path, filename: str) -> None:
    (inbox / filename).unlink(missing_ok=True)


def approve_issue(inbox: Path, issue_number: int, reason: str) -> Path:
    return write_message(
        inbox, APPROVE_TEMPLATE.format(issue_number=issue_number), new_message(reason)
    )


def skip_issue(inbox: Path, issue_number: int, reason: str) -> Path:
    return write_message(
        inbox, SKIP_TEMPLATE.format(issue_number=issue_number), new_message(reason)
    )


def answer_question(
    inbox: Path, issue_number: int, question_index: int, question: str, text: str
) -> Path:
    answer = InboxAnswer(
        issue_number=issue_number,
        question_index=question_index,
        question=question,
        text=text,
        written_at=datetime.now(UTC),
    )
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / ANSWER_TEMPLATE.format(issue_number=issue_number, question_index=question_index)
    write_record(answer, path)
    return path


def post_message(inbox: Path, text: str) -> Path:
    stamp = datetime.now(UTC).strftime(MESSAGE_STAMP_FORMAT)
    return write_message(inbox, MESSAGE_TEMPLATE.format(stamp=stamp), new_message(text))


def read_inbox(inbox: Path) -> Inbox:
    if not inbox.is_dir():
        return Inbox(stop=False, pause=False, approvals=[], skips=[], answers=[], messages=[])
    approvals: list[int] = []
    skips: list[int] = []
    answers: list[InboxAnswer] = []
    messages: list[str] = []
    for path in sorted(inbox.iterdir()):
        name = path.name
        approve = APPROVE_PATTERN.match(name)
        skip = SKIP_PATTERN.match(name)
        answer = ANSWER_PATTERN.match(name)
        if approve is not None:
            approvals.append(int(approve.group(1)))
        elif skip is not None:
            skips.append(int(skip.group(1)))
        elif answer is not None:
            answers.append(InboxAnswer.model_validate_json(path.read_text()))
        elif MESSAGE_PATTERN.match(name) is not None:
            messages.append(str(json.loads(path.read_text())["text"]))
    return Inbox(
        stop=(inbox / STOP_FILENAME).is_file(),
        pause=(inbox / PAUSE_FILENAME).is_file(),
        approvals=sorted(approvals),
        skips=sorted(skips),
        answers=sorted(answers, key=lambda entry: (entry.issue_number, entry.question_index)),
        messages=messages,
    )


def answers_for(inbox: Inbox, issue_number: int) -> list[str]:
    return [
        f"{answer.question} — {answer.text}"
        for answer in inbox.answers
        if answer.issue_number == issue_number
    ]


def stop_requested(inbox_directory: Path) -> bool:
    return (inbox_directory / STOP_FILENAME).is_file()


def pause_requested(inbox_directory: Path) -> bool:
    return (inbox_directory / PAUSE_FILENAME).is_file()
