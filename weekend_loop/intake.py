from __future__ import annotations

from pathlib import Path
from typing import Final

from weekend_loop.backends import BoardReader, board_operator_login
from weekend_loop.briefing import (
    RepoBriefing,
    answers_of,
    append_note,
    notes_of,
    question_key,
    read_briefing,
    record_answer,
)
from weekend_loop.models import EventType, IdentityPolicy, Intake, Issue, IssueIntake, ParsedReply
from weekend_loop.questions import parse_reply, question_thread
from weekend_loop.runs import RunDirectory, append_event

INTAKE_LINE_TEMPLATE: Final[str] = (
    "#{issue_number}: {replies} reply(ies), {answers} new answer(s), {notes} new note(s)"
)
INTAKE_EVENT_TEMPLATE: Final[str] = (
    "{replies} reply(ies) on the issue, {answers} new answer(s), {notes} new note(s)"
)
NO_REPLIES_TEXT: Final[str] = "No operator reply on the issues waiting for an answer."


def waiting_issue_numbers(issues: list[Issue], needs_input_label: str) -> list[int]:
    return [issue.number for issue in issues if needs_input_label in issue.labels]


def answer_is_new(briefing: RepoBriefing, issue_number: int, question: str, text: str) -> bool:
    standing = answers_of(briefing, issue_number).get(question_key(question))
    return standing is None or standing.text != text


def note_is_new(briefing: RepoBriefing, issue_number: int, text: str) -> bool:
    return all(note.text != text for note in notes_of(briefing, issue_number))


def file_reply(
    state_directory: Path, repo_key: str, issue_number: int, reply: ParsedReply
) -> tuple[int, int]:
    briefing = read_briefing(state_directory, repo_key)
    new_answers = [
        answer
        for answer in reply.answers
        if answer_is_new(briefing, issue_number, answer.question, answer.text)
    ]
    for answer in new_answers:
        record_answer(state_directory, repo_key, issue_number, answer.question, answer.text)
    if reply.note is None or not note_is_new(briefing, issue_number, reply.note):
        return len(new_answers), 0
    append_note(state_directory, repo_key, issue_number, reply.note)
    return len(new_answers), 1


def ingest_issue(
    state_directory: Path,
    repo_key: str,
    reader: BoardReader,
    operator_login: str,
    issue_number: int,
) -> IssueIntake | None:
    thread = question_thread(reader.issue_comments(issue_number), operator_login)
    if thread is None or not thread.replies:
        return None
    answers = 0
    notes = 0
    for body in thread.replies:
        filed_answers, filed_notes = file_reply(
            state_directory, repo_key, issue_number, parse_reply(body, thread.questions)
        )
        answers += filed_answers
        notes += filed_notes
    return IssueIntake(
        issue_number=issue_number, replies=len(thread.replies), answers=answers, notes=notes
    )


def ingest_answers(
    state_directory: Path,
    repo_key: str,
    reader: BoardReader,
    identity: IdentityPolicy,
    needs_input_label: str,
    limit: int,
) -> Intake:
    operator_login = board_operator_login(identity, reader)
    waiting = waiting_issue_numbers(reader.open_issues(limit), needs_input_label)
    found = [
        ingest_issue(state_directory, repo_key, reader, operator_login, issue_number)
        for issue_number in waiting
    ]
    return Intake(issues=[entry for entry in found if entry is not None])


def replied_issues(intake: Intake) -> set[int]:
    return {entry.issue_number for entry in intake.issues}


def render_intake(intake: Intake) -> str:
    if not intake.issues:
        return NO_REPLIES_TEXT
    return "\n".join(
        INTAKE_LINE_TEMPLATE.format(
            issue_number=entry.issue_number,
            replies=entry.replies,
            answers=entry.answers,
            notes=entry.notes,
        )
        for entry in intake.issues
    )


def record_intake(run_directory: RunDirectory, intake: Intake) -> None:
    for entry in intake.issues:
        append_event(
            run_directory,
            EventType.ANSWER_RECEIVED,
            INTAKE_EVENT_TEMPLATE.format(
                replies=entry.replies, answers=entry.answers, notes=entry.notes
            ),
            entry.issue_number,
        )
