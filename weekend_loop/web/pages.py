from __future__ import annotations

from typing import Final

from weekend_loop.briefing import RepoBriefing, notes_of
from weekend_loop.status import Liveness, RunStatus, liveness_phrase
from weekend_loop.web.render import (
    render_budget,
    render_header,
    render_lines_block,
    render_live,
    render_notes,
    render_status_chips,
)
from weekend_loop.web.theme import PAGE_STYLE, PRODUCT_NAME, escape
from weekend_loop.web.view import (
    ANSWER_SOURCE_BRIEFING,
    ANSWER_SOURCE_RUN,
    QuestionRow,
    RunView,
    TaskRow,
)

PAGE_TITLE: Final[str] = PRODUCT_NAME
NO_RUN_TEXT: Final[str] = "No run has written its state yet. Start one with `weekend-loop prepare`."
NO_QUESTIONS_TEXT: Final[str] = "Nothing is waiting on you."
NO_NOTES_TEXT: Final[str] = "No standing note yet."
NO_DIGEST_TEXT: Final[str] = "This run has written no digest yet."
NO_TRANSCRIPT_TEXT: Final[str] = "This run has written no transcript yet."
REFRESH_SECONDS: Final[int] = 10
LIVE_PATH_TEMPLATE: Final[str] = "/runs/{run_id}/live"
SOURCE_LABELS: Final[dict[str, str]] = {
    ANSWER_SOURCE_RUN: "answered during this run",
    ANSWER_SOURCE_BRIEFING: "standing answer",
}
NAVIGATION: Final[str] = (
    '<div class="weekend-panel"><div class="weekend-nav">'
    '<a href="/">Run</a><a href="/questions">Questions</a><a href="/runs">History</a>'
    "</div></div>"
)


def document(body: str, head: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"{head}<title>{PAGE_TITLE}</title>{PAGE_STYLE}</head>"
        f"<body>{NAVIGATION}{body}</body></html>"
    )


def page(body: str) -> str:
    return document(body, "")


def refreshing_head(status: RunStatus) -> str:
    if status.liveness is not Liveness.ALIVE:
        return ""
    return f'<meta http-equiv="refresh" content="{REFRESH_SECONDS}">'


def live_path(run_id: str) -> str:
    return LIVE_PATH_TEMPLATE.format(run_id=run_id)


def card(label: str, body: str) -> str:
    return (
        '<div class="weekend-panel"><div class="weekend-card">'
        f'<div class="weekend-label">{escape(label)}</div>{body}</div></div>'
    )


def render_empty() -> str:
    return page(card("runs", f'<div class="weekend-note">{escape(NO_RUN_TEXT)}</div>'))


def render_task_table(rows: list[TaskRow]) -> str:
    if not rows:
        return '<div class="weekend-note">no task yet</div>'
    header = (
        "<tr><th>issue</th><th>title</th><th>status</th><th>verdict</th>"
        "<th>branch</th><th>lines</th><th>cost</th></tr>"
    )
    body = "".join(
        f"<tr><td>#{row.issue_number}</td><td>{escape(row.title)}</td>"
        f"<td>{escape(row.status)}</td><td>{escape(row.verdict)}</td>"
        f"<td>{escape(row.branch)}</td><td>{row.diff_lines}</td>"
        f"<td>${row.cost_usd:.2f}</td></tr>"
        for row in rows
    )
    return f"<table>{header}{body}</table>"


def render_pull_requests(rows: list[TaskRow], links: dict[int, str]) -> str:
    entries = [
        f'<div class="weekend-note">#{row.issue_number} '
        f'<a href="{escape(links[row.issue_number])}">{escape(links[row.issue_number])}</a></div>'
        for row in rows
        if row.issue_number in links
    ]
    return "".join(entries) if entries else '<div class="weekend-note">nothing pushed yet</div>'


def render_control_form() -> str:
    buttons = "".join(
        f'<button type="submit" name="action" value="{action}">{label}</button> '
        for action, label in (
            ("stop", "stop the run"),
            ("pause", "pause after this task"),
            ("resume", "clear stop and pause"),
        )
    )
    return f'<form method="post" action="/control">{buttons}</form>'


def render_run(
    view: RunView, status: RunStatus, pull_requests: dict[int, str], digest: str | None
) -> str:
    digest_body = (
        f'<div class="weekend-digest">{escape(digest)}</div>'
        if digest is not None
        else f'<div class="weekend-note">{escape(NO_DIGEST_TEXT)}</div>'
    )
    body = (
        render_header(view, status)
        + render_budget(view)
        + render_status_chips(view)
        + render_live(status, live_path(status.run_id))
        + card("tasks", render_task_table(view.tasks))
        + card("ready for review", render_pull_requests(view.review, pull_requests))
        + card("control", render_control_form())
        + card("notes", render_notes(view))
        + card("digest", digest_body)
    )
    return document(body, refreshing_head(status))


def render_live_transcript(status: RunStatus, lines: list[str]) -> str:
    source = str(status.transcript) if status.transcript is not None else NO_TRANSCRIPT_TEXT
    summary = (
        f'<div class="weekend-note">run {escape(status.run_id)} · '
        f"{escape(liveness_phrase(status))}</div>"
        f'<div class="weekend-note">{escape(source)}</div>'
    )
    return document(
        card("live transcript", summary + render_lines_block(lines, NO_TRANSCRIPT_TEXT)),
        refreshing_head(status),
    )


def render_question_card(row: QuestionRow) -> str:
    source = SOURCE_LABELS.get(row.source, "")
    provenance = f'<div class="weekend-source">{escape(source)}</div>' if source else ""
    return (
        '<div class="weekend-panel"><div class="weekend-card">'
        f'<div class="weekend-label">#{row.issue_number} {escape(row.title)}</div>'
        f"<div>{escape(row.question)}</div>"
        f'<form method="post" action="/questions">'
        f'<input type="hidden" name="issue_number" value="{row.issue_number}">'
        f'<input type="hidden" name="question" value="{escape(row.question)}">'
        f'<textarea name="text" rows="3">{escape(row.answer)}</textarea>'
        f"{provenance}"
        '<button type="submit">save answer</button>'
        "</form></div></div>"
    )


def render_questions(rows: list[QuestionRow]) -> str:
    if not rows:
        return page(
            card("questions", f'<div class="weekend-note">{escape(NO_QUESTIONS_TEXT)}</div>')
        )
    return page("".join(render_question_card(row) for row in rows))


def render_issue(briefing: RepoBriefing, issue_number: int) -> str:
    answers = "".join(
        f'<div class="weekend-note">{escape(answer.question)} — {escape(answer.text)}</div>'
        for answer in briefing.answers
        if answer.issue_number == issue_number
    )
    notes = "".join(
        f'<div class="weekend-note">{escape(note.text)}</div>'
        for note in notes_of(briefing, issue_number)
    )
    form = (
        f'<form method="post" action="/issues/{issue_number}/notes">'
        '<textarea name="text" rows="3" placeholder="standing guidance for this issue"></textarea>'
        '<button type="submit">add note</button></form>'
    )
    return page(
        card(f"answers on #{issue_number}", answers or '<div class="weekend-note">none</div>')
        + card(
            f"standing notes on #{issue_number}",
            (notes or f'<div class="weekend-note">{escape(NO_NOTES_TEXT)}</div>') + form,
        )
    )


def render_runs(run_ids: list[str]) -> str:
    entries = "".join(
        f'<div class="weekend-note"><a href="/?run_id={escape(run_id)}">{escape(run_id)}</a></div>'
        for run_id in reversed(run_ids)
    )
    return page(card("runs", entries or f'<div class="weekend-note">{escape(NO_RUN_TEXT)}</div>'))
