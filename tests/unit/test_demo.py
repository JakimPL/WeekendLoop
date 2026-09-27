from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from tests.unit.conftest import (
    ELIGIBLE,
    build_assessment,
    build_run_state,
    build_task,
    write_test_policy,
)
from tests.unit.live_run import build_activity, build_pulse, orchestrator_stand_in
from weekend_loop.adopt import ADOPTED_NOTE_TEMPLATE
from weekend_loop.execute import OUTSIDE_LIMITS_REASON
from weekend_loop.models import (
    ActivityKind,
    AssessmentOutcome,
    ClaudeOutcome,
    Effort,
    EligibilityDecision,
    EventType,
    IneligibilityReason,
    Policy,
    RepoMode,
    Risk,
    RunEvent,
    RunPhase,
    RunState,
    Task,
    TaskStatus,
    Verdict,
)
from weekend_loop.policy import policy_at
from weekend_loop.runs import (
    RunDirectory,
    append_event,
    create_run_directory,
    save_run_state,
    write_pulse,
)
from weekend_loop.status import read_run_events
from weekend_loop.supervision import current_boot_id
from weekend_loop.triage import record_assessment
from weekend_loop.web.application import build_application
from weekend_loop.web.demo import STEP_LABELS, Outcome, Tone, demo_step, demo_task
from weekend_loop.web.demo_pages import DEMO_REFRESH_SECONDS

RUN_ID: Final[str] = "20260918-123257-pilot"
LATER_RUN_ID: Final[str] = "20260918-150000-pilot"
REPO_KEY: Final[str] = "pilot"
WARSAW: Final[ZoneInfo] = ZoneInfo("Europe/Warsaw")
MOMENT: Final[datetime] = datetime(2026, 9, 18, 12, 33, 7, tzinfo=UTC)
PULL_REQUEST_URL: Final[str] = "https://github.com/owner/repo/pull/12"


def event(kind: EventType, detail: str, issue_number: int | None) -> RunEvent:
    return RunEvent(at=MOMENT, event=kind, issue_number=issue_number, detail=detail)


def outcome_of(kind: EventType, detail: str) -> Outcome:
    return demo_step(event(kind, detail, 1), RUN_ID, WARSAW).outcome


def task_with(status: TaskStatus, verdict: Verdict | None) -> Task:
    assessment = (
        build_assessment(verdict, Effort.XS, Risk.DOCS, [], []) if verdict is not None else None
    )
    return build_task(1, "The New chat button does nothing", status, ELIGIBLE, assessment)


def demo_state(run_id: str, tasks: list[Task]) -> RunState:
    return build_run_state(tasks, 0.18, [], run_id, REPO_KEY, RepoMode.EXECUTE)


def seeded_run(policy: Policy, run_id: str, detail: str) -> RunDirectory:
    run_directory = create_run_directory(policy.state_dir, run_id)
    save_run_state(run_directory, demo_state(run_id, [task_with(TaskStatus.ASSESSED, None)]))
    append_event(run_directory, EventType.PREFILTER_FINISHED, detail, None)
    return run_directory


def client_for(tmp_path: Path) -> tuple[Policy, TestClient]:
    policy = policy_at(write_test_policy(tmp_path, None))
    return policy, TestClient(build_application(policy))


def test_every_event_the_run_logs_has_a_step_name() -> None:
    assert set(STEP_LABELS) == set(EventType)


def test_an_assessment_line_becomes_its_verdict_and_size() -> None:
    skipped = outcome_of(EventType.ASSESSMENT_FINISHED, "skip (L/interface) for $0.03 [ok]")
    executed = outcome_of(EventType.ASSESSMENT_FINISHED, "execute (XS/docs) for $0.04 [ok]")

    assert skipped == Outcome(text="skip", tone=Tone.MUTED, detail="L · interface")
    assert executed == Outcome(text="do it", tone=Tone.POSITIVE, detail="XS · docs")


def test_the_line_the_triage_writes_is_the_line_the_page_reads(tmp_path: Path) -> None:
    run_directory = create_run_directory(tmp_path, RUN_ID)
    record_assessment(
        run_directory,
        AssessmentOutcome(
            issue_number=3,
            assessment=build_assessment(Verdict.NEEDS_INPUT, Effort.S, Risk.BEHAVIOUR, [], []),
            outcome=ClaudeOutcome.OK,
            cost_usd=0.04,
            session_id=None,
        ),
    )

    [written] = read_run_events(run_directory)
    step = demo_step(written, RUN_ID, WARSAW)

    assert step.issue_number == 3
    assert step.outcome == Outcome(text="ask first", tone=Tone.ATTENTION, detail="S · behaviour")


def test_a_step_reads_its_time_on_the_policy_clock() -> None:
    step = demo_step(event(EventType.RUN_STARTED, "owner/repo as owner", None), RUN_ID, WARSAW)

    assert step.time == "14:33:07"
    assert step.step == "Run started"


def test_the_triage_summary_counts_issues_and_leaves_the_spend_out() -> None:
    summary = outcome_of(EventType.RUN_FINISHED, "7 tasks, $0.18 spent")

    assert summary.text == "7 issues reviewed"
    assert "$" not in summary.text


def test_the_checks_and_hidden_tests_read_as_passed_or_failed() -> None:
    assert outcome_of(EventType.GATE_FINISHED, "failed, 12 changed lines") == Outcome(
        text="failed", tone=Tone.NEGATIVE, detail="12 lines changed"
    )
    assert outcome_of(EventType.ACCEPTANCE_FINISHED, "exit 0").text == "passed"
    assert outcome_of(EventType.ACCEPTANCE_FINISHED, "exit 1").tone is Tone.NEGATIVE


def test_the_worker_and_the_pull_request_read_in_plain_words() -> None:
    worker = outcome_of(EventType.WORKER_FINISHED, "done for $0.31 [ok], 0 denials")
    pull_request = outcome_of(EventType.PULL_REQUEST_OPENED, "board://owner/repo/pull/12")

    assert worker == Outcome(text="done", tone=Tone.POSITIVE, detail=None)
    assert pull_request.text == "PR #12"


def test_a_skip_reason_is_said_briefly() -> None:
    reason = outcome_of(EventType.TASK_SKIPPED, OUTSIDE_LIMITS_REASON)

    assert reason.text == "outside the agent's limits"


def test_a_wave_and_an_overlap_read_as_the_run_wrote_them() -> None:
    started = event(EventType.WAVE_STARTED, "wave 1: #5, #7", None)
    wave = demo_step(started, RUN_ID, WARSAW)
    overlap = outcome_of(EventType.OVERLAP_FOUND, "#5 and #7 both changed docs/index.md")

    assert wave.step == "Wave started"
    assert wave.outcome == Outcome(text="wave 1: #5, #7", tone=Tone.PLAIN, detail=None)
    assert overlap.tone is Tone.ATTENTION


def test_a_run_that_takes_over_a_prepared_triage_says_so() -> None:
    adopted = outcome_of(EventType.RUN_STARTED, ADOPTED_NOTE_TEMPLATE.format(run_id=RUN_ID))
    fresh = outcome_of(EventType.RUN_STARTED, "owner/repo as owner")

    assert adopted.text == "picks up the prepared triage"
    assert fresh.text == ""


def test_a_detail_the_page_has_no_reading_for_is_shown_as_written() -> None:
    assert outcome_of(EventType.GATE_FINISHED, "skipped by hand") == Outcome(
        text="skipped by hand", tone=Tone.PLAIN, detail=None
    )


def test_a_filtered_issue_says_why() -> None:
    never = build_task(
        5,
        "Move the chat service to the company cloud",
        TaskStatus.INELIGIBLE,
        EligibilityDecision(eligible=False, reasons=[IneligibilityReason.NEVER_LABEL]),
        None,
    )

    assert demo_task(never).outcome == Outcome(
        text="filtered out", tone=Tone.MUTED, detail="marked weekend:never"
    )


def test_an_assessed_issue_shows_what_the_agent_decided() -> None:
    assert demo_task(task_with(TaskStatus.ASSESSED, Verdict.NEEDS_INPUT)).outcome.text == (
        "needs input"
    )
    assert demo_task(task_with(TaskStatus.ASSESSED, Verdict.EXECUTE)).outcome.text == "to do"


def test_only_a_web_address_becomes_a_pull_request_link() -> None:
    delivered = task_with(TaskStatus.REVIEW, Verdict.EXECUTE)
    on_github = demo_task(delivered.model_copy(update={"pull_request_url": PULL_REQUEST_URL}))
    on_the_board = demo_task(
        delivered.model_copy(update={"pull_request_url": "board://owner/repo/pull/12"})
    )

    assert on_github.link == PULL_REQUEST_URL
    assert on_github.outcome.text == "ready for review"
    assert on_the_board.link is None


def test_the_demo_page_polls_its_body_every_five_seconds(tmp_path: Path) -> None:
    policy, client = client_for(tmp_path)
    seeded_run(policy, RUN_ID, "5 of 7 issues survived the pre-filter")

    response = client.get("/demo")

    assert response.status_code == 200
    assert f"setInterval(refreshDemo, {DEMO_REFRESH_SECONDS * 1000})" in response.text
    assert "5 of 7 issues eligible" in response.text


def test_the_demo_body_is_a_fragment_with_steps_and_tasks_and_no_spend(tmp_path: Path) -> None:
    policy, client = client_for(tmp_path)
    seeded_run(policy, RUN_ID, "5 of 7 issues survived the pre-filter")

    body = client.get("/demo/body").text

    assert "<html" not in body
    assert "Backlog filtered" in body
    assert "The New chat button does nothing" in body
    assert "$" not in body


def test_the_demo_follows_the_newest_run(tmp_path: Path) -> None:
    policy, client = client_for(tmp_path)
    seeded_run(policy, RUN_ID, "5 of 7 issues survived the pre-filter")
    seeded_run(policy, LATER_RUN_ID, "3 of 7 issues survived the pre-filter")

    body = client.get("/demo/body").text

    assert "3 of 7 issues eligible" in body
    assert "5 of 7 issues eligible" not in body


def test_the_demo_waits_for_a_first_run_and_keeps_polling(tmp_path: Path) -> None:
    _, client = client_for(tmp_path)

    response = client.get("/demo")

    assert response.status_code == 200
    assert "Waiting for the first run." in response.text
    assert "setInterval(refreshDemo" in response.text


def test_the_demo_of_a_run_nobody_has_heard_of_is_missing(tmp_path: Path) -> None:
    _, client = client_for(tmp_path)

    assert client.get("/demo?run_id=20260101-000000-pilot").status_code == 404
    assert client.get("/demo/body?run_id=20260101-000000-pilot").status_code == 404


def test_a_living_run_shows_as_live_with_what_it_is_doing(tmp_path: Path) -> None:
    policy, client = client_for(tmp_path)
    run_directory = seeded_run(policy, RUN_ID, "5 of 7 issues survived the pre-filter")
    working = demo_state(RUN_ID, [task_with(TaskStatus.WORKING, Verdict.EXECUTE)])
    save_run_state(run_directory, working.model_copy(update={"phase": RunPhase.EXECUTE}))
    with orchestrator_stand_in() as pid:
        activity = build_activity(ActivityKind.WORKING, 1, None, datetime.now(UTC), None)
        write_pulse(run_directory, build_pulse(pid, current_boot_id(), datetime.now(UTC), activity))

        body = client.get("/demo/body").text

    assert ">live</span>" in body
    assert "now: working on #1" in body
    assert ">working</span>" in body
