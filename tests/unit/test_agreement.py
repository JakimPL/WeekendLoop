from __future__ import annotations

from pathlib import Path

from tests.unit.conftest import (
    ELIGIBLE,
    NEVER_LABELLED,
    build_assessment,
    build_run_state,
    build_task,
)
from tests.unit.test_prefilter import build_issue
from weekend_loop.agreement import (
    load_blind_labels,
    render_agreement,
    score_agreement,
    write_label_template,
)
from weekend_loop.models import (
    BlindLabel,
    BlindLabelEntry,
    Blocker,
    Effort,
    Issue,
    RepoMode,
    Risk,
    RunState,
    TaskStatus,
    Verdict,
)


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
            "Empty speed field",
            TaskStatus.ASSESSED,
            ELIGIBLE,
            build_assessment(Verdict.EXECUTE, Effort.XS, Risk.TESTS, [], []),
        ),
        build_task(13, "Move to a monorepo", TaskStatus.INELIGIBLE, NEVER_LABELLED, None),
    ]
    return build_run_state(tasks, 0.3, [], "20260918-210000-dryrun", "dryrun", RepoMode.DRY_RUN)


def build_entries() -> list[BlindLabelEntry]:
    return [
        BlindLabelEntry(
            issue_number=10, label=BlindLabel.NEVER, expected_blocker=Blocker.NEEDS_EXTERNAL_DATA
        ),
        BlindLabelEntry(issue_number=11, label=BlindLabel.NEVER, expected_blocker=None),
        BlindLabelEntry(issue_number=13, label=BlindLabel.NEVER, expected_blocker=None),
    ]


def test_the_sheet_round_trips_through_the_operator(tmp_path: Path) -> None:
    issues: list[Issue] = [
        build_issue(10, "Ingest from S3", "body", [], [], [], None),
        build_issue(11, "Empty speed field", "body", [], [], [], None),
    ]
    sheet = tmp_path / "labels.csv"
    write_label_template(sheet, issues)
    assert load_blind_labels(sheet) == []
    rows = sheet.read_text().replace("10,,,", "10,never,needs_external_data,")
    sheet.write_text(rows)
    entries = load_blind_labels(sheet)
    assert entries == [
        BlindLabelEntry(
            issue_number=10, label=BlindLabel.NEVER, expected_blocker=Blocker.NEEDS_EXTERNAL_DATA
        )
    ]


def test_agreement_counts_the_two_way_judgement_and_names_the_gaps() -> None:
    report = score_agreement(build_state(), build_entries())
    assert report.compared_count == 3
    assert report.agreed_count == 2
    assert report.execute_on_never == [11]
    assert [gap.issue_number for gap in report.disagreements] == [11]
    assert report.disagreements[0].agent_label is BlindLabel.NOT_NEVER
    assert report.blocker_matched == 1
    assert report.blocker_compared == 1


def test_an_unassessed_issue_stays_out_of_the_comparison() -> None:
    entries = [
        *build_entries(),
        BlindLabelEntry(issue_number=99, label=BlindLabel.NOT_NEVER, expected_blocker=None),
    ]
    report = score_agreement(build_state(), entries)
    assert report.labelled_count == 4
    assert report.compared_count == 3


def test_the_summary_states_the_rate_and_the_disagreements() -> None:
    rendered = render_agreement(score_agreement(build_state(), build_entries()))
    assert "never vs not-never agreement: 67% (2/3)" in rendered
    assert "#11: operator never, agent not-never (execute)" in rendered
