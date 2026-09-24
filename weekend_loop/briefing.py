from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict

from weekend_loop.models import (
    BriefingAnswer,
    Inbox,
    InboxAnswer,
    IssueNote,
    IssueNotes,
    PreparedSession,
    Task,
)
from weekend_loop.records import read_record, write_record

BRIEFING_DIRECTORY_NAME: Final[str] = "briefing"
PREPARED_DIRECTORY_NAME: Final[str] = "prepared"
ANSWER_TEMPLATE: Final[str] = "answer-{issue_number}-{question_key}.json"
NOTES_TEMPLATE: Final[str] = "notes-{issue_number}.json"
PREPARED_TEMPLATE: Final[str] = "{repo_key}.json"
ANSWER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^answer-(\d+)-([0-9a-f]+)\.json$")
NOTES_PATTERN: Final[re.Pattern[str]] = re.compile(r"^notes-(\d+)\.json$")
WHITESPACE_PATTERN: Final[re.Pattern[str]] = re.compile(r"\s+")
TRAILING_PUNCTUATION: Final[str] = " .,:;?!"
QUESTION_KEY_LENGTH: Final[int] = 12
PREPARED_SESSION_MAX_AGE_HOURS: Final[int] = 72
ANSWER_LINE_TEMPLATE: Final[str] = "{question} — {text}"
CONTEXT_LINE_TEMPLATE: Final[str] = "Standing answer about #{issue_number}: {question} — {text}"
NOTE_LINE_TEMPLATE: Final[str] = "Standing note: {text}"
NO_BRIEFING_TEXT: Final[str] = "The operator has left no standing guidance on this issue."


class RepoBriefing(BaseModel):
    model_config = ConfigDict(frozen=True)

    repo_key: str
    answers: list[BriefingAnswer]
    notes: list[IssueNotes]


def question_key(question: str) -> str:
    collapsed = WHITESPACE_PATTERN.sub(" ", question).strip().lower()
    normalised = collapsed.rstrip(TRAILING_PUNCTUATION)
    return hashlib.sha256(normalised.encode()).hexdigest()[:QUESTION_KEY_LENGTH]


def briefing_directory(state_directory: Path, repo_key: str) -> Path:
    return state_directory / BRIEFING_DIRECTORY_NAME / repo_key


def prepared_path(state_directory: Path, repo_key: str) -> Path:
    return state_directory / PREPARED_DIRECTORY_NAME / PREPARED_TEMPLATE.format(repo_key=repo_key)


def empty_briefing(repo_key: str) -> RepoBriefing:
    return RepoBriefing(repo_key=repo_key, answers=[], notes=[])


def read_briefing(state_directory: Path, repo_key: str) -> RepoBriefing:
    directory = briefing_directory(state_directory, repo_key)
    if not directory.is_dir():
        return empty_briefing(repo_key)
    answers: list[BriefingAnswer] = []
    notes: list[IssueNotes] = []
    for path in sorted(directory.iterdir()):
        if ANSWER_PATTERN.match(path.name) is not None:
            answers.append(read_record(BriefingAnswer, path))
        elif NOTES_PATTERN.match(path.name) is not None:
            notes.append(read_record(IssueNotes, path))
    return RepoBriefing(
        repo_key=repo_key,
        answers=sorted(answers, key=lambda answer: (answer.issue_number, answer.question_key)),
        notes=sorted(notes, key=lambda entry: entry.issue_number),
    )


def record_answer(
    state_directory: Path, repo_key: str, issue_number: int, question: str, text: str
) -> Path:
    key = question_key(question)
    answer = BriefingAnswer(
        issue_number=issue_number,
        question=question,
        question_key=key,
        text=text,
        written_at=datetime.now(UTC),
    )
    path = briefing_directory(state_directory, repo_key) / ANSWER_TEMPLATE.format(
        issue_number=issue_number, question_key=key
    )
    write_record(answer, path)
    return path


def notes_path(state_directory: Path, repo_key: str, issue_number: int) -> Path:
    return briefing_directory(state_directory, repo_key) / NOTES_TEMPLATE.format(
        issue_number=issue_number
    )


def read_notes(state_directory: Path, repo_key: str, issue_number: int) -> IssueNotes | None:
    path = notes_path(state_directory, repo_key, issue_number)
    return read_record(IssueNotes, path) if path.is_file() else None


def append_note(state_directory: Path, repo_key: str, issue_number: int, text: str) -> Path:
    existing = read_notes(state_directory, repo_key, issue_number)
    kept = existing.notes if existing is not None else []
    updated = IssueNotes(
        issue_number=issue_number,
        notes=[*kept, IssueNote(text=text, written_at=datetime.now(UTC))],
    )
    path = notes_path(state_directory, repo_key, issue_number)
    write_record(updated, path)
    return path


def notes_of(briefing: RepoBriefing, issue_number: int) -> list[IssueNote]:
    for entry in briefing.notes:
        if entry.issue_number == issue_number:
            return entry.notes
    return []


def answers_of(briefing: RepoBriefing, issue_number: int) -> dict[str, BriefingAnswer]:
    return {
        answer.question_key: answer
        for answer in briefing.answers
        if answer.issue_number == issue_number
    }


def questions_of(task: Task) -> list[str]:
    if task.delivery is not None and task.delivery.questions:
        return task.delivery.questions
    return task.assessment.questions if task.assessment is not None else []


def live_answers_of(inbox: Inbox, issue_number: int) -> dict[int, InboxAnswer]:
    return {
        answer.question_index: answer
        for answer in inbox.answers
        if answer.issue_number == issue_number
    }


def questions_answered(briefing: RepoBriefing, task: Task) -> bool:
    questions = questions_of(task)
    if not questions:
        return False
    standing = answers_of(briefing, task.issue_number)
    return all(question_key(question) in standing for question in questions)


def guidance_for(inbox: Inbox, briefing: RepoBriefing, task: Task) -> list[str]:
    live = live_answers_of(inbox, task.issue_number)
    standing = answers_of(briefing, task.issue_number)
    lines: list[str] = []
    used_indices: set[int] = set()
    used_keys: set[str] = set()
    for index, question in enumerate(questions_of(task)):
        key = question_key(question)
        answered = live.get(index)
        if answered is not None:
            used_indices.add(index)
            lines.append(ANSWER_LINE_TEMPLATE.format(question=question, text=answered.text))
            used_keys.add(key)
        elif key in standing:
            used_keys.add(key)
            lines.append(ANSWER_LINE_TEMPLATE.format(question=question, text=standing[key].text))
    for index, answer in sorted(live.items()):
        if index not in used_indices:
            lines.append(ANSWER_LINE_TEMPLATE.format(question=answer.question, text=answer.text))
            used_keys.add(question_key(answer.question))
    for key, standing_answer in standing.items():
        if key not in used_keys:
            lines.append(
                CONTEXT_LINE_TEMPLATE.format(
                    issue_number=standing_answer.issue_number,
                    question=standing_answer.question,
                    text=standing_answer.text,
                )
            )
    lines.extend(
        NOTE_LINE_TEMPLATE.format(text=note.text) for note in notes_of(briefing, task.issue_number)
    )
    return lines


def render_briefing_block(briefing: RepoBriefing, issue_number: int) -> str:
    lines = [
        ANSWER_LINE_TEMPLATE.format(question=answer.question, text=answer.text)
        for answer in briefing.answers
        if answer.issue_number == issue_number
    ]
    lines.extend(note.text for note in notes_of(briefing, issue_number))
    return "\n".join(f"- {line}" for line in lines) if lines else NO_BRIEFING_TEXT


def write_prepared(state_directory: Path, prepared: PreparedSession) -> Path:
    path = prepared_path(state_directory, prepared.repo_key)
    write_record(prepared, path)
    return path


def read_prepared(state_directory: Path, repo_key: str) -> PreparedSession | None:
    path = prepared_path(state_directory, repo_key)
    return read_record(PreparedSession, path) if path.is_file() else None


def clear_prepared(state_directory: Path, repo_key: str) -> None:
    prepared_path(state_directory, repo_key).unlink(missing_ok=True)


def prepared_is_fresh(prepared: PreparedSession, now: datetime) -> bool:
    return now - prepared.prepared_at < timedelta(hours=PREPARED_SESSION_MAX_AGE_HOURS)
