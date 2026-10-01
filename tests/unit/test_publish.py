from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.unit.conftest import (
    ELIGIBLE,
    build_assessment,
    build_task,
    delivery_payload,
    write_test_policy,
)
from tests.unit.test_execute import (
    FIXED_RECORDS,
    RUN_ID,
    approved_delivery,
    execute,
    prepare,
    run_git,
)
from weekend_loop.cli import EXIT_BLOCKED, main
from weekend_loop.github import (
    PULL_REQUEST_URL_PATTERN,
    push_refspec,
    signed,
    url_from_output,
)
from weekend_loop.guards import assert_branch_allowed, assert_labels_allowed
from weekend_loop.models import Effort, Overlap, Policy, Risk, TaskStatus, Verdict
from weekend_loop.publish import (
    publishable,
    pull_request_body,
    pull_request_title,
    question_comment,
)
from weekend_loop.questions import QUESTION_MARKER, is_agent_comment
from weekend_loop.runs import load_run_state, open_run_directory

FOOTER = "— weekend-loop run {run_id}"
NAMESPACE = "weekend:"


def read_bodies(fake_binaries: Path) -> list[dict[str, Any]]:
    path = fake_binaries / "gh-bodies.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []


def gh_calls(fake_binaries: Path) -> list[list[str]]:
    return [
        json.loads(line) for line in (fake_binaries / "gh-calls.jsonl").read_text().splitlines()
    ]


def test_a_push_stays_inside_the_agents_branch_namespace() -> None:
    assert_branch_allowed("weekend/1-empty-speed", "weekend/")
    with pytest.raises(ValueError, match="outside"):
        assert_branch_allowed("main", "weekend/")
    assert push_refspec("weekend/1-x") == "refs/heads/weekend/1-x:refs/heads/weekend/1-x"


def test_a_label_write_stays_inside_the_agents_namespace() -> None:
    assert_labels_allowed(["weekend:review", "weekend:needs-input"], NAMESPACE)
    with pytest.raises(ValueError, match="outside"):
        assert_labels_allowed(["bug"], NAMESPACE)


def test_a_signed_comment_names_the_run() -> None:
    assert signed("body", FOOTER, "run-7").endswith("— weekend-loop run run-7\n")


def test_a_signed_comment_carries_the_hidden_agent_marker() -> None:
    assert is_agent_comment(signed("body", FOOTER, "run-7"))


def test_a_url_is_taken_from_what_gh_printed() -> None:
    printed = "Creating pull request\nhttps://github.com/owner/repo/pull/42\n"
    assert url_from_output(printed, PULL_REQUEST_URL_PATTERN) == (
        "https://github.com/owner/repo/pull/42"
    )
    with pytest.raises(ValueError, match="no url"):
        url_from_output("something went wrong", PULL_REQUEST_URL_PATTERN)


def publish(policy_path: Path) -> int:
    arguments = ["--home", str(policy_path), "publish", "--repo-key", "demo", "--run-id", RUN_ID]
    return main(arguments)


def origin_of(policy: Policy) -> Path:
    remote = policy.repos["demo"].remote_url
    assert remote is not None
    return Path(remote)


def test_a_gated_branch_reaches_github_as_a_draft_pull_request(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path,
        fake_binaries,
        ["weekend:auto"],
        {"logbook/records.py": FIXED_RECORDS},
        approved_delivery(),
        3,
        [1],
    )
    assert execute(policy_path) == 0
    assert publish(policy_path) == 0
    state = load_run_state(open_run_directory(policy.state_dir, RUN_ID))
    assert state.tasks[0].pull_request_url == "https://github.com/owner/repo/pull/42"
    assert "weekend/1-empty-speed-field" in run_git(["branch", "--list"], origin_of(policy))
    verbs = [tuple(call[:2]) for call in gh_calls(fake_binaries)]
    assert verbs.index(("pr", "create")) < verbs.index(("issue", "edit"))
    assert ("issue", "create") in verbs


def test_the_pull_request_leaves_the_issue_state_to_the_human(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path,
        fake_binaries,
        ["weekend:auto"],
        {"logbook/records.py": FIXED_RECORDS},
        approved_delivery(),
        3,
        [1],
    )
    execute(policy_path)
    publish(policy_path)
    bodies = read_bodies(fake_binaries)
    pull_request = next(body for body in bodies if body["arguments"][:2] == ["pr", "create"])
    assert "Refs #1" in pull_request["body"]
    assert "Closes #1" not in pull_request["body"]
    assert "weekend-loop run" in pull_request["body"]
    assert "--draft" in pull_request["arguments"]


def test_only_labels_in_the_agents_namespace_are_written(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path,
        fake_binaries,
        ["weekend:auto"],
        {"logbook/records.py": FIXED_RECORDS},
        approved_delivery(),
        3,
        [1],
    )
    execute(policy_path)
    publish(policy_path)
    edits = [call for call in gh_calls(fake_binaries) if call[:2] == ["issue", "edit"]]
    written = [call[call.index("--add-label") + 1] for call in edits if "--add-label" in call]
    removed = [call[call.index("--remove-label") + 1] for call in edits if "--remove-label" in call]
    assert written == ["weekend:review"]
    assert removed == ["weekend:needs-input"]


def test_a_question_is_published_as_a_comment_and_a_label(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path,
        fake_binaries,
        ["weekend:auto"],
        {"logbook/records.py": FIXED_RECORDS},
        delivery_payload("needs_input", "", ["Which unit does the feed use?"]),
        3,
        [1],
    )
    execute(policy_path)
    assert publish(policy_path) == 0
    verbs = [tuple(call[:2]) for call in gh_calls(fake_binaries)]
    assert ("pr", "create") not in verbs
    written = [
        call[call.index("--add-label") + 1]
        for call in gh_calls(fake_binaries)
        if call[:2] == ["issue", "edit"]
    ]
    assert written == ["weekend:needs-input"]
    comment = next(
        body for body in read_bodies(fake_binaries) if body["arguments"][:2] == ["issue", "comment"]
    )
    assert "Which unit does the feed use?" in comment["body"]


def test_a_branch_the_gate_refused_is_never_offered(tmp_path: Path, fake_binaries: Path) -> None:
    policy, policy_path = prepare(
        tmp_path,
        fake_binaries,
        ["weekend:auto"],
        {".github/workflows/evil.yml": "on: push\n"},
        approved_delivery(),
        3,
        [1],
    )
    execute(policy_path)
    assert publish(policy_path) == 0
    verbs = [tuple(call[:2]) for call in gh_calls(fake_binaries)]
    assert ("pr", "create") not in verbs
    assert "weekend/1-empty-speed-field" not in run_git(["branch", "--list"], origin_of(policy))


def test_the_digest_names_the_delivery_and_lands_on_disk(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path,
        fake_binaries,
        ["weekend:auto"],
        {"logbook/records.py": FIXED_RECORDS},
        approved_delivery(),
        3,
        [1],
    )
    execute(policy_path)
    publish(policy_path)
    run_directory = open_run_directory(policy.state_dir, RUN_ID)
    digest = run_directory.digest_path.read_text()
    assert "Weekend run" in digest
    assert "https://github.com/owner/repo/pull/42" in digest
    state = load_run_state(run_directory)
    assert "digest posted at https://github.com/owner/repo/issues/99" in state.notes


def test_publishing_a_dry_run_repository_is_refused(tmp_path: Path, fake_binaries: Path) -> None:

    policy_path = write_test_policy(tmp_path, datetime.now(UTC) + timedelta(days=2))
    arguments = ["--home", str(policy_path), "publish", "--repo-key", "dryrun"]
    assert main(arguments) == EXIT_BLOCKED


def test_a_delivery_without_a_passing_gate_is_not_publishable() -> None:
    assert not publishable(
        build_task(
            1,
            "Empty speed field",
            TaskStatus.REVIEW,
            ELIGIBLE,
            build_assessment(Verdict.EXECUTE, Effort.XS, Risk.TESTS, [], []),
        )
    )


def test_the_question_comment_asks_without_changing_anything() -> None:
    task = build_task(
        1,
        "Empty speed field",
        TaskStatus.NEEDS_INPUT,
        ELIGIBLE,
        build_assessment(Verdict.EXECUTE, Effort.XS, Risk.TESTS, [], []),
    )
    body = question_comment(task, "run-7", FOOTER)
    assert "decision only you can make" in body
    assert body.startswith(QUESTION_MARKER)
    assert body.endswith("— weekend-loop run run-7\n")


def test_the_pull_request_title_falls_back_to_the_issue_title() -> None:
    task = build_task(
        1,
        "Empty speed field",
        TaskStatus.REVIEW,
        ELIGIBLE,
        build_assessment(Verdict.EXECUTE, Effort.XS, Risk.TESTS, [], []),
    )
    assert pull_request_title(task) == "weekend: Empty speed field"
    assert "Refs #1" in pull_request_body(task, "run-7", FOOTER, "main", None)


def test_a_branch_git_cannot_merge_with_another_says_so_in_the_pull_request() -> None:
    task = build_task(
        1,
        "Empty speed field",
        TaskStatus.REVIEW,
        ELIGIBLE,
        build_assessment(Verdict.EXECUTE, Effort.XS, Risk.TESTS, [], []),
    ).model_copy(update={"overlaps": [Overlap(issue_number=2, paths=["logbook/records.py"])]})
    assert pull_request_title(task) == "weekend: Empty speed field [merge care]"
    body = pull_request_body(task, "run-7", FOOTER, "main", None)
    assert "## Merge with care" in body
    assert (
        "This branch and #2 both changed logbook/records.py, and git cannot merge them on its own."
        in body
    )
    clean = task.model_copy(
        update={
            "overlaps": [Overlap(issue_number=2, paths=["logbook/records.py"], merges_cleanly=True)]
        }
    )
    assert pull_request_title(clean) == "weekend: Empty speed field"
    assert "## Shares files with" in pull_request_body(clean, "run-7", FOOTER, "main", None)


def test_a_stacked_branch_names_its_parent_and_the_order_to_merge_in() -> None:
    task = build_task(
        11,
        "Who starts",
        TaskStatus.REVIEW,
        ELIGIBLE,
        build_assessment(Verdict.EXECUTE, Effort.S, Risk.BEHAVIOUR, [], []),
    ).model_copy(update={"stacked_on": 5, "base_branch": "weekend/5-archived"})
    body = pull_request_body(
        task, "run-7", FOOTER, "features", "https://github.com/owner/repo/pull/41"
    )
    assert "## Stacked on #5" in body
    assert "This branch builds on #5 (https://github.com/owner/repo/pull/41)" in body
    assert "moves this pull request onto `features`" in body
