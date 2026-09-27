from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.unit.conftest import (
    assessment_payload,
    claude_response,
    issue_payload,
    usage_event,
    write_claude_responses,
    write_github_data,
    write_probe_plan,
    write_test_policy,
)
from weekend_loop.cli import EXIT_BLOCKED, main
from weekend_loop.models import RunState, TaskStatus
from weekend_loop.policy import policy_at
from weekend_loop.preflight import ABSENT_COMMIT, PROBE_REFERENCE
from weekend_loop.runs import latest_run_id, open_run_directory

TEMPLATE_BODY = (
    "## Business requirement\nThe harbour log misreports vessel speed.\n\n"
    "## Goal\nParse an empty speed field as unknown instead of crashing.\n\n"
    "## Scope\nTouch `src/module.py` only; leave the report renderer alone.\n\n"
    "## Acceptance criteria\n- [ ] an empty speed field yields no reading\n"
    "- [ ] the existing suite stays green\n"
)
READ_ONLY_VERBS = {("issue", "list"), ("issue", "view"), ("pr", "list"), ("--version",)}


def seed_github(fake_binaries: Path) -> None:
    write_github_data(
        fake_binaries,
        issues=[
            issue_payload(1, "Empty speed field crashes the parser", TEMPLATE_BODY, ["bug"], []),
            issue_payload(2, "Improve the summary", TEMPLATE_BODY, [], []),
            issue_payload(3, "Move to a monorepo", TEMPLATE_BODY, ["weekend:never"], []),
            issue_payload(4, "Fix it", "too short to assess", [], []),
            issue_payload(5, "CSV export", TEMPLATE_BODY, [], []),
        ],
        pull_requests=[
            {
                "number": 12,
                "title": "CSV export",
                "body": "Closes #5",
                "headRefName": "feat/csv-export",
                "author": {"login": "colleague"},
                "url": "https://github.com/owner/repo/pull/12",
            }
        ],
        comments={},
        push=False,
    )


def seed_claude(fake_binaries: Path) -> None:
    write_claude_responses(
        fake_binaries,
        {
            "1": claude_response(assessment_payload("execute", "XS", "tests", [], []), 0.02),
            "2": claude_response(
                assessment_payload(
                    "needs_input", "S", "behaviour", ["unclear_goal"], ["Which column is speed?"]
                ),
                0.03,
            ),
            "default": claude_response(assessment_payload("skip", "L", "interface", [], []), 0.01),
        },
    )


def prepare(tmp_path: Path, fake_binaries: Path, weekly_reset_at: datetime | None) -> Path:
    seed_github(fake_binaries)
    seed_claude(fake_binaries)
    return write_test_policy(tmp_path, weekly_reset_at)


def read_calls(path: Path) -> list[list[str]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_a_dry_run_assesses_the_survivors_and_writes_a_plan(
    tmp_path: Path, fake_binaries: Path, fake_git: Path
) -> None:
    policy_path = prepare(tmp_path, fake_binaries, datetime.now(UTC) + timedelta(days=2))
    assert main(["--home", str(policy_path), "triage", "--repo-key", "dryrun"]) == 0
    policy = policy_at(policy_path)
    run_directory = open_run_directory(policy.state_dir, latest_run_id(policy.state_dir))
    state = RunState.model_validate_json(run_directory.run_state_path.read_text())
    statuses = {task.issue_number: task.status for task in state.tasks}
    assert statuses == {
        1: TaskStatus.ASSESSED,
        2: TaskStatus.ASSESSED,
        3: TaskStatus.INELIGIBLE,
        4: TaskStatus.INELIGIBLE,
        5: TaskStatus.INELIGIBLE,
    }
    assert state.spent_usd == 0.05
    plan = run_directory.plan_path.read_text()
    assert "| #1 | execute | XS | tests |" in plan
    assert "Which column is speed?" in plan
    assert "open_linked_pull_request" in plan


def test_every_assessment_is_recorded_with_its_own_file_and_event(
    tmp_path: Path, fake_binaries: Path, fake_git: Path
) -> None:
    policy_path = prepare(tmp_path, fake_binaries, datetime.now(UTC) + timedelta(days=2))
    main(["--home", str(policy_path), "triage", "--repo-key", "dryrun"])
    policy = policy_at(policy_path)
    run_directory = open_run_directory(policy.state_dir, latest_run_id(policy.state_dir))
    assert (run_directory.task_directory(1) / "assessment.json").is_file()
    assert (run_directory.task_directory(2) / "assessment.json").is_file()
    assert not run_directory.task_directory(3).exists()
    events = [
        json.loads(line)["event"] for line in run_directory.events_path.read_text().splitlines()
    ]
    assert events == [
        "run_started",
        "prefilter_finished",
        "assessment_finished",
        "assessment_finished",
        "run_finished",
    ]


WRITE_PROBE = [
    "api",
    "-X",
    "POST",
    "repos/example-org/example-repo/git/refs",
    "-f",
    f"ref={PROBE_REFERENCE}",
    "-f",
    f"sha={ABSENT_COMMIT}",
]


def test_a_dry_run_only_reads_from_github_and_fences_every_assessment(
    tmp_path: Path, fake_binaries: Path, fake_git: Path
) -> None:
    policy_path = prepare(tmp_path, fake_binaries, datetime.now(UTC) + timedelta(days=2))
    main(["--home", str(policy_path), "triage", "--repo-key", "dryrun"])
    for call in read_calls(fake_binaries / "gh-calls.jsonl"):
        if call[0] == "api":
            reads = ("user", "repos/example-org/example-repo")
            assert call[1] in reads or call == WRITE_PROBE, call
        else:
            assert tuple(call[:2]) in READ_ONLY_VERBS, call
    assessments = [
        call
        for call in read_calls(fake_binaries / "claude-calls.jsonl")
        if "-p" in call and not call[call.index("-p") + 1].startswith("Reply with")
    ]
    assert len(assessments) == 2
    for call in assessments:
        assert "--restricted" in call
        assert call[call.index("--tools") + 1] == "Read,Grep,Glob"
        assert call[call.index("--settings") + 1].endswith("assessor.settings.json")


def test_a_spent_weekly_allowance_refuses_to_start_a_run(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy_path = prepare(tmp_path, fake_binaries, None)
    write_probe_plan(
        fake_binaries,
        usage_event(
            0.10,
            0.99,
            datetime.now(UTC) + timedelta(hours=2),
            datetime.now(UTC) + timedelta(days=2),
        ),
        0.001,
    )
    assert main(["--home", str(policy_path), "triage", "--repo-key", "dryrun"]) == EXIT_BLOCKED
    assert not (policy_at(policy_path).state_dir / "runs").exists()


def test_the_candidate_sheet_lists_the_survivors_for_blind_labelling(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy_path = prepare(tmp_path, fake_binaries, datetime.now(UTC) + timedelta(days=2))
    assert main(["--home", str(policy_path), "candidates", "--repo-key", "dryrun"]) == 0
    sheet = policy_at(policy_path).state_dir / "dryrun" / "dryrun-labels.csv"
    rows = sheet.read_text().splitlines()
    assert rows[0] == "issue_number,label,expected_blocker,title"
    assert [row.split(",")[0] for row in rows[1:]] == ["1", "2"]
