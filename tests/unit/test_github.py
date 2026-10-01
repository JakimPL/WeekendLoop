from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.unit.conftest import OWNER_LOGIN, issue_payload, write_github_data
from weekend_loop.backends import with_foreign_activity
from weekend_loop.github import (
    GitHubReader,
    issue_from_payload,
    last_foreign_activity,
    linked_issue_numbers,
    parse_blockers,
    pull_request_from_payload,
    pull_requests_by_issue,
    read_token,
    signed,
)
from weekend_loop.models import IssueComment

SLUG = "owner/repo"
FOOTER = "— weekend-loop run {run_id}"


def build_reader(tmp_path: Path) -> GitHubReader:
    return GitHubReader(SLUG, "fake-token", tmp_path / "gh-config")


def test_the_listing_payload_becomes_an_issue_record() -> None:
    payload = issue_payload(7, "CSV export", "body", ["bug", "weekend:auto"], ["colleague"])
    payload["milestone"] = {"title": "v1"}
    issue = issue_from_payload(payload, [12], [])
    assert issue.labels == ["bug", "weekend:auto"]
    assert issue.assignees == ["colleague"]
    assert issue.milestone == "v1"
    assert issue.author == OWNER_LOGIN
    assert issue.open_linked_pull_requests == [12]
    assert issue.last_foreign_activity_at is None


def test_a_pull_request_claims_issues_through_keywords_and_its_branch() -> None:
    pull_request = pull_request_from_payload(
        {
            "number": 12,
            "title": "CSV export",
            "body": "Work in progress.\n\nCloses #7",
            "headRefName": "feat/9-csv-export",
            "author": {"login": "colleague"},
            "url": "https://github.com/owner/repo/pull/12",
        }
    )
    assert linked_issue_numbers(pull_request) == [7, 9]
    assert pull_requests_by_issue([pull_request]) == {7: [12], 9: [12]}


def test_only_other_people_count_as_foreign_activity() -> None:
    moment = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    comments = [
        IssueComment(author=OWNER_LOGIN, created_at=moment, body=""),
        IssueComment(author="colleague", created_at=moment.replace(hour=9), body=""),
    ]
    assert last_foreign_activity(comments, OWNER_LOGIN) == moment.replace(hour=9)
    assert last_foreign_activity(comments[:1], OWNER_LOGIN) is None


def test_the_agents_own_signed_comment_is_not_foreign_activity() -> None:
    moment = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    body = signed("1. Which unit does the feed use?", FOOTER, "run-7")
    comments = [IssueComment(author="Weekend Agent", created_at=moment, body=body)]
    assert last_foreign_activity(comments, OWNER_LOGIN) is None


def test_open_issues_carry_the_pull_requests_that_claim_them(
    tmp_path: Path, fake_binaries: Path
) -> None:
    write_github_data(
        fake_binaries,
        issues=[issue_payload(7, "CSV export", "body", [], [])],
        pull_requests=[
            {
                "number": 12,
                "title": "CSV export",
                "body": "Closes #7",
                "headRefName": "feat/csv-export",
                "author": {"login": "colleague"},
                "url": "https://github.com/owner/repo/pull/12",
            }
        ],
        comments={},
        push=False,
    )
    issues = build_reader(tmp_path).open_issues(50)
    assert [issue.number for issue in issues] == [7]
    assert issues[0].open_linked_pull_requests == [12]


def test_comments_supply_the_timestamp_the_listing_cannot(
    tmp_path: Path, fake_binaries: Path
) -> None:
    moment = datetime(2026, 9, 18, 8, 30, tzinfo=UTC)
    write_github_data(
        fake_binaries,
        issues=[issue_payload(7, "CSV export", "body", [], [])],
        pull_requests=[],
        comments={"7": [{"author": {"login": "colleague"}, "createdAt": moment.isoformat()}]},
        push=False,
    )
    reader = build_reader(tmp_path)
    issue = with_foreign_activity(reader, reader.open_issues(50)[0], OWNER_LOGIN)
    assert issue.last_foreign_activity_at == moment


def test_a_comment_comes_back_with_its_body(tmp_path: Path, fake_binaries: Path) -> None:
    moment = datetime(2026, 9, 18, 8, 30, tzinfo=UTC)
    write_github_data(
        fake_binaries,
        issues=[issue_payload(7, "CSV export", "body", [], [])],
        pull_requests=[],
        comments={
            "7": [
                {
                    "author": {"login": OWNER_LOGIN},
                    "createdAt": moment.isoformat(),
                    "body": "1. Knots.",
                }
            ]
        },
        push=False,
    )
    assert [comment.body for comment in build_reader(tmp_path).issue_comments(7)] == ["1. Knots."]


def test_a_missing_or_empty_token_file_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_token(tmp_path / "absent.token")
    empty = tmp_path / "empty.token"
    empty.write_text("\n")
    with pytest.raises(ValueError, match="empty"):
        read_token(empty)


def test_open_issues_carry_what_blocks_them_on_github(tmp_path: Path, fake_binaries: Path) -> None:
    write_github_data(
        fake_binaries,
        issues=[
            issue_payload(13, "Saved rules", "body", [], []),
            issue_payload(17, "Rules", "b", [], []),
        ],
        pull_requests=[],
        comments={},
        push=False,
    )
    data_path = fake_binaries / "gh-data.json"
    data = json.loads(data_path.read_text())
    data_path.write_text(json.dumps({**data, "blockers": {"17": [13], "13": []}}))
    issues = {issue.number: issue for issue in build_reader(tmp_path).open_issues(50)}
    assert issues[17].blocked_by == [13]
    assert issues[13].blocked_by == []


def test_the_blockers_answer_keeps_one_issue_per_line() -> None:
    answer = '{"number": 17, "blocked_by": [13, 11]}\n\n{"number": 13, "blocked_by": []}\n'
    assert parse_blockers(answer) == {17: [11, 13], 13: []}
