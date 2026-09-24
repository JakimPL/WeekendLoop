from __future__ import annotations

import re
from typing import Final

from weekend_loop.models import IssueComment, ParsedReply, QuestionThread, ReplyAnswer

AGENT_MARKERS: Final[tuple[str, ...]] = ("<!-- weekend-loop -->", "<!-- weekend-agent -->")
QUESTION_MARKERS: Final[tuple[str, ...]] = (
    "<!-- weekend-loop:questions v1 -->",
    "<!-- weekend-agent:questions v1 -->",
)
AGENT_MARKER: Final[str] = AGENT_MARKERS[0]
QUESTION_MARKER: Final[str] = QUESTION_MARKERS[0]
QUOTE_PREFIX: Final[str] = ">"
QUESTION_LINE_TEMPLATE: Final[str] = "{number}. {question}"
PREPARE_LEAD: Final[str] = "Before the weekend run starts, I need a decision on this issue."
STOPPED_LEAD: Final[str] = "Work on this issue stopped for a decision only you can make."
REPLY_HINT: Final[str] = (
    "Reply in a comment on this issue, one line per number, for example `1. yes`. "
    "Anything else you write is kept as standing guidance for this issue."
)
WHITESPACE_PATTERN: Final[re.Pattern[str]] = re.compile(r"\s+")
BLANK_LINES_PATTERN: Final[re.Pattern[str]] = re.compile(r"\n{3,}")
PARAGRAPH_BREAK: Final[str] = "\n\n"
NUMBERED_LINE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^ {0,3}(\d{1,2})\s*[.):](?:\s+(.*))?$")


def without_quoted_lines(body: str) -> str:
    return "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith(QUOTE_PREFIX)
    )


def is_agent_comment(body: str) -> bool:
    text = without_quoted_lines(body)
    return any(marker in text for marker in AGENT_MARKERS)


def is_question_comment(body: str) -> bool:
    text = without_quoted_lines(body)
    return any(marker in text for marker in QUESTION_MARKERS)


def one_line(text: str) -> str:
    return WHITESPACE_PATTERN.sub(" ", text).strip()


def render_question_comment(lead: str, questions: list[str]) -> str:
    numbered = [
        QUESTION_LINE_TEMPLATE.format(number=number, question=one_line(question))
        for number, question in enumerate(questions, start=1)
    ]
    return "\n".join([QUESTION_MARKER, lead, "", *numbered, "", REPLY_HINT])


def numbered_line(line: str) -> tuple[int, str] | None:
    match = NUMBERED_LINE_PATTERN.match(line)
    if match is None:
        return None
    return int(match.group(1)), (match.group(2) or "").strip()


def questions_asked(body: str) -> list[str]:
    found = [numbered_line(line) for line in without_quoted_lines(body).splitlines()]
    return [numbered[1] for numbered in found if numbered is not None]


def question_thread(comments: list[IssueComment], operator_login: str) -> QuestionThread | None:
    ordered = sorted(comments, key=lambda comment: comment.created_at)
    anchors = [index for index, comment in enumerate(ordered) if is_question_comment(comment.body)]
    if not anchors:
        return None
    anchor = anchors[-1]
    replies = [
        comment.body
        for comment in ordered[anchor + 1 :]
        if comment.author == operator_login and not is_agent_comment(comment.body)
    ]
    return QuestionThread(questions=questions_asked(ordered[anchor].body), replies=replies)


def split_reply(body: str, question_count: int) -> tuple[dict[int, list[str]], list[str]]:
    numbered: dict[int, list[str]] = {}
    loose: list[str] = []
    current: int | None = None
    for line in without_quoted_lines(body).splitlines():
        found = numbered_line(line)
        if found is not None and 1 <= found[0] <= question_count:
            current = found[0]
            numbered.setdefault(current, []).append(found[1])
        elif not line.strip():
            current = None
            loose.append("")
        elif current is not None:
            numbered[current].append(line.strip())
        else:
            loose.append(line)
    return numbered, loose


def parse_reply(body: str, questions: list[str]) -> ParsedReply:
    numbered, loose = split_reply(body, len(questions))
    answers = [
        ReplyAnswer(question=questions[number - 1], text="\n".join(parts).strip())
        for number, parts in sorted(numbered.items())
        if "\n".join(parts).strip()
    ]
    note = BLANK_LINES_PATTERN.sub(PARAGRAPH_BREAK, "\n".join(loose)).strip() or None
    if not answers and len(questions) == 1 and note is not None:
        return ParsedReply(answers=[ReplyAnswer(question=questions[0], text=note)], note=None)
    return ParsedReply(answers=answers, note=note)
