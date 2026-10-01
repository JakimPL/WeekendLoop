import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from weekend_loop.github_queries import BLOCKERS_QUERY
from weekend_loop.local_github import messages
from weekend_loop.local_github.payloads import (
    blockers_page,
    issue_number_of,
    rest_issue,
    rest_repository,
    rest_user,
)
from weekend_loop.local_github.repository import (
    commit_exists,
    create_reference,
    default_branch,
    reference_exists,
)
from weekend_loop.local_github.store import LocalBoard, board_at, read_account
from weekend_loop.models import IssueState

GET: Final[str] = "GET"
POST: Final[str] = "POST"
BAD_REQUEST: Final[int] = 400
NOT_FOUND: Final[int] = 404
FORBIDDEN: Final[int] = 403
UNPROCESSABLE: Final[int] = 422
GRAPHQL_PAGE_SIZE: Final[int] = 100
REPOSITORY_PATTERN: Final[str] = r"repos/(?P<owner>[^/]+)/(?P<name>[^/]+)"
ISSUE_PATTERN: Final[str] = REPOSITORY_PATTERN + r"/issues/(?P<number>\d+)"
QUERY_FIELD: Final[str] = "query"
CURSOR_FIELD: Final[str] = "endCursor"
ISSUE_ID_FIELD: Final[str] = "issue_id"
STACK_FIELD: Final[str] = "pull_requests"


@dataclass(frozen=True)
class ApiRequest:
    method: str
    endpoint: str
    fields: dict[str, Any]
    body: Any
    paginate: bool


@dataclass(frozen=True)
class ApiContext:
    root: Path
    now: datetime


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class GraphQLError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


type Handler = Callable[[re.Match[str], ApiRequest, ApiContext], list[Any]]


@dataclass(frozen=True)
class Route:
    method: str
    pattern: re.Pattern[str]
    handler: Handler


def slug_from(match: re.Match[str]) -> str:
    return f"{match['owner']}/{match['name']}"


def existing_board(context: ApiContext, slug: str) -> LocalBoard:
    board = board_at(context.root, slug)
    if not board.exists():
        raise ApiError(NOT_FOUND, messages.NOT_FOUND)
    return board


def current_user(match: re.Match[str], request: ApiRequest, context: ApiContext) -> list[Any]:
    return [rest_user(read_account(context.root).login)]


def repository(match: re.Match[str], request: ApiRequest, context: ApiContext) -> list[Any]:
    board = existing_board(context, slug_from(match))
    writable = board.read_index().contents_writable
    return [rest_repository(board.slug, writable, default_branch(board.repository_path))]


def create_ref(match: re.Match[str], request: ApiRequest, context: ApiContext) -> list[Any]:
    board = existing_board(context, slug_from(match))
    if not board.read_index().contents_writable:
        raise ApiError(FORBIDDEN, messages.FORBIDDEN)
    reference = str(request.fields.get("ref", ""))
    sha = str(request.fields.get("sha", ""))
    if not commit_exists(board.repository_path, sha):
        raise ApiError(UNPROCESSABLE, messages.OBJECT_MISSING)
    if reference_exists(board.repository_path, reference):
        raise ApiError(UNPROCESSABLE, messages.REFERENCE_EXISTS)
    create_reference(board.repository_path, reference, sha)
    return [{"ref": reference, "object": {"sha": sha, "type": "commit"}}]


def create_stack(match: re.Match[str], request: ApiRequest, context: ApiContext) -> list[Any]:
    board = existing_board(context, slug_from(match))
    numbers = request.body.get(STACK_FIELD) if isinstance(request.body, dict) else None
    if not isinstance(numbers, list) or not all(isinstance(number, int) for number in numbers):
        raise ApiError(UNPROCESSABLE, messages.VALIDATION_FAILED)
    for number in numbers:
        if not board.has_pull_request(number):
            raise ApiError(UNPROCESSABLE, messages.VALIDATION_FAILED)
    board.link_stack(numbers)
    return [{"id": len(board.read_index().stacks), STACK_FIELD: numbers}]


def existing_issue(board: LocalBoard, number: int) -> None:
    if not board.has_issue(number):
        raise ApiError(NOT_FOUND, messages.NOT_FOUND)


def issue(match: re.Match[str], request: ApiRequest, context: ApiContext) -> list[Any]:
    board = existing_board(context, slug_from(match))
    number = int(match["number"])
    existing_issue(board, number)
    return [rest_issue(board.read_issue(number), board.slug, board.labels())]


def blockers(match: re.Match[str], request: ApiRequest, context: ApiContext) -> list[Any]:
    board = existing_board(context, slug_from(match))
    number = int(match["number"])
    existing_issue(board, number)
    labels = board.labels()
    blocked = board.read_issue(number)
    return [
        [
            rest_issue(board.read_issue(blocker), board.slug, labels)
            for blocker in blocked.blocked_by
            if board.has_issue(blocker)
        ]
    ]


def blocking_number(request: ApiRequest) -> int:
    value = request.fields.get(ISSUE_ID_FIELD)
    if not isinstance(value, int) or isinstance(value, bool):
        detail = messages.NOT_AN_INTEGER.format(name=ISSUE_ID_FIELD, value=value)
        raise ApiError(UNPROCESSABLE, detail)
    return issue_number_of(value)


def add_blocker(match: re.Match[str], request: ApiRequest, context: ApiContext) -> list[Any]:
    board = existing_board(context, slug_from(match))
    number = int(match["number"])
    existing_issue(board, number)
    blocker = blocking_number(request)
    existing_issue(board, blocker)
    blocked = board.read_issue(number)
    if blocker in blocked.blocked_by:
        detail = messages.DEPENDENCY_EXISTS.format(number=number, blocker=blocker)
        raise ApiError(UNPROCESSABLE, detail)
    board.write_issue(
        blocked.model_copy(
            update={"blocked_by": [*blocked.blocked_by, blocker], "updated_at": context.now}
        )
    )
    return [rest_issue(board.read_issue(blocker), board.slug, board.labels())]


def normalised(query: str) -> str:
    return " ".join(query.split())


def blockers_pages(board: LocalBoard, cursor: int, paginate: bool) -> list[Any]:
    issues = board.issues()
    states = {issue.number: issue.state for issue in issues}
    open_issues = [issue for issue in issues if issue.state is IssueState.OPEN]
    pages: list[Any] = []
    while True:
        page = open_issues[cursor : cursor + GRAPHQL_PAGE_SIZE]
        cursor += GRAPHQL_PAGE_SIZE
        next_cursor = str(cursor) if cursor < len(open_issues) else None
        pages.append(blockers_page(page, states, next_cursor))
        if next_cursor is None or not paginate:
            return pages


def graphql(match: re.Match[str], request: ApiRequest, context: ApiContext) -> list[Any]:
    query = str(request.fields.get(QUERY_FIELD, ""))
    if normalised(query) != normalised(BLOCKERS_QUERY):
        raise GraphQLError(messages.UNSUPPORTED_QUERY)
    slug = f"{request.fields.get('owner', '')}/{request.fields.get('name', '')}"
    board = board_at(context.root, slug)
    if not board.exists():
        raise GraphQLError(messages.REPOSITORY_NOT_FOUND.format(slug=slug))
    cursor = request.fields.get(CURSOR_FIELD)
    start = int(cursor) if isinstance(cursor, str) and cursor.isdigit() else 0
    return blockers_pages(board, start, request.paginate)


ROUTES: Final[tuple[Route, ...]] = (
    Route(GET, re.compile("user"), current_user),
    Route(GET, re.compile(REPOSITORY_PATTERN), repository),
    Route(POST, re.compile(REPOSITORY_PATTERN + "/git/refs"), create_ref),
    Route(POST, re.compile(REPOSITORY_PATTERN + "/stacks"), create_stack),
    Route(GET, re.compile(ISSUE_PATTERN), issue),
    Route(GET, re.compile(ISSUE_PATTERN + "/dependencies/blocked_by"), blockers),
    Route(POST, re.compile(ISSUE_PATTERN + "/dependencies/blocked_by"), add_blocker),
    Route(POST, re.compile("graphql"), graphql),
)


def endpoint_path(endpoint: str) -> str:
    return endpoint.split("?", 1)[0].strip("/")


def respond(request: ApiRequest, context: ApiContext) -> list[Any]:
    path = endpoint_path(request.endpoint)
    for route in ROUTES:
        match = route.pattern.fullmatch(path)
        if match is not None and route.method == request.method:
            return route.handler(match, request, context)
    raise ApiError(NOT_FOUND, messages.NOT_FOUND)
