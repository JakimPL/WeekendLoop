from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from weekend_loop.local_github.paths import (
    api_issue_url,
    comment_url,
    issue_url,
    pull_request_url,
)
from weekend_loop.models import (
    BoardComment,
    BoardIssue,
    BoardLabel,
    BoardPullRequest,
    IssueState,
)

ISSUE_ID_OFFSET: Final[int] = 4_000_000
REPOSITORY_ID: Final[int] = 1
TIMESTAMP_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%SZ"
OWNER_ASSOCIATION: Final[str] = "OWNER"
GRAPHQL_STATES: Final[dict[IssueState, str]] = {
    IssueState.OPEN: "OPEN",
    IssueState.CLOSED: "CLOSED",
}
REST_STATES: Final[dict[IssueState, str]] = {
    IssueState.OPEN: "open",
    IssueState.CLOSED: "closed",
}

type Payload = dict[str, Any]


@dataclass(frozen=True)
class IssueView:
    issue: BoardIssue
    slug: str
    labels: dict[str, BoardLabel]
    comments: list[BoardComment]
    viewer: str


@dataclass(frozen=True)
class PullRequestView:
    pull_request: BoardPullRequest
    slug: str


def timestamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(TIMESTAMP_FORMAT)


def issue_id(number: int) -> int:
    return ISSUE_ID_OFFSET + number


def issue_number_of(identifier: int) -> int:
    return identifier - ISSUE_ID_OFFSET


def actor(login: str) -> Payload:
    return {"id": f"U_{login}", "is_bot": False, "login": login, "name": ""}


def label_object(name: str, labels: dict[str, BoardLabel]) -> Payload:
    label = labels.get(name, BoardLabel(name=name, description="", colour=""))
    return {
        "id": f"LA_{label.name}",
        "name": label.name,
        "description": label.description,
        "color": label.colour,
    }


def milestone_object(title: str | None) -> Payload | None:
    if title is None:
        return None
    return {"number": 0, "title": title, "description": "", "dueOn": None}


def comment_object(view: IssueView, comment: BoardComment) -> Payload:
    return {
        "id": f"IC_{comment.id}",
        "author": {"login": comment.author},
        "authorAssociation": OWNER_ASSOCIATION,
        "body": comment.body,
        "createdAt": timestamp(comment.created_at),
        "includesCreatedEdit": False,
        "isMinimized": False,
        "minimizedReason": "",
        "reactionGroups": [],
        "url": comment_url(view.slug, view.issue.number, comment.id),
        "viewerDidAuthor": comment.author == view.viewer,
    }


ISSUE_FIELDS: Final[dict[str, Callable[[IssueView], Any]]] = {
    "assignees": lambda view: [
        {"id": f"U_{login}", "login": login, "name": ""} for login in view.issue.assignees
    ],
    "author": lambda view: actor(view.issue.author),
    "body": lambda view: view.issue.body,
    "closed": lambda view: view.issue.state is IssueState.CLOSED,
    "comments": lambda view: [comment_object(view, comment) for comment in view.comments],
    "createdAt": lambda view: timestamp(view.issue.created_at),
    "id": lambda view: f"I_{issue_id(view.issue.number)}",
    "labels": lambda view: [label_object(name, view.labels) for name in view.issue.labels],
    "milestone": lambda view: milestone_object(view.issue.milestone),
    "number": lambda view: view.issue.number,
    "state": lambda view: GRAPHQL_STATES[view.issue.state],
    "title": lambda view: view.issue.title,
    "updatedAt": lambda view: timestamp(view.issue.updated_at),
    "url": lambda view: issue_url(view.slug, view.issue.number),
}

PULL_REQUEST_FIELDS: Final[dict[str, Callable[[PullRequestView], Any]]] = {
    "author": lambda view: actor(view.pull_request.author),
    "baseRefName": lambda view: view.pull_request.base_branch,
    "body": lambda view: view.pull_request.body,
    "createdAt": lambda view: timestamp(view.pull_request.created_at),
    "headRefName": lambda view: view.pull_request.head_branch,
    "id": lambda view: f"PR_{view.pull_request.number}",
    "isDraft": lambda view: view.pull_request.draft,
    "number": lambda view: view.pull_request.number,
    "state": lambda view: GRAPHQL_STATES[view.pull_request.state],
    "title": lambda view: view.pull_request.title,
    "url": lambda view: pull_request_url(view.slug, view.pull_request.number),
}

LABEL_FIELDS: Final[dict[str, Callable[[BoardLabel], Any]]] = {
    "color": lambda label: label.colour,
    "description": lambda label: label.description,
    "id": lambda label: f"LA_{label.name}",
    "name": lambda label: label.name,
}


def unknown_fields(requested: list[str], available: dict[str, Any]) -> list[str]:
    return [field for field in requested if field not in available]


def issue_payload(view: IssueView, fields: list[str]) -> Payload:
    return {field: ISSUE_FIELDS[field](view) for field in fields}


def pull_request_payload(view: PullRequestView, fields: list[str]) -> Payload:
    return {field: PULL_REQUEST_FIELDS[field](view) for field in fields}


def label_payload(label: BoardLabel, fields: list[str]) -> Payload:
    return {field: LABEL_FIELDS[field](label) for field in fields}


def rest_label(name: str, labels: dict[str, BoardLabel]) -> Payload:
    label = label_object(name, labels)
    return {
        "name": label["name"],
        "description": label["description"],
        "color": label["color"],
    }


def rest_issue(issue: BoardIssue, slug: str, labels: dict[str, BoardLabel]) -> Payload:
    return {
        "id": issue_id(issue.number),
        "node_id": f"I_{issue_id(issue.number)}",
        "number": issue.number,
        "title": issue.title,
        "body": issue.body,
        "state": REST_STATES[issue.state],
        "labels": [rest_label(name, labels) for name in issue.labels],
        "user": {"login": issue.author},
        "url": api_issue_url(slug, issue.number),
        "html_url": issue_url(slug, issue.number),
        "created_at": timestamp(issue.created_at),
        "updated_at": timestamp(issue.updated_at),
    }


def rest_repository(slug: str, contents_writable: bool, default_branch: str) -> Payload:
    owner, name = slug.split("/", 1)
    return {
        "id": REPOSITORY_ID,
        "name": name,
        "full_name": slug,
        "owner": {"login": owner},
        "private": True,
        "default_branch": default_branch,
        "permissions": {"admin": False, "push": contents_writable, "pull": True},
    }


def rest_user(login: str) -> Payload:
    return {"login": login, "id": REPOSITORY_ID, "type": "User"}


def blocked_by_nodes(issue: BoardIssue, states: dict[int, IssueState]) -> Payload:
    return {
        "nodes": [
            {"number": number, "state": GRAPHQL_STATES[states[number]]}
            for number in issue.blocked_by
            if number in states
        ]
    }


def blockers_page(
    issues: list[BoardIssue], states: dict[int, IssueState], end_cursor: str | None
) -> Payload:
    nodes = [
        {"number": issue.number, "blockedBy": blocked_by_nodes(issue, states)} for issue in issues
    ]
    page_info = {"hasNextPage": end_cursor is not None, "endCursor": end_cursor}
    return {"data": {"repository": {"issues": {"nodes": nodes, "pageInfo": page_info}}}}
