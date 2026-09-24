from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.unit.conftest import ELIGIBLE, build_assessment, build_run_state, build_task
from tests.unit.live_run import build_activity, build_pulse, quiet_status
from weekend_loop.briefing import empty_briefing, read_briefing, record_answer
from weekend_loop.mailbox import (
    answer_question,
    approve_issue,
    post_message,
    read_inbox,
    request_stop,
)
from weekend_loop.models import (
    ActivityKind,
    Effort,
    EventType,
    RepoMode,
    Risk,
    RunEvent,
    RunPhase,
    RunState,
    TaskStatus,
    Verdict,
)
from weekend_loop.status import Liveness, RunExtras, build_status
from weekend_loop.web.render import (
    render_budget,
    render_header,
    render_live,
    render_notes,
    render_status_chips,
)
from weekend_loop.web.theme import ACCENT, ALERT, FONT_STACK, PAGE_STYLE, PRODUCT_NAME
from weekend_loop.web.view import ANSWER_SOURCE_BRIEFING, ANSWER_SOURCE_RUN, build_view

RUN_ID = "20260918-210000-demo"
REPO_KEY = "demo"


def build_state(spent_usd: float) -> RunState:
    tasks = [
        build_task(
            1,
            "Empty speed field",
            TaskStatus.REVIEW,
            ELIGIBLE,
            build_assessment(Verdict.EXECUTE, Effort.XS, Risk.TESTS, [], []),
        ),
        build_task(
            2,
            "Improve the summary <b>",
            TaskStatus.ASSESSED,
            ELIGIBLE,
            build_assessment(
                Verdict.NEEDS_INPUT, Effort.S, Risk.BEHAVIOUR, [], ["Which column holds speed?"]
            ),
        ),
        build_task(
            3,
            "Bearing range",
            TaskStatus.ASSESSED,
            ELIGIBLE,
            build_assessment(Verdict.EXECUTE, Effort.XS, Risk.REFACTOR, [], []),
        ),
    ]
    return build_run_state(tasks, spent_usd, ["budget"], RUN_ID, "demo", RepoMode.EXECUTE)


def test_the_view_summarises_the_run_for_a_glance(tmp_path: Path) -> None:
    view = build_view(
        build_state(3.0), read_inbox(tmp_path), empty_briefing(REPO_KEY), datetime.now(UTC)
    )
    assert view.counts == {"assessed": 2, "review": 1}
    assert view.budget_fraction == 0.2
    assert [row.issue_number for row in view.review] == [1]
    assert view.notes == ["budget"]
    assert not view.stop


def test_questions_carry_the_answer_once_the_reviewer_wrote_one(tmp_path: Path) -> None:
    answer_question(tmp_path, 2, 0, "Which column holds speed?", "The fifth, in knots.")
    view = build_view(
        build_state(3.0), read_inbox(tmp_path), empty_briefing(REPO_KEY), datetime.now(UTC)
    )
    assert len(view.questions) == 1
    assert view.questions[0].issue_number == 2
    assert view.questions[0].answer == "The fifth, in knots."


def test_a_question_says_whether_its_answer_is_standing_or_from_this_run(tmp_path: Path) -> None:
    record_answer(tmp_path, REPO_KEY, 2, "Which column holds speed?", "The fifth, in knots.")
    standing = build_view(
        build_state(3.0), read_inbox(tmp_path), read_briefing(tmp_path, REPO_KEY), datetime.now(UTC)
    )
    assert standing.questions[0].answer == "The fifth, in knots."
    assert standing.questions[0].source == ANSWER_SOURCE_BRIEFING

    answer_question(tmp_path, 2, 0, "Which column holds speed?", "The sixth, in metres.")
    live = build_view(
        build_state(3.0), read_inbox(tmp_path), read_briefing(tmp_path, REPO_KEY), datetime.now(UTC)
    )
    assert live.questions[0].answer == "The sixth, in metres."
    assert live.questions[0].source == ANSWER_SOURCE_RUN


def test_only_executable_proposals_wait_for_approval(tmp_path: Path) -> None:
    before = build_view(
        build_state(3.0), read_inbox(tmp_path), empty_briefing(REPO_KEY), datetime.now(UTC)
    )
    assert [row.issue_number for row in before.approvals] == [3]
    approve_issue(tmp_path, 3, "panel")
    after = build_view(
        build_state(3.0), read_inbox(tmp_path), empty_briefing(REPO_KEY), datetime.now(UTC)
    )
    assert after.approvals == []


def test_a_stop_request_shows_in_the_header(tmp_path: Path) -> None:
    request_stop(tmp_path, "the reviewer said so")
    state = build_state(3.0)
    view = build_view(state, read_inbox(tmp_path), empty_briefing(REPO_KEY), datetime.now(UTC))
    assert view.stop
    assert "stop requested" in render_header(view, quiet_status(state, datetime.now(UTC)))


def test_the_page_styles_itself_from_the_theme_and_a_font_the_reader_already_has(
    tmp_path: Path,
) -> None:
    assert ACCENT in PAGE_STYLE
    assert FONT_STACK in PAGE_STYLE
    assert "https://" not in PAGE_STYLE
    state = build_state(3.0)
    view = build_view(state, read_inbox(tmp_path), empty_briefing(REPO_KEY), datetime.now(UTC))
    assert PRODUCT_NAME in render_header(view, quiet_status(state, datetime.now(UTC)))
    assert "review 1" in render_status_chips(view)


def test_text_from_github_cannot_inject_markup(tmp_path: Path) -> None:
    post_message(tmp_path, "<script>alert('x')</script>")
    view = build_view(
        build_state(3.0), read_inbox(tmp_path), empty_briefing(REPO_KEY), datetime.now(UTC)
    )
    notes = render_notes(view)
    assert "<script>" not in notes
    assert "&lt;script&gt;" in notes


def test_the_budget_bar_turns_to_alert_near_the_envelope(tmp_path: Path) -> None:
    inbox = read_inbox(tmp_path)
    calm = render_budget(
        build_view(build_state(3.0), inbox, empty_briefing(REPO_KEY), datetime.now(UTC))
    )
    assert ACCENT in calm
    assert "20%" in calm
    alarmed = render_budget(
        build_view(build_state(14.9), inbox, empty_briefing(REPO_KEY), datetime.now(UTC))
    )
    assert ALERT in alarmed


def test_a_stale_heartbeat_is_named_in_the_header(tmp_path: Path) -> None:
    state = build_state(3.0)
    stale = state.model_copy(update={"heartbeat_at": datetime.now(UTC) - timedelta(hours=2)})
    view = build_view(stale, read_inbox(tmp_path), empty_briefing(REPO_KEY), datetime.now(UTC))
    assert "stale" in render_header(view, quiet_status(stale, datetime.now(UTC)))


def test_the_header_reads_the_pulse_once_the_run_has_one(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    state = build_state(3.0).model_copy(update={"phase": RunPhase.EXECUTE})
    activity = build_activity(ActivityKind.WORKING, 3, None, now - timedelta(minutes=4), None)
    pulse = build_pulse(4242, "boot", now - timedelta(seconds=20), activity)
    status = build_status(state, RunExtras(), pulse, Liveness.ALIVE, [], None, [], now)
    view = build_view(state, read_inbox(tmp_path), empty_briefing(REPO_KEY), now)

    header = render_header(view, status)

    assert "alive · now: working on #3 for 4 min · pulse 20 s ago" in header
    assert "heartbeat" not in header


def test_the_live_card_shows_the_transcript_and_the_events(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    state = build_state(3.0)
    event = RunEvent(at=now, event=EventType.GATE_FINISHED, issue_number=3, detail="passed <ok>")
    status = build_status(
        state, RunExtras(), None, Liveness.FINISHED, [event], None, ["says: <b>done</b>"], now
    )

    card = render_live(status, "/runs/x/live")

    assert "finished · no pulse" in card
    assert "says: &lt;b&gt;done&lt;/b&gt;" in card
    assert "gate_finished #3: passed &lt;ok&gt;" in card
    assert 'href="/runs/x/live"' in card
    assert "full transcript" not in render_live(status, None)
