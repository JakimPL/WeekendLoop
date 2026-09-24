from datetime import UTC, datetime, timedelta
from typing import Final

from weekend_loop.github import signed
from weekend_loop.models import IssueComment, ReplyAnswer
from weekend_loop.questions import (
    AGENT_MARKER,
    AGENT_MARKERS,
    PREPARE_LEAD,
    QUESTION_MARKER,
    QUESTION_MARKERS,
    is_agent_comment,
    parse_reply,
    question_thread,
    questions_asked,
    render_question_comment,
    without_quoted_lines,
)

FOOTER: Final[str] = "— weekend-loop run {run_id}"
LEGACY_AGENT_MARKER: Final[str] = AGENT_MARKERS[-1]
LEGACY_QUESTION_MARKER: Final[str] = QUESTION_MARKERS[-1]


def test_the_marker_identifies_the_agents_comment() -> None:
    assert is_agent_comment(f"1. Which unit?\n\n{AGENT_MARKER}\n— weekend-loop run run-7")
    assert not is_agent_comment("1. Knots.")


def test_quoting_the_agent_keeps_a_reply_human() -> None:
    reply = f"> 1. Which unit?\n> {AGENT_MARKER}\n\n1. Knots."
    assert not is_agent_comment(reply)
    assert without_quoted_lines(reply) == "\n1. Knots."


def test_the_question_comment_opens_with_its_marker_and_numbers_each_question() -> None:
    body = render_question_comment(PREPARE_LEAD, ["Which unit?", "Round\n  or truncate?"])
    lines = body.splitlines()
    assert lines[0] == QUESTION_MARKER
    assert "1. Which unit?" in lines
    assert "2. Round or truncate?" in lines


QUESTIONS: Final[list[str]] = ["Which unit does the feed use?", "Round or truncate?"]
OPERATOR: Final[str] = "operator"
ASKED_AT: Final[datetime] = datetime(2026, 9, 17, 20, 0, tzinfo=UTC)


def comment(author: str, minutes: int, body: str) -> IssueComment:
    return IssueComment(author=author, created_at=ASKED_AT + timedelta(minutes=minutes), body=body)


def asking(minutes: int) -> IssueComment:
    body = signed(render_question_comment(PREPARE_LEAD, QUESTIONS), FOOTER, "run-7")
    return comment(OPERATOR, minutes, body)


def test_the_questions_come_back_out_of_the_comment_that_asked_them() -> None:
    body = signed(render_question_comment(PREPARE_LEAD, QUESTIONS), FOOTER, "run-7")
    assert questions_asked(body) == QUESTIONS


def test_a_thread_holds_the_operators_replies_after_the_latest_questions() -> None:
    comments = [
        comment(OPERATOR, -5, "1. An early thought."),
        asking(0),
        comment("colleague", 1, "1. Furlongs."),
        comment(OPERATOR, 2, "1. Knots."),
        comment(OPERATOR, 3, signed("A draft pull request is ready.", FOOTER, "run-8")),
    ]
    thread = question_thread(comments, OPERATOR)
    assert thread is not None
    assert thread.questions == QUESTIONS
    assert thread.replies == ["1. Knots."]


def test_a_thread_without_questions_has_nothing_to_read() -> None:
    assert question_thread([comment(OPERATOR, 0, "1. Knots.")], OPERATOR) is None


def test_a_comment_written_under_the_earlier_name_is_still_the_agents() -> None:
    assert is_agent_comment(f"A draft pull request is ready.\n\n{LEGACY_AGENT_MARKER}")


def test_questions_asked_under_the_earlier_name_still_anchor_the_replies() -> None:
    asked = "\n".join([LEGACY_QUESTION_MARKER, PREPARE_LEAD, "", "1. Which unit?"])
    comments = [comment(OPERATOR, 0, asked), comment(OPERATOR, 2, "1. Knots.")]
    thread = question_thread(comments, OPERATOR)
    assert thread is not None
    assert thread.questions == ["Which unit?"]
    assert thread.replies == ["1. Knots."]


def test_the_latest_questions_restart_the_thread() -> None:
    thread = question_thread([asking(0), comment(OPERATOR, 1, "1. Knots."), asking(2)], OPERATOR)
    assert thread is not None
    assert thread.replies == []


def test_a_numbered_reply_answers_each_question_by_its_number() -> None:
    reply = parse_reply("1. Knots.\n2) Round, half up.", QUESTIONS)
    assert reply.answers == [
        ReplyAnswer(question=QUESTIONS[0], text="Knots."),
        ReplyAnswer(question=QUESTIONS[1], text="Round, half up."),
    ]
    assert reply.note is None


def test_an_answer_carries_on_over_the_lines_below_its_number() -> None:
    reply = parse_reply("1.\nKnots, as the feed\nspecification says.", QUESTIONS)
    assert reply.answers == [
        ReplyAnswer(question=QUESTIONS[0], text="Knots, as the feed\nspecification says.")
    ]


def test_prose_around_the_numbers_becomes_a_standing_note() -> None:
    reply = parse_reply(
        "Thanks for asking.\n\n1. Knots.\n\nAlso, leave the CLI alone this time.", QUESTIONS
    )
    assert reply.answers == [ReplyAnswer(question=QUESTIONS[0], text="Knots.")]
    assert reply.note == "Thanks for asking.\n\nAlso, leave the CLI alone this time."


def test_a_number_nobody_asked_joins_the_note() -> None:
    reply = parse_reply("7. Keep the old parser.", QUESTIONS)
    assert reply.answers == []
    assert reply.note == "7. Keep the old parser."


def test_plain_prose_answers_a_lone_question_whole() -> None:
    reply = parse_reply("Knots, and round half up.", QUESTIONS[:1])
    assert reply.answers == [ReplyAnswer(question=QUESTIONS[0], text="Knots, and round half up.")]
    assert reply.note is None


def test_plain_prose_to_several_questions_is_kept_as_a_note() -> None:
    reply = parse_reply("Knots, and round half up.", QUESTIONS)
    assert reply.answers == []
    assert reply.note == "Knots, and round half up."


def test_the_quoted_questions_in_a_reply_are_not_read_as_answers() -> None:
    reply = parse_reply("> 1. Which unit does the feed use?\n\n1. Knots.", QUESTIONS)
    assert reply.answers == [ReplyAnswer(question=QUESTIONS[0], text="Knots.")]
    assert reply.note is None
