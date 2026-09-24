from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from weekend_loop.models import EligibilityPolicy, IneligibilityReason, Issue, LabelPolicy
from weekend_loop.prefilter import evaluate_eligibility, spec_signals

OWNER = "owner-login"
NOW = datetime(2026, 9, 18, 21, 0, tzinfo=UTC)
LABELS = LabelPolicy(
    auto="weekend:auto",
    approved="weekend:approved",
    never="weekend:never",
    review="weekend:review",
    needs_input="weekend:needs-input",
    unfinished="weekend:unfinished",
)
ELIGIBILITY = EligibilityPolicy(
    minimum_body_length=200,
    excluded_title_pattern="EPIC|AREA|SPIKE",
    foreign_activity_window_hours=48,
)
TEMPLATE_BODY = (
    "## Business requirement\nThe harbour log misreports speed.\n\n"
    "## Goal\nParse an empty speed field as unknown.\n\n"
    "## Scope\nTouch `src/module.py` and `src/missing.py` only.\n\n"
    "## Acceptance criteria\n- [ ] the parser returns None for an empty field\n"
) + "Extra context so the body clears the minimum length. " * 4


def build_issue(
    number: int,
    title: str,
    body: str,
    labels: list[str],
    assignees: list[str],
    linked_pull_requests: list[int],
    last_foreign_activity_at: datetime | None,
) -> Issue:
    return Issue(
        number=number,
        title=title,
        body=body,
        labels=labels,
        assignees=assignees,
        milestone=None,
        author=OWNER,
        created_at=NOW - timedelta(days=30),
        updated_at=NOW - timedelta(days=1),
        url=f"https://github.com/owner/repo/issues/{number}",
        open_linked_pull_requests=linked_pull_requests,
        last_foreign_activity_at=last_foreign_activity_at,
    )


def default_issue() -> Issue:
    return build_issue(1, "Speed field is misparsed", TEMPLATE_BODY, [], [], [], None)


def test_a_well_specified_unassigned_issue_is_eligible() -> None:
    decision = evaluate_eligibility(default_issue(), ELIGIBILITY, LABELS, OWNER, NOW)
    assert decision.eligible
    assert decision.reasons == []


def test_every_policy_rule_names_its_own_reason() -> None:
    cases = [
        (
            build_issue(2, "Speed", TEMPLATE_BODY, [], ["colleague"], [], None),
            IneligibilityReason.ASSIGNED_TO_SOMEONE_ELSE,
        ),
        (
            build_issue(3, "Speed", TEMPLATE_BODY, ["weekend:never"], [], [], None),
            IneligibilityReason.NEVER_LABEL,
        ),
        (
            build_issue(4, "Speed", "too short", [], [], [], None),
            IneligibilityReason.BODY_TOO_SHORT,
        ),
        (
            build_issue(5, "[EPIC] Rework everything", TEMPLATE_BODY, [], [], [], None),
            IneligibilityReason.TITLE_PATTERN,
        ),
        (
            build_issue(6, "Speed", TEMPLATE_BODY, [], [], [42], None),
            IneligibilityReason.OPEN_LINKED_PULL_REQUEST,
        ),
        (
            build_issue(7, "Speed", TEMPLATE_BODY, [], [], [], NOW - timedelta(hours=2)),
            IneligibilityReason.RECENT_FOREIGN_ACTIVITY,
        ),
    ]
    for issue, expected in cases:
        decision = evaluate_eligibility(issue, ELIGIBILITY, LABELS, OWNER, NOW)
        assert decision.reasons == [expected], issue.number
        assert not decision.eligible


def test_an_issue_assigned_to_the_operator_stays_eligible() -> None:
    issue = build_issue(8, "Speed", TEMPLATE_BODY, [], [OWNER], [], None)
    assert evaluate_eligibility(issue, ELIGIBILITY, LABELS, OWNER, NOW).eligible


def test_activity_older_than_the_window_leaves_the_issue_eligible() -> None:
    issue = build_issue(9, "Speed", TEMPLATE_BODY, [], [], [], NOW - timedelta(hours=72))
    assert evaluate_eligibility(issue, ELIGIBILITY, LABELS, OWNER, NOW).eligible


def test_spec_signals_separate_paths_that_exist_from_paths_that_do_not(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "module.py").write_text("value = 1\n")
    signals = spec_signals(default_issue(), tmp_path)
    assert signals.has_template
    assert signals.has_acceptance_criteria
    assert signals.referenced_paths == ["src/module.py", "src/missing.py"]
    assert signals.resolved_paths == ["src/module.py"]
    assert "Business requirement" in signals.sections_present


def test_a_free_text_issue_shows_no_template(tmp_path: Path) -> None:
    issue = build_issue(10, "Speed", "Please fix the speed parsing, thanks.", [], [], [], None)
    signals = spec_signals(issue, tmp_path)
    assert not signals.has_template
    assert not signals.has_acceptance_criteria
    assert signals.referenced_paths == []
