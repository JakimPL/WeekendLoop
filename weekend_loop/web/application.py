from __future__ import annotations

from datetime import UTC, datetime
from typing import Final
from zoneinfo import ZoneInfo

import uvicorn
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from weekend_loop.briefing import append_note, question_key, read_briefing, record_answer
from weekend_loop.mailbox import (
    PAUSE_FILENAME,
    STOP_FILENAME,
    answer_question,
    approve_issue,
    clear_request,
    read_inbox,
    request_pause,
    request_stop,
    skip_issue,
)
from weekend_loop.models import Inbox, Policy, RunState
from weekend_loop.runs import RunDirectory, list_run_ids, load_run_state, open_run_directory
from weekend_loop.status import current_status, read_run_events
from weekend_loop.transcript import rendered_transcript
from weekend_loop.web import demo_pages, pages
from weekend_loop.web.demo import DemoView, build_demo_view
from weekend_loop.web.view import LIVE_EVENT_COUNT, LIVE_TRANSCRIPT_LINES, build_view

APPLICATION_TITLE: Final[str] = "Weekend Loop"
DEFAULT_HOST: Final[str] = "127.0.0.1"
DEFAULT_PORT: Final[int] = 8788
SEE_OTHER: Final[int] = 303
NOT_FOUND: Final[int] = 404
UNKNOWN_ACTION: Final[int] = 400
HEALTHY_TEXT: Final[str] = "ok"
STOP_ACTION: Final[str] = "stop"
PAUSE_ACTION: Final[str] = "pause"
RESUME_ACTION: Final[str] = "resume"
APPROVE_ACTION: Final[str] = "approve"
SKIP_ACTION: Final[str] = "skip"
WEB_REASON: Final[str] = "web"


def resolve_run_id(policy: Policy, run_id: str | None) -> str | None:
    known = list_run_ids(policy.state_dir)
    if run_id is not None:
        return run_id if run_id in known else None
    return known[-1] if known else None


def open_run(policy: Policy, run_id: str | None) -> tuple[RunDirectory, RunState] | None:
    resolved = resolve_run_id(policy, run_id)
    if resolved is None:
        return None
    run_directory = open_run_directory(policy.state_dir, resolved)
    return run_directory, load_run_state(run_directory)


def require_run(policy: Policy, run_id: str | None) -> tuple[RunDirectory, RunState]:
    opened = open_run(policy, run_id)
    if opened is None:
        raise HTTPException(status_code=NOT_FOUND, detail="no such run")
    return opened


def demo_view_of(policy: Policy, run_id: str | None) -> DemoView | None:
    opened = open_run(policy, run_id)
    if opened is None:
        if run_id is not None:
            raise HTTPException(status_code=NOT_FOUND, detail="no such run")
        return None
    run_directory, state = opened
    return build_demo_view(
        state,
        current_status(run_directory, 0, 0),
        read_run_events(run_directory),
        ZoneInfo(policy.schedule.timezone),
    )


def pull_request_links(state: RunState) -> dict[int, str]:
    return {
        task.issue_number: task.pull_request_url
        for task in state.tasks
        if task.pull_request_url is not None
    }


def read_digest(run_directory: RunDirectory) -> str | None:
    path = run_directory.digest_path
    return path.read_text() if path.is_file() else None


def live_question_index(state: RunState, issue_number: int, question: str) -> int | None:
    for task in state.tasks:
        if task.issue_number != issue_number:
            continue
        questions = (
            task.delivery.questions
            if task.delivery is not None and task.delivery.questions
            else (task.assessment.questions if task.assessment is not None else [])
        )
        for index, candidate in enumerate(questions):
            if question_key(candidate) == question_key(question):
                return index
    return None


def build_application(policy: Policy) -> FastAPI:
    application = FastAPI(title=APPLICATION_TITLE)

    @application.get("/healthz", response_class=PlainTextResponse)
    def healthz() -> str:
        return HEALTHY_TEXT

    @application.get("/", response_class=HTMLResponse)
    def run_page(run_id: str | None = None) -> str:
        opened = open_run(policy, run_id)
        if opened is None:
            if run_id is not None:
                raise HTTPException(status_code=NOT_FOUND, detail="no such run")
            return pages.render_empty()
        run_directory, state = opened
        briefing = read_briefing(policy.state_dir, state.repo_key)
        view = build_view(state, read_inbox_of(run_directory), briefing, datetime.now(UTC))
        status = current_status(run_directory, LIVE_TRANSCRIPT_LINES, LIVE_EVENT_COUNT)
        return pages.render_run(view, status, pull_request_links(state), read_digest(run_directory))

    @application.get("/runs/{run_id}/live", response_class=HTMLResponse)
    def live_page(run_id: str) -> str:
        run_directory, _ = require_run(policy, run_id)
        status = current_status(run_directory, 0, 0)
        lines = rendered_transcript(status.transcript) if status.transcript is not None else []
        return pages.render_live_transcript(status, lines)

    @application.get(demo_pages.DEMO_PATH, response_class=HTMLResponse)
    def demo_page(run_id: str | None = None) -> str:
        return demo_pages.render_demo_page(demo_view_of(policy, run_id))

    @application.get(demo_pages.DEMO_BODY_PATH, response_class=HTMLResponse)
    def demo_body(run_id: str | None = None) -> str:
        return demo_pages.render_demo_body(demo_view_of(policy, run_id))

    @application.get("/runs", response_class=HTMLResponse)
    def runs_page() -> str:
        return pages.render_runs(list_run_ids(policy.state_dir))

    @application.get("/questions", response_class=HTMLResponse)
    def questions_page(run_id: str | None = None) -> str:
        opened = open_run(policy, run_id)
        if opened is None:
            return pages.render_questions([])
        run_directory, state = opened
        briefing = read_briefing(policy.state_dir, state.repo_key)
        view = build_view(state, read_inbox_of(run_directory), briefing, datetime.now(UTC))
        return pages.render_questions(view.questions)

    @application.post("/questions")
    def answer(
        issue_number: int = Form(...),
        question: str = Form(...),
        text: str = Form(...),
        run_id: str | None = Form(None),
    ) -> RedirectResponse:
        run_directory, state = require_run(policy, run_id)
        record_answer(policy.state_dir, state.repo_key, issue_number, question, text)
        index = live_question_index(state, issue_number, question)
        if index is not None:
            answer_question(run_directory.inbox, issue_number, index, question, text)
        return RedirectResponse("/questions", status_code=SEE_OTHER)

    @application.get("/issues/{issue_number}", response_class=HTMLResponse)
    def issue_page(issue_number: int) -> str:
        opened = open_run(policy, None)
        repo_key = opened[1].repo_key if opened is not None else policy.scheduled_repo_key
        return pages.render_issue(read_briefing(policy.state_dir, repo_key), issue_number)

    @application.post("/issues/{issue_number}/notes")
    def add_note(issue_number: int, text: str = Form(...)) -> RedirectResponse:
        opened = open_run(policy, None)
        repo_key = opened[1].repo_key if opened is not None else policy.scheduled_repo_key
        append_note(policy.state_dir, repo_key, issue_number, text)
        return RedirectResponse(f"/issues/{issue_number}", status_code=SEE_OTHER)

    @application.post("/control")
    def control(action: str = Form(...), run_id: str | None = Form(None)) -> RedirectResponse:
        run_directory, _ = require_run(policy, run_id)
        apply_control(run_directory, action)
        return RedirectResponse("/", status_code=SEE_OTHER)

    @application.post("/tasks/{issue_number}/consent")
    def consent(
        issue_number: int, action: str = Form(...), run_id: str | None = Form(None)
    ) -> RedirectResponse:
        run_directory, _ = require_run(policy, run_id)
        if action == APPROVE_ACTION:
            approve_issue(run_directory.inbox, issue_number, WEB_REASON)
        elif action == SKIP_ACTION:
            skip_issue(run_directory.inbox, issue_number, WEB_REASON)
        else:
            raise HTTPException(status_code=UNKNOWN_ACTION, detail=f"unknown action {action!r}")
        return RedirectResponse("/", status_code=SEE_OTHER)

    return application


def apply_control(run_directory: RunDirectory, action: str) -> None:
    if action == STOP_ACTION:
        request_stop(run_directory.inbox, WEB_REASON)
    elif action == PAUSE_ACTION:
        request_pause(run_directory.inbox, WEB_REASON)
    elif action == RESUME_ACTION:
        clear_request(run_directory.inbox, STOP_FILENAME)
        clear_request(run_directory.inbox, PAUSE_FILENAME)
    else:
        raise HTTPException(status_code=UNKNOWN_ACTION, detail=f"unknown action {action!r}")


def read_inbox_of(run_directory: RunDirectory) -> Inbox:
    return read_inbox(run_directory.inbox)


def serve(policy: Policy, host: str, port: int) -> None:
    uvicorn.run(build_application(policy), host=host, port=port)
