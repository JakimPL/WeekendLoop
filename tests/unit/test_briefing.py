from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from tests.unit.conftest import ELIGIBLE, build_assessment, build_task
from weekend_loop.briefing import (
    PREPARED_SESSION_MAX_AGE_HOURS,
    append_note,
    clear_prepared,
    empty_briefing,
    guidance_for,
    prepared_is_fresh,
    question_key,
    questions_answered,
    read_briefing,
    read_prepared,
    record_answer,
    render_briefing_block,
    write_prepared,
)
from weekend_loop.mailbox import answer_question, read_inbox, request_stop
from weekend_loop.models import (
    Blocker,
    Effort,
    Inbox,
    PreparedSession,
    Risk,
    Task,
    TaskStatus,
    Verdict,
)

REPO_KEY: Final[str] = "demo"
ISSUE_NUMBER: Final[int] = 7
QUESTION: Final[str] = "Which unit does the feed use?"
OTHER_QUESTION: Final[str] = "Should the parser round or truncate?"
EMPTY_INBOX: Final[Inbox] = Inbox(
    stop=False, pause=False, approvals=[], skips=[], answers=[], messages=[]
)


def build_asking_task(questions: list[str]) -> Task:
    return build_task(
        ISSUE_NUMBER,
        "Parse the log timestamps",
        TaskStatus.ASSESSED,
        ELIGIBLE,
        build_assessment(
            Verdict.NEEDS_INPUT, Effort.S, Risk.TESTS, [Blocker.UNCLEAR_GOAL], questions
        ),
    )


def test_a_repository_nobody_has_briefed_reads_as_an_empty_one(tmp_path: Path) -> None:
    briefing = read_briefing(tmp_path, REPO_KEY)

    assert briefing == empty_briefing(REPO_KEY)


def test_a_question_keeps_its_key_through_case_spacing_and_trailing_punctuation() -> None:
    assert question_key(QUESTION) == question_key("  which UNIT does the   feed use  ")
    assert question_key(QUESTION) == question_key("Which unit does the feed use")
    assert question_key(QUESTION) != question_key(OTHER_QUESTION)


def test_an_answer_written_before_any_run_reaches_the_task_that_asks_it(tmp_path: Path) -> None:
    record_answer(tmp_path, REPO_KEY, ISSUE_NUMBER, QUESTION, "Knots.")

    lines = guidance_for(
        EMPTY_INBOX, read_briefing(tmp_path, REPO_KEY), build_asking_task([QUESTION])
    )

    assert lines == [f"{QUESTION} — Knots."]


def test_an_answer_written_during_the_run_wins_over_the_standing_one(tmp_path: Path) -> None:
    record_answer(tmp_path, REPO_KEY, ISSUE_NUMBER, QUESTION, "Knots.")
    inbox_directory = tmp_path / "inbox"
    answer_question(inbox_directory, ISSUE_NUMBER, 0, QUESTION, "Metres per second.")

    lines = guidance_for(
        read_inbox(inbox_directory),
        read_briefing(tmp_path, REPO_KEY),
        build_asking_task([QUESTION]),
    )

    assert lines == [f"{QUESTION} — Metres per second."]


def test_a_standing_answer_to_a_question_no_longer_asked_is_carried_as_context(
    tmp_path: Path,
) -> None:
    record_answer(tmp_path, REPO_KEY, ISSUE_NUMBER, QUESTION, "Knots.")

    lines = guidance_for(
        EMPTY_INBOX, read_briefing(tmp_path, REPO_KEY), build_asking_task([OTHER_QUESTION])
    )

    assert lines == [f"Standing answer about #{ISSUE_NUMBER}: {QUESTION} — Knots."]


def test_standing_notes_reach_every_attempt_on_that_issue(tmp_path: Path) -> None:
    append_note(tmp_path, REPO_KEY, ISSUE_NUMBER, "Reuse the helper in records.py.")
    append_note(tmp_path, REPO_KEY, ISSUE_NUMBER, "Leave the CLI alone.")

    lines = guidance_for(EMPTY_INBOX, read_briefing(tmp_path, REPO_KEY), build_asking_task([]))

    assert lines == [
        "Standing note: Reuse the helper in records.py.",
        "Standing note: Leave the CLI alone.",
    ]


def test_answering_the_same_question_twice_keeps_only_the_later_answer(tmp_path: Path) -> None:
    record_answer(tmp_path, REPO_KEY, ISSUE_NUMBER, QUESTION, "Knots.")
    record_answer(tmp_path, REPO_KEY, ISSUE_NUMBER, QUESTION, "Metres per second.")

    briefing = read_briefing(tmp_path, REPO_KEY)

    assert [answer.text for answer in briefing.answers] == ["Metres per second."]


def test_a_task_counts_as_answered_only_once_every_question_has_one(tmp_path: Path) -> None:
    record_answer(tmp_path, REPO_KEY, ISSUE_NUMBER, QUESTION, "Knots.")
    briefing = read_briefing(tmp_path, REPO_KEY)

    assert questions_answered(briefing, build_asking_task([QUESTION]))
    assert not questions_answered(briefing, build_asking_task([QUESTION, OTHER_QUESTION]))
    assert not questions_answered(briefing, build_asking_task([]))


def test_the_briefing_ignores_files_that_belong_to_the_run_mailbox(tmp_path: Path) -> None:
    directory = tmp_path / "briefing" / REPO_KEY
    directory.mkdir(parents=True)
    request_stop(directory, "panel")
    record_answer(tmp_path, REPO_KEY, ISSUE_NUMBER, QUESTION, "Knots.")

    briefing = read_briefing(tmp_path, REPO_KEY)

    assert len(briefing.answers) == 1
    assert briefing.notes == []


def test_the_assessor_block_spells_out_the_answers_and_the_notes(tmp_path: Path) -> None:
    record_answer(tmp_path, REPO_KEY, ISSUE_NUMBER, QUESTION, "Knots.")
    append_note(tmp_path, REPO_KEY, ISSUE_NUMBER, "Leave the CLI alone.")

    block = render_briefing_block(read_briefing(tmp_path, REPO_KEY), ISSUE_NUMBER)

    assert f"- {QUESTION} — Knots." in block
    assert "- Leave the CLI alone." in block


def test_an_issue_nobody_briefed_renders_as_such(tmp_path: Path) -> None:
    block = render_briefing_block(read_briefing(tmp_path, REPO_KEY), ISSUE_NUMBER)

    assert "no standing guidance" in block


def test_a_prepared_session_round_trips_and_can_be_cleared(tmp_path: Path) -> None:
    prepared = PreparedSession(
        run_id="20260917-200000-demo",
        repo_key=REPO_KEY,
        prepared_at=datetime.now(UTC),
        question_count=2,
    )
    write_prepared(tmp_path, prepared)

    assert read_prepared(tmp_path, REPO_KEY) == prepared

    clear_prepared(tmp_path, REPO_KEY)

    assert read_prepared(tmp_path, REPO_KEY) is None


def test_a_prepared_session_stops_being_taken_up_once_it_is_old() -> None:
    now = datetime.now(UTC)
    prepared = PreparedSession(
        run_id="20260917-200000-demo",
        repo_key=REPO_KEY,
        prepared_at=now - timedelta(hours=PREPARED_SESSION_MAX_AGE_HOURS + 1),
        question_count=2,
    )

    assert not prepared_is_fresh(prepared, now)
    assert prepared_is_fresh(prepared.model_copy(update={"prepared_at": now}), now)
