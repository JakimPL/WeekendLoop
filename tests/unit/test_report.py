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
    SpecSignals,
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


def worked_state(stacked_on: dict[int, int | None]) -> RunState:
    tasks = [
        build_task(
            number,
            f"issue {number}",
            TaskStatus.REVIEW,
            ELIGIBLE,
            build_assessment(Verdict.EXECUTE, Effort.XS, Risk.TESTS, [], []),
        ).model_copy(update={"stacked_on": parent})
        for number, parent in stacked_on.items()
    ]
    return build_run_state(tasks, 2.4, [], "20260918-210000-demo", "demo", RepoMode.EXECUTE)


def test_the_digest_names_the_order_a_stack_merges_in() -> None:
    digest = render_digest(worked_state({5: None, 11: 5, 13: 11, 7: None}), SLUG)
    section = digest.split("## Merge order")[1]
    assert "- #5 → #11 → #13: each builds on the one before it; merge them in this order" in section
    assert "#7" not in section.split("##")[0]


def test_a_run_without_stacks_or_shared_files_has_no_merge_order() -> None:
    assert "## Merge order" not in render_digest(worked_state({12: None}), SLUG)


def test_the_digest_asks_for_care_only_where_git_cannot_merge_two_branches() -> None:
    state = worked_state({12: None, 13: None, 14: None})
    first, second, third = state.tasks
    paired = state.model_copy(
        update={
            "tasks": [
                first.model_copy(
                    update={
                        "overlaps": [
                            Overlap(issue_number=13, paths=["a.py"]),
                            Overlap(issue_number=14, paths=["b.py"], merges_cleanly=True),
                        ]
                    }
                ),
                second.model_copy(update={"overlaps": [Overlap(issue_number=12, paths=["a.py"])]}),
                third.model_copy(
                    update={
                        "overlaps": [Overlap(issue_number=12, paths=["b.py"], merges_cleanly=True)]
                    }
                ),
            ]
        }
    )
    digest = render_digest(paired, SLUG)
    care = digest.split("## Merge with care")[1]
    assert "- #12 and #13 both changed a.py: git cannot merge them on its own" in care
    assert care.count("#12 and #13") == 1
    order = digest.split("## Merge order")[1].split("## Merge with care")[0]
    assert "- #12 and #14 both changed b.py: git merges them cleanly" in order
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
    state = worked_state({12: None})
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


def test_the_plan_says_what_each_issue_builds_on_and_who_said_so() -> None:
    state = build_state()
    linked = state.tasks[2].model_copy(
        update={
            "spec_signals": SpecSignals(
                body_length=10,
                sections_present=[],
                has_template=False,
                referenced_paths=[],
                resolved_paths=[],
                has_acceptance_criteria=False,
                blocked_by=[5],
            ),
            "assessment": state.tasks[2].assessment.model_copy(update={"depends_on": [5, 9]})
            if state.tasks[2].assessment is not None
            else None,
        }
    )
    plan = render_triage_plan(
        state.model_copy(update={"tasks": [*state.tasks[:2], linked, state.tasks[3]]}), SLUG
    )
    assert (
        "Builds on: #5 (linked on GitHub), "
        "#9 (found by the assessor; link it on GitHub to keep it)" in plan
    )
