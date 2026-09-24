from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from fastapi.testclient import TestClient

from tests.unit.conftest import (
    ELIGIBLE,
    build_assessment,
    build_run_state,
    build_task,
    write_test_policy,
)
from tests.unit.live_run import (
    build_activity,
    build_pulse,
    orchestrator_stand_in,
    said,
    write_transcript,
)
from weekend_loop.briefing import read_briefing, read_notes
from weekend_loop.mailbox import STOP_FILENAME, read_inbox
from weekend_loop.models import (
    ActivityKind,
    Blocker,
    Effort,
    EventType,
    Policy,
    RepoMode,
    Risk,
    RunPhase,
    RunState,
    TaskStatus,
    Verdict,
)
from weekend_loop.policy import policy_at
from weekend_loop.runs import (
    append_event,
    create_run_directory,
    load_run_state,
    open_run_directory,
    save_run_state,
    write_pulse,
)
from weekend_loop.supervision import current_boot_id
from weekend_loop.web.application import build_application
from weekend_loop.web.pages import render_question_card, render_questions, render_runs
from weekend_loop.web.view import ANSWER_SOURCE_BRIEFING, QuestionRow

RUN_ID: Final[str] = "20260918-210000-demo"
REPO_KEY: Final[str] = "demo"
QUESTION: Final[str] = "Which column holds speed?"
MARKUP_TITLE: Final[str] = "Improve the summary <script>alert(1)</script>"


def build_state() -> RunState:
    tasks = [
        build_task(
            2,
            MARKUP_TITLE,
            TaskStatus.ASSESSED,
            ELIGIBLE,
            build_assessment(
                Verdict.NEEDS_INPUT, Effort.S, Risk.TESTS, [Blocker.UNCLEAR_GOAL], [QUESTION]
            ),
        )
    ]
    return build_run_state(tasks, 1.5, ["budget"], RUN_ID, REPO_KEY, RepoMode.EXECUTE)


def prepare(tmp_path: Path) -> Policy:
    policy = policy_at(write_test_policy(tmp_path, None))
    run_directory = create_run_directory(policy.state_dir, RUN_ID)
    save_run_state(run_directory, build_state())
    return policy


def client_for(policy: Policy) -> TestClient:
    return TestClient(build_application(policy))


def question_row(answer: str, source: str) -> QuestionRow:
    return QuestionRow(
        issue_number=2,
        title=MARKUP_TITLE,
        question_index=0,
        question=QUESTION,
        answer=answer,
        source=source,
    )


def test_a_question_card_prefills_the_answer_and_names_where_it_came_from() -> None:
    card = render_question_card(question_row("The fifth, in knots.", ANSWER_SOURCE_BRIEFING))

    assert QUESTION in card
    assert "The fifth, in knots." in card
    assert "standing answer" in card


def test_a_queue_with_nothing_in_it_says_so() -> None:
    assert "Nothing is waiting on you." in render_questions([])


def test_text_from_github_cannot_inject_markup_into_a_question_card() -> None:
    card = render_question_card(question_row("", ""))

    assert "<script>" not in card
    assert "&lt;script&gt;" in card


def test_the_history_lists_the_newest_run_first() -> None:
    body = render_runs(["20260911-200000-demo", RUN_ID])

    assert body.index(RUN_ID) < body.index("20260911-200000-demo")


def test_the_application_reports_that_it_is_up(tmp_path: Path) -> None:
    response = client_for(prepare(tmp_path)).get("/healthz")

    assert response.status_code == 200
    assert response.text == "ok"


def test_the_run_page_shows_the_latest_run_without_being_told_which(tmp_path: Path) -> None:
    response = client_for(prepare(tmp_path)).get("/")

    assert response.status_code == 200
    assert RUN_ID in response.text
    assert "$1.50 of $15.00" in response.text


def test_a_run_nobody_has_heard_of_is_a_missing_page_not_a_traceback(tmp_path: Path) -> None:
    response = client_for(prepare(tmp_path)).get("/?run_id=20260101-000000-demo")

    assert response.status_code == 404


def test_a_state_directory_with_no_run_still_serves_a_page(tmp_path: Path) -> None:
    policy = policy_at(write_test_policy(tmp_path, None))

    response = client_for(policy).get("/")

    assert response.status_code == 200
    assert "No run has written its state yet" in response.text


def test_an_answer_lands_in_the_briefing_and_in_the_run_that_asked_it(tmp_path: Path) -> None:
    policy = prepare(tmp_path)

    response = client_for(policy).post(
        "/questions",
        data={"issue_number": "2", "question": QUESTION, "text": "The fifth, in knots."},
        follow_redirects=False,
    )

    assert response.status_code == 303
    briefing = read_briefing(policy.state_dir, REPO_KEY)
    assert [answer.text for answer in briefing.answers] == ["The fifth, in knots."]
    inbox = read_inbox(policy.state_dir / "runs" / RUN_ID / "inbox")
    assert [answer.text for answer in inbox.answers] == ["The fifth, in knots."]


def test_a_standing_note_is_kept_for_the_issue_it_was_written_about(tmp_path: Path) -> None:
    policy = prepare(tmp_path)

    client_for(policy).post("/issues/2/notes", data={"text": "Reuse the helper in records.py."})

    notes = read_notes(policy.state_dir, REPO_KEY, 2)
    assert notes is not None
    assert [note.text for note in notes.notes] == ["Reuse the helper in records.py."]


def test_the_control_form_leaves_a_standing_request_for_the_orchestrator(tmp_path: Path) -> None:
    policy = prepare(tmp_path)

    client_for(policy).post("/control", data={"action": "stop"})

    assert (policy.state_dir / "runs" / RUN_ID / "inbox" / STOP_FILENAME).is_file()

    client_for(policy).post("/control", data={"action": "resume"})

    assert not (policy.state_dir / "runs" / RUN_ID / "inbox" / STOP_FILENAME).is_file()


def test_an_action_the_application_does_not_know_is_refused(tmp_path: Path) -> None:
    response = client_for(prepare(tmp_path)).post("/control", data={"action": "detonate"})

    assert response.status_code == 400


def test_the_question_queue_carries_the_answer_already_on_file(tmp_path: Path) -> None:
    policy = prepare(tmp_path)
    client = client_for(policy)
    client.post(
        "/questions", data={"issue_number": "2", "question": QUESTION, "text": "The fifth."}
    )

    response = client.get("/questions")

    assert "The fifth." in response.text
    assert QUESTION in response.text


def seed_working_run(policy: Policy, pid: int, boot_id: str, step_count: int) -> Path:
    run_directory = open_run_directory(policy.state_dir, RUN_ID)
    state = load_run_state(run_directory).model_copy(update={"phase": RunPhase.EXECUTE})
    save_run_state(run_directory, state)
    transcript = write_transcript(
        run_directory.task_directory(2) / "worker-1.jsonl",
        [said(f"step {index}") for index in range(step_count)],
    )
    now = datetime.now(UTC)
    activity = build_activity(ActivityKind.WORKING, 2, transcript, now, None)
    write_pulse(run_directory, build_pulse(pid, boot_id, now, activity))
    append_event(run_directory, EventType.TASK_STARTED, "branch weekend/2-summary", 2)
    return transcript


def test_a_finished_run_shows_its_live_card_without_refreshing(tmp_path: Path) -> None:
    response = client_for(prepare(tmp_path)).get("/")

    assert '<div class="weekend-label">live</div>' in response.text
    assert "finished · no pulse" in response.text
    assert 'http-equiv="refresh"' not in response.text


def test_a_living_run_refreshes_and_shows_what_the_worker_is_doing(tmp_path: Path) -> None:
    policy = prepare(tmp_path)
    with orchestrator_stand_in() as pid:
        seed_working_run(policy, pid, current_boot_id(), 40)

        response = client_for(policy).get("/")

    assert '<meta http-equiv="refresh" content="10">' in response.text
    assert "now: working on #2" in response.text
    assert "says: step 39" in response.text
    assert "says: step 10" in response.text
    assert "says: step 9\n" not in response.text
    assert "task_started #2: branch weekend/2-summary" in response.text
    assert f'href="/runs/{RUN_ID}/live"' in response.text


def test_a_run_whose_process_is_gone_stops_refreshing(tmp_path: Path) -> None:
    policy = prepare(tmp_path)
    seed_working_run(policy, 4242, "another-boot", 3)

    response = client_for(policy).get("/")

    assert "dead · pid 4242" in response.text
    assert 'http-equiv="refresh"' not in response.text


def test_the_live_page_carries_the_whole_transcript(tmp_path: Path) -> None:
    policy = prepare(tmp_path)
    transcript = seed_working_run(policy, 4242, "another-boot", 40)

    response = client_for(policy).get(f"/runs/{RUN_ID}/live")

    assert response.status_code == 200
    assert "says: step 0\n" in response.text
    assert "says: step 39" in response.text
    assert str(transcript) in response.text


def test_the_live_page_of_an_unknown_run_is_missing(tmp_path: Path) -> None:
    response = client_for(prepare(tmp_path)).get("/runs/20260101-000000-pilot/live")

    assert response.status_code == 404
