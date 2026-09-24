from __future__ import annotations

from tests.unit.conftest import (
    ELIGIBLE,
    NEVER_LABELLED,
    build_assessment,
    build_run_state,
    build_task,
)
from weekend_loop.models import Blocker, Effort, RepoMode, Risk, RunState, TaskStatus, Verdict
from weekend_loop.report import assessed_tasks, render_triage_plan

SLUG = "owner/repo"


def build_state() -> RunState:
    tasks = [
        build_task(
            10,
            "Ingest from S3",
            TaskStatus.ASSESSED,
            ELIGIBLE,
            build_assessment(
                Verdict.SKIP, Effort.L, Risk.INTERFACE, [Blocker.NEEDS_EXTERNAL_DATA], []
            ),
        ),
        build_task(
            11,
            "Improve the summary",
            TaskStatus.ASSESSED,
            ELIGIBLE,
            build_assessment(
                Verdict.NEEDS_INPUT,
                Effort.S,
                Risk.BEHAVIOUR,
                [Blocker.UNCLEAR_GOAL],
                ["Which column holds the speed?"],
            ),
        ),
        build_task(
            12,
            "Empty speed field crashes the parser",
            TaskStatus.ASSESSED,
            ELIGIBLE,
            build_assessment(Verdict.EXECUTE, Effort.XS, Risk.TESTS, [], []),
        ),
        build_task(13, "Move to a monorepo | now", TaskStatus.INELIGIBLE, NEVER_LABELLED, None),
    ]
    return build_run_state(
        tasks,
        0.3,
        ["triage stopped early: budget"],
        "20260918-210000-dryrun",
        "dryrun",
        RepoMode.DRY_RUN,
    )


def test_candidates_are_ranked_by_verdict_then_effort() -> None:
    assert [task.issue_number for task in assessed_tasks(build_state())] == [12, 11, 10]


def test_the_plan_states_the_run_the_spend_and_the_verdicts() -> None:
    plan = render_triage_plan(build_state(), SLUG)
    assert f"# Triage plan — {SLUG}" in plan
    assert "- spend: $0.30 of $15.00" in plan
    assert "- note: triage stopped early: budget" in plan
    assert "| #12 | execute | XS | tests | high | — |" in plan


def test_the_plan_carries_the_questions_and_the_plans_worth_reading() -> None:
    plan = render_triage_plan(build_state(), SLUG)
    questions_section = plan.split("## Questions it would ask")[1]
    assert "Which column holds the speed?" in questions_section
    actions_section = plan.split("## What it would do")[1].split("## Questions")[0]
    assert "#12" in actions_section
    assert "#10" not in actions_section


def test_issues_filtered_before_assessment_are_listed_with_their_reason() -> None:
    plan = render_triage_plan(build_state(), SLUG)
    filtered_section = plan.split("## Filtered out before assessment")[1]
    assert "| #13 | never_label | Move to a monorepo \\| now |" in filtered_section
