from __future__ import annotations

from tests.unit.conftest import (
    ELIGIBLE,
    NEVER_LABELLED,
    build_assessment,
    build_run_state,
    build_task,
)
from weekend_loop.models import (
    BaselineResult,
    Blocker,
    CommandResult,
    Effort,
    GateResult,
    Overlap,
    RepoMode,
    Risk,
    RunState,
    SoloReason,
    TaskStatus,
    Verdict,
)
from weekend_loop.report import assessed_tasks, render_digest, render_triage_plan

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


def worked_state(waves: list[tuple[int, int | None, SoloReason | None]]) -> RunState:
    tasks = [
        build_task(
            number,
            f"issue {number}",
            TaskStatus.REVIEW,
            ELIGIBLE,
            build_assessment(Verdict.EXECUTE, Effort.XS, Risk.TESTS, [], []),
        ).model_copy(update={"wave": wave, "solo_reason": reason})
        for number, wave, reason in waves
    ]
    return build_run_state(tasks, 2.4, [], "20260918-210000-demo", "demo", RepoMode.EXECUTE)


def test_the_digest_lists_the_waves_and_why_a_task_ran_alone() -> None:
    digest = render_digest(
        worked_state(
            [
                (12, 1, None),
                (13, 1, None),
                (11, 2, SoloReason.SHARED_PATH),
                (14, 3, SoloReason.NO_TOUCHED_PATHS),
            ]
        ),
        SLUG,
    )
    waves_section = digest.split("## Waves")[1]
    assert "- wave 1: #12, #13" in waves_section
    assert "- wave 2: #11 (alone: it touches a shared path)" in waves_section
    assert "- wave 3: #14 (alone: its assessment names no paths)" in waves_section


def test_a_run_that_worked_one_task_at_a_time_has_no_waves_to_list() -> None:
    assert "## Waves" not in render_digest(worked_state([(12, None, None)]), SLUG)


def test_the_digest_asks_for_care_where_two_branches_changed_the_same_file() -> None:
    state = worked_state([(12, 1, None), (13, 1, None)])
    first, second = state.tasks
    paired = state.model_copy(
        update={
            "tasks": [
                first.model_copy(update={"overlaps": [Overlap(issue_number=13, paths=["a.py"])]}),
                second.model_copy(update={"overlaps": [Overlap(issue_number=12, paths=["a.py"])]}),
            ]
        }
    )
    digest = render_digest(paired, SLUG)
    section = digest.split("## Merge with care")[1].split("## Waves")[0]
    assert "- #12 and #13 both changed a.py: merge them one at a time" in section
    assert section.count("#12 and #13") == 1
    assert "## Merge with care" not in render_digest(state, SLUG)


def gate_command(command: str, peak_gb: float | None, capped: bool) -> CommandResult:
    return CommandResult(
        command=command,
        exit_code=137 if capped else 0,
        duration_seconds=1.0,
        output_tail="",
        peak_memory_gb=peak_gb,
        stopped_at_memory_cap=capped,
    )


def test_the_digest_names_the_memory_each_gate_took_and_what_hit_its_cap() -> None:
    state = worked_state([(12, None, None)])
    capped_gate = GateResult(
        passed=False,
        commands=[gate_command("pytest -q", 9.9, True)],
        diff_lines=4,
        forbidden_paths_touched=[],
        secret_matches=[],
        binary_files=[],
        commit_count=1,
        changed_paths=["a.py"],
    )
    measured = state.model_copy(
        update={
            "baseline": BaselineResult(
                commit="abc", passed=True, commands=[gate_command("pytest -q", 7.25, False)]
            ),
            "tasks": [state.tasks[0].model_copy(update={"gate": capped_gate})],
        }
    )
    section = render_digest(measured, SLUG).split("## Memory")[1]
    assert "- the base branch: gate peak 7.2 GB" in section
    assert "- #12: gate peak 9.9 GB; `pytest -q` stopped at its memory cap" in section
    assert "## Memory" not in render_digest(state, SLUG)
