from __future__ import annotations

from pathlib import Path

from weekend_loop.mailbox import (
    PAUSE_FILENAME,
    STOP_FILENAME,
    answer_question,
    answers_for,
    approve_issue,
    clear_request,
    post_message,
    read_inbox,
    request_pause,
    request_stop,
    skip_issue,
    stop_requested,
)


def test_an_absent_mailbox_reads_as_an_empty_one(tmp_path: Path) -> None:
    inbox = read_inbox(tmp_path / "inbox")
    assert not inbox.stop
    assert not inbox.pause
    assert inbox.approvals == []
    assert inbox.answers == []
    assert not stop_requested(tmp_path / "inbox")


def test_every_request_the_panel_writes_is_read_back(tmp_path: Path) -> None:
    inbox_directory = tmp_path / "inbox"
    request_stop(inbox_directory, "the reviewer is going to bed")
    request_pause(inbox_directory, "later")
    approve_issue(inbox_directory, 12, "panel")
    approve_issue(inbox_directory, 3, "panel")
    skip_issue(inbox_directory, 7, "panel")
    post_message(inbox_directory, "the S3 bucket moved")
    inbox = read_inbox(inbox_directory)
    assert inbox.stop
    assert inbox.pause
    assert inbox.approvals == [3, 12]
    assert inbox.skips == [7]
    assert inbox.messages == ["the S3 bucket moved"]
    assert stop_requested(inbox_directory)


def test_clearing_a_request_lifts_it(tmp_path: Path) -> None:
    inbox_directory = tmp_path / "inbox"
    request_stop(inbox_directory, "stop")
    request_pause(inbox_directory, "pause")
    clear_request(inbox_directory, STOP_FILENAME)
    clear_request(inbox_directory, PAUSE_FILENAME)
    clear_request(inbox_directory, STOP_FILENAME)
    inbox = read_inbox(inbox_directory)
    assert not inbox.stop
    assert not inbox.pause


def test_answers_are_kept_per_question_and_rendered_for_the_task(tmp_path: Path) -> None:
    inbox_directory = tmp_path / "inbox"
    answer_question(inbox_directory, 12, 1, "Which unit?", "Knots.")
    answer_question(inbox_directory, 12, 0, "Which column?", "The fifth.")
    answer_question(inbox_directory, 5, 0, "Which branch?", "main")
    inbox = read_inbox(inbox_directory)
    for_twelve = [answer.question_index for answer in inbox.answers if answer.issue_number == 12]
    assert for_twelve == [0, 1]
    assert answers_for(inbox, 12) == ["Which column? — The fifth.", "Which unit? — Knots."]
    assert answers_for(inbox, 99) == []


def test_a_second_answer_replaces_the_first(tmp_path: Path) -> None:
    inbox_directory = tmp_path / "inbox"
    answer_question(inbox_directory, 12, 0, "Which column?", "The fourth.")
    answer_question(inbox_directory, 12, 0, "Which column?", "The fifth.")
    assert answers_for(read_inbox(inbox_directory), 12) == ["Which column? — The fifth."]
