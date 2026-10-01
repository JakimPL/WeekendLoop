import argparse
import json
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final, NoReturn

from weekend_loop.local_github import messages
from weekend_loop.local_github.api import (
    BAD_REQUEST,
    GET,
    POST,
    ApiContext,
    ApiError,
    ApiRequest,
    GraphQLError,
    respond,
)
from weekend_loop.local_github.paths import (
    REPOSITORY_DIRECTORY_NAME,
    comment_url,
    issue_url,
    pull_request_url,
    slug_of,
)
from weekend_loop.local_github.payloads import (
    ISSUE_FIELDS,
    LABEL_FIELDS,
    PULL_REQUEST_FIELDS,
    IssueView,
    PullRequestView,
    issue_payload,
    label_payload,
    pull_request_payload,
    timestamp,
)
from weekend_loop.local_github.repository import (
    branch_commit,
    branch_diff,
    commits_between,
    default_branch,
)
from weekend_loop.local_github.store import LocalBoard, board_at, locked, read_account
from weekend_loop.models import BoardComment, BoardIssue, BoardLabel, BoardPullRequest, IssueState

FAILURE_EXIT_CODE: Final[int] = 1
DEFAULT_LIST_LIMIT: Final[int] = 30
DEFAULT_LABEL_COLOUR: Final[str] = "ededed"
REPOSITORY_VARIABLE: Final[str] = "GH_REPO"
STDIN_MARKER: Final[str] = "-"
FILE_MARKER: Final[str] = "@"
ISSUE_STATES: Final[dict[str, tuple[IssueState, ...]]] = {
    "open": (IssueState.OPEN,),
    "closed": (IssueState.CLOSED,),
    "all": (IssueState.OPEN, IssueState.CLOSED),
}
PULL_REQUEST_STATES: Final[dict[str, tuple[IssueState, ...]]] = {
    **ISSUE_STATES,
    "merged": (),
}
NUMBER_PATTERN: Final[re.Pattern[str]] = re.compile(r"(?:^#?|/(?:issues|pull)/)(\d+)$")
INTEGER_PATTERN: Final[re.Pattern[str]] = re.compile(r"-?\d+")
LITERALS: Final[dict[str, bool | None]] = {"true": True, "false": False, "null": None}
DRAFT_STATE: Final[str] = "DRAFT"
API_COMMAND: Final[tuple[str, ...]] = ("api",)
GRAPHQL_ENDPOINT: Final[str] = "graphql"


@dataclass(frozen=True)
class Answer:
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class Invocation:
    root: Path
    arguments: list[str]
    read_stdin: Callable[[], str]
    environment: Mapping[str, str]
    working_directory: Path
    now: datetime


class GhError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class GhParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise GhError(messages.USAGE.format(message=message))


type Handler = Callable[[argparse.Namespace, Invocation], Answer]


@dataclass(frozen=True)
class Command:
    build: Callable[[GhParser], None]
    handler: Handler
    exclusive: bool


def succeeded(stdout: str, stderr: str) -> Answer:
    return Answer(exit_code=0, stdout=stdout, stderr=stderr)


def compact(value: Any) -> str:  # noqa: ANN401
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def number_argument(text: str) -> int:
    match = NUMBER_PATTERN.search(text)
    if match is None:
        raise argparse.ArgumentTypeError(f"invalid issue or pull request: {text!r}")
    return int(match.group(1))


def strip_host(value: str) -> str:
    parts = value.strip("/").split("/")
    return "/".join(parts[-2:])


def origin_slug(invocation: Invocation) -> str | None:
    answer = subprocess.run(
        ["git", "-C", str(invocation.working_directory), "remote", "get-url", "origin"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    origin = Path(answer.stdout.strip())
    if answer.returncode != 0 or origin.name != REPOSITORY_DIRECTORY_NAME:
        return None
    if not origin.is_relative_to(invocation.root):
        return None
    return slug_of(origin.parent)


def repository_slug(options: argparse.Namespace, invocation: Invocation) -> str:
    named: str | None = options.repo
    if named:
        return strip_host(named)
    variable = invocation.environment.get(REPOSITORY_VARIABLE)
    if variable:
        return strip_host(variable)
    inferred = origin_slug(invocation)
    if inferred is None:
        raise GhError(messages.NO_REPOSITORY)
    return inferred


def board_for(options: argparse.Namespace, invocation: Invocation) -> LocalBoard:
    slug = repository_slug(options, invocation)
    board = board_at(invocation.root, slug)
    if not board.exists():
        raise GhError(messages.REPOSITORY_NOT_FOUND.format(slug=slug))
    return board


def body_text(options: argparse.Namespace, invocation: Invocation) -> str | None:
    body: str | None = options.body
    body_file: str | None = options.body_file
    if body is not None:
        return body
    if body_file == STDIN_MARKER:
        return invocation.read_stdin()
    if body_file is not None:
        return Path(body_file).read_text()
    return None


def split_names(values: list[str]) -> list[str]:
    return [name.strip() for value in values for name in value.split(",") if name.strip()]


def requested_fields(spec: str | None, available: Mapping[str, Any]) -> list[str] | None:
    if spec is None:
        return None
    fields = split_names([spec])
    for field in fields:
        if field not in available:
            listing = "\n".join(f"  {name}" for name in sorted(available))
            raise GhError(messages.UNKNOWN_JSON_FIELD.format(field=field, available=listing))
    return fields


def missing_label(board: LocalBoard, names: list[str]) -> str | None:
    labels = board.labels()
    return next((name for name in names if name not in labels), None)


def existing_issue(board: LocalBoard, number: int) -> BoardIssue:
    if not board.has_issue(number):
        raise GhError(messages.ISSUE_NOT_FOUND.format(number=number))
    return board.read_issue(number)


def existing_pull_request(board: LocalBoard, number: int) -> BoardPullRequest:
    if not board.has_pull_request(number):
        raise GhError(messages.PULL_REQUEST_NOT_FOUND.format(number=number))
    return board.read_pull_request(number)


def issue_view(
    board: LocalBoard, issue: BoardIssue, with_comments: bool, invocation: Invocation
) -> IssueView:
    return IssueView(
        issue=issue,
        slug=board.slug,
        labels=board.labels(),
        comments=board.comments(issue.number) if with_comments else [],
        viewer=read_account(invocation.root).login,
    )


def listed(numbered: list[Any], limit: int) -> list[Any]:
    return sorted(numbered, key=lambda item: item.number, reverse=True)[:limit]


def build_issue_list(parser: GhParser) -> None:
    parser.add_argument("-R", "--repo")
    parser.add_argument("-s", "--state", choices=sorted(ISSUE_STATES), default="open")
    parser.add_argument("-L", "--limit", type=int, default=DEFAULT_LIST_LIMIT)
    parser.add_argument("-l", "--label", action="append", default=[])
    parser.add_argument("--json")


def issue_list(options: argparse.Namespace, invocation: Invocation) -> Answer:
    board = board_for(options, invocation)
    fields = requested_fields(options.json, ISSUE_FIELDS)
    states = ISSUE_STATES[options.state]
    wanted = split_names(options.label)
    issues = listed(
        [
            issue
            for issue in board.issues()
            if issue.state in states and all(label in issue.labels for label in wanted)
        ],
        options.limit,
    )
    if fields is not None:
        with_comments = "comments" in fields
        payloads = [
            issue_payload(issue_view(board, issue, with_comments, invocation), fields)
            for issue in issues
        ]
        return succeeded(compact(payloads) + "\n", "")
    lines = [
        "\t".join(
            [
                str(issue.number),
                issue.state.value.upper(),
                issue.title,
                ", ".join(issue.labels),
                timestamp(issue.updated_at),
            ]
        )
        for issue in issues
    ]
    return succeeded("".join(f"{line}\n" for line in lines), "")


def build_issue_view(parser: GhParser) -> None:
    parser.add_argument("number", type=number_argument)
    parser.add_argument("-R", "--repo")
    parser.add_argument("-c", "--comments", action="store_true")
    parser.add_argument("--json")


def readable_issue(view: IssueView, with_comments: bool) -> str:
    issue = view.issue
    lines = [
        f"title:\t{issue.title}",
        f"state:\t{issue.state.value.upper()}",
        f"author:\t{issue.author}",
        f"labels:\t{', '.join(issue.labels)}",
        f"comments:\t{len(view.comments)}",
        f"assignees:\t{', '.join(issue.assignees)}",
        f"milestone:\t{issue.milestone or ''}",
        f"number:\t{issue.number}",
        "--",
        issue.body.rstrip(),
    ]
    if with_comments:
        for comment in view.comments:
            lines.extend(
                ["--", f"author:\t{comment.author}", f"created:\t{timestamp(comment.created_at)}"]
            )
            lines.extend(["--", comment.body.rstrip()])
    return "\n".join(lines) + "\n"


def issue_show(options: argparse.Namespace, invocation: Invocation) -> Answer:
    board = board_for(options, invocation)
    issue = existing_issue(board, options.number)
    fields = requested_fields(options.json, ISSUE_FIELDS)
    if fields is not None:
        view = issue_view(board, issue, "comments" in fields, invocation)
        return succeeded(compact(issue_payload(view, fields)) + "\n", "")
    view = issue_view(board, issue, True, invocation)
    return succeeded(readable_issue(view, options.comments), "")


def build_issue_create(parser: GhParser) -> None:
    parser.add_argument("-R", "--repo")
    parser.add_argument("-t", "--title")
    parser.add_argument("-b", "--body")
    parser.add_argument("-F", "--body-file")
    parser.add_argument("-l", "--label", action="append", default=[])


def issue_create(options: argparse.Namespace, invocation: Invocation) -> Answer:
    board = board_for(options, invocation)
    title: str | None = options.title
    body = body_text(options, invocation)
    if title is None or body is None:
        raise GhError(messages.TITLE_AND_BODY_REQUIRED)
    labels = split_names(options.label)
    absent = missing_label(board, labels)
    if absent is not None:
        reason = messages.LABEL_NOT_FOUND.format(label=absent)
        raise GhError(messages.ADD_LABELS_FAILED.format(reason=reason))
    number = board.take_number()
    board.write_issue(
        BoardIssue(
            number=number,
            title=title,
            body=body,
            labels=labels,
            assignees=[],
            milestone=None,
            author=read_account(invocation.root).login,
            created_at=invocation.now,
            updated_at=invocation.now,
            state=IssueState.OPEN,
            blocked_by=[],
        )
    )
    return succeeded(issue_url(board.slug, number) + "\n", "")


def build_issue_comment(parser: GhParser) -> None:
    parser.add_argument("number", type=number_argument)
    parser.add_argument("-R", "--repo")
    parser.add_argument("-b", "--body")
    parser.add_argument("-F", "--body-file")


def add_comment(board: LocalBoard, number: int, body: str, invocation: Invocation) -> int:
    comment_id = board.take_comment_id()
    board.append_comment(
        number,
        BoardComment(
            id=comment_id,
            author=read_account(invocation.root).login,
            created_at=invocation.now,
            body=body,
        ),
    )
    return comment_id


def issue_comment(options: argparse.Namespace, invocation: Invocation) -> Answer:
    board = board_for(options, invocation)
    existing_issue(board, options.number)
    body = body_text(options, invocation)
    if body is None:
        raise GhError(messages.BODY_REQUIRED)
    comment_id = add_comment(board, options.number, body, invocation)
    return succeeded(comment_url(board.slug, options.number, comment_id) + "\n", "")


def build_issue_edit(parser: GhParser) -> None:
    parser.add_argument("numbers", type=number_argument, nargs="+")
    parser.add_argument("-R", "--repo")
    parser.add_argument("--add-label", action="append", default=[])
    parser.add_argument("--remove-label", action="append", default=[])
    parser.add_argument("-t", "--title")
    parser.add_argument("-b", "--body")
    parser.add_argument("-F", "--body-file")


def edited_issue(
    issue: BoardIssue, added: list[str], removed: list[str], text: dict[str, str]
) -> BoardIssue:
    labels = [label for label in issue.labels if label not in removed]
    labels.extend(label for label in added if label not in labels)
    return issue.model_copy(update={"labels": labels, **text})


def issue_edit(options: argparse.Namespace, invocation: Invocation) -> Answer:
    board = board_for(options, invocation)
    added = split_names(options.add_label)
    removed = split_names(options.remove_label)
    text: dict[str, str] = {}
    if options.title is not None:
        text["title"] = options.title
    body = body_text(options, invocation)
    if body is not None:
        text["body"] = body
    urls: list[str] = []
    for number in options.numbers:
        issue = existing_issue(board, number)
        url = issue_url(board.slug, number)
        absent = missing_label(board, [*added, *removed])
        if absent is not None:
            reason = messages.LABEL_NOT_FOUND.format(label=absent)
            raise GhError(messages.EDIT_FAILED.format(url=url, reason=reason))
        changed = edited_issue(issue, added, removed, text)
        if changed != issue:
            board.write_issue(changed.model_copy(update={"updated_at": invocation.now}))
        urls.append(url)
    return succeeded("".join(f"{url}\n" for url in urls), "")


def build_issue_close(parser: GhParser) -> None:
    parser.add_argument("number", type=number_argument)
    parser.add_argument("-R", "--repo")
    parser.add_argument("-c", "--comment")


def build_issue_reopen(parser: GhParser) -> None:
    parser.add_argument("number", type=number_argument)
    parser.add_argument("-R", "--repo")


def set_issue_state(board: LocalBoard, number: int, state: IssueState, now: datetime) -> BoardIssue:
    issue = existing_issue(board, number)
    changed = issue.model_copy(update={"state": state, "updated_at": now})
    board.write_issue(changed)
    return changed


def issue_close(options: argparse.Namespace, invocation: Invocation) -> Answer:
    board = board_for(options, invocation)
    existing_issue(board, options.number)
    if options.comment is not None:
        add_comment(board, options.number, options.comment, invocation)
    issue = set_issue_state(board, options.number, IssueState.CLOSED, invocation.now)
    message = messages.CLOSED_ISSUE.format(slug=board.slug, number=issue.number, title=issue.title)
    return succeeded("", message + "\n")


def issue_reopen(options: argparse.Namespace, invocation: Invocation) -> Answer:
    board = board_for(options, invocation)
    issue = set_issue_state(board, options.number, IssueState.OPEN, invocation.now)
    message = messages.REOPENED_ISSUE.format(
        slug=board.slug, number=issue.number, title=issue.title
    )
    return succeeded("", message + "\n")


def build_pr_list(parser: GhParser) -> None:
    parser.add_argument("-R", "--repo")
    parser.add_argument("-s", "--state", choices=sorted(PULL_REQUEST_STATES), default="open")
    parser.add_argument("-L", "--limit", type=int, default=DEFAULT_LIST_LIMIT)
    parser.add_argument("-H", "--head")
    parser.add_argument("-B", "--base")
    parser.add_argument("--json")


def pull_request_state(pull_request: BoardPullRequest) -> str:
    if pull_request.draft and pull_request.state is IssueState.OPEN:
        return DRAFT_STATE
    return pull_request.state.value.upper()


def pr_list(options: argparse.Namespace, invocation: Invocation) -> Answer:
    board = board_for(options, invocation)
    fields = requested_fields(options.json, PULL_REQUEST_FIELDS)
    states = PULL_REQUEST_STATES[options.state]
    head: str | None = options.head
    base: str | None = options.base
    pull_requests = listed(
        [
            pull_request
            for pull_request in board.pull_requests()
            if pull_request.state in states
            and head in (None, pull_request.head_branch)
            and base in (None, pull_request.base_branch)
        ],
        options.limit,
    )
    if fields is not None:
        payloads = [
            pull_request_payload(PullRequestView(pull_request, board.slug), fields)
            for pull_request in pull_requests
        ]
        return succeeded(compact(payloads) + "\n", "")
    lines = [
        "\t".join(
            [
                str(pull_request.number),
                pull_request.title,
                pull_request.head_branch,
                pull_request_state(pull_request),
                timestamp(pull_request.created_at),
            ]
        )
        for pull_request in pull_requests
    ]
    return succeeded("".join(f"{line}\n" for line in lines), "")


def build_pr_view(parser: GhParser) -> None:
    parser.add_argument("number", type=number_argument)
    parser.add_argument("-R", "--repo")
    parser.add_argument("--json")


def pr_view(options: argparse.Namespace, invocation: Invocation) -> Answer:
    board = board_for(options, invocation)
    pull_request = existing_pull_request(board, options.number)
    fields = requested_fields(options.json, PULL_REQUEST_FIELDS)
    if fields is not None:
        payload = pull_request_payload(PullRequestView(pull_request, board.slug), fields)
        return succeeded(compact(payload) + "\n", "")
    lines = [
        f"title:\t{pull_request.title}",
        f"state:\t{pull_request_state(pull_request)}",
        f"author:\t{pull_request.author}",
        f"number:\t{pull_request.number}",
        f"url:\t{pull_request_url(board.slug, pull_request.number)}",
        f"base:\t{pull_request.base_branch}",
        f"head:\t{pull_request.head_branch}",
        "--",
        pull_request.body.rstrip(),
    ]
    return succeeded("\n".join(lines) + "\n", "")


def build_pr_create(parser: GhParser) -> None:
    parser.add_argument("-R", "--repo")
    parser.add_argument("-d", "--draft", action="store_true")
    parser.add_argument("-H", "--head")
    parser.add_argument("-B", "--base")
    parser.add_argument("-t", "--title")
    parser.add_argument("-b", "--body")
    parser.add_argument("-F", "--body-file")


def existing_request(board: LocalBoard, head: str, base: str) -> BoardPullRequest | None:
    return next(
        (
            pull_request
            for pull_request in board.open_pull_requests()
            if pull_request.head_branch == head and pull_request.base_branch == base
        ),
        None,
    )


def checked_branches(board: LocalBoard, head: str, base: str) -> None:
    head_commit = branch_commit(board.repository_path, head)
    base_commit = branch_commit(board.repository_path, base)
    if head_commit is None or base_commit is None:
        raise GhError(messages.BRANCH_MISSING)
    if commits_between(board.repository_path, base_commit, head_commit) == 0:
        raise GhError(messages.NO_COMMITS.format(base=base, head=head))


def pr_create(options: argparse.Namespace, invocation: Invocation) -> Answer:
    board = board_for(options, invocation)
    head: str | None = options.head
    if head is None:
        raise GhError(messages.HEAD_REQUIRED)
    base: str = options.base or default_branch(board.repository_path)
    title: str | None = options.title
    body = body_text(options, invocation)
    if title is None or body is None:
        raise GhError(messages.TITLE_AND_BODY_REQUIRED)
    duplicate = existing_request(board, head, base)
    if duplicate is not None:
        url = pull_request_url(board.slug, duplicate.number)
        raise GhError(messages.PULL_REQUEST_EXISTS.format(head=head, base=base, url=url))
    checked_branches(board, head, base)
    number = board.take_number()
    board.write_pull_request(
        BoardPullRequest(
            number=number,
            title=title,
            body=body,
            head_branch=head,
            base_branch=base,
            author=read_account(invocation.root).login,
            draft=options.draft,
            state=IssueState.OPEN,
            created_at=invocation.now,
        )
    )
    return succeeded(pull_request_url(board.slug, number) + "\n", "")


def build_pr_diff(parser: GhParser) -> None:
    parser.add_argument("number", type=number_argument)
    parser.add_argument("-R", "--repo")


def pr_diff(options: argparse.Namespace, invocation: Invocation) -> Answer:
    board = board_for(options, invocation)
    pull_request = existing_pull_request(board, options.number)
    head = branch_commit(board.repository_path, pull_request.head_branch)
    base = branch_commit(board.repository_path, pull_request.base_branch)
    if head is None or base is None:
        raise GhError(messages.BRANCH_MISSING)
    diff = branch_diff(board.repository_path, pull_request.base_branch, pull_request.head_branch)
    return succeeded(diff, "")


def build_pr_close(parser: GhParser) -> None:
    parser.add_argument("number", type=number_argument)
    parser.add_argument("-R", "--repo")


def pr_close(options: argparse.Namespace, invocation: Invocation) -> Answer:
    board = board_for(options, invocation)
    pull_request = existing_pull_request(board, options.number)
    board.write_pull_request(pull_request.model_copy(update={"state": IssueState.CLOSED}))
    message = messages.CLOSED_PULL_REQUEST.format(
        slug=board.slug, number=pull_request.number, title=pull_request.title
    )
    return succeeded("", message + "\n")


def build_label_create(parser: GhParser) -> None:
    parser.add_argument("name")
    parser.add_argument("-R", "--repo")
    parser.add_argument("-d", "--description", default="")
    parser.add_argument("-c", "--color", default=DEFAULT_LABEL_COLOUR)
    parser.add_argument("-f", "--force", action="store_true")


def label_create(options: argparse.Namespace, invocation: Invocation) -> Answer:
    board = board_for(options, invocation)
    name: str = options.name
    exists = name in board.labels()
    if exists and not options.force:
        raise GhError(messages.LABEL_EXISTS.format(name=name))
    board.write_label(BoardLabel(name=name, description=options.description, colour=options.color))
    template = messages.LABEL_UPDATED if exists else messages.LABEL_CREATED
    return succeeded("", template.format(name=name, slug=board.slug) + "\n")


def build_label_list(parser: GhParser) -> None:
    parser.add_argument("-R", "--repo")
    parser.add_argument("-L", "--limit", type=int, default=DEFAULT_LIST_LIMIT)
    parser.add_argument("--json")


def label_list(options: argparse.Namespace, invocation: Invocation) -> Answer:
    board = board_for(options, invocation)
    fields = requested_fields(options.json, LABEL_FIELDS)
    labels = sorted(board.labels().values(), key=lambda label: label.name)[: options.limit]
    if fields is not None:
        return succeeded(compact([label_payload(label, fields) for label in labels]) + "\n", "")
    lines = [f"{label.name}\t{label.description}\t#{label.colour}" for label in labels]
    return succeeded("".join(f"{line}\n" for line in lines), "")


def build_api(parser: GhParser) -> None:
    parser.add_argument("endpoint")
    parser.add_argument("-X", "--method")
    parser.add_argument("-f", "--raw-field", action="append", default=[])
    parser.add_argument("-F", "--field", action="append", default=[])
    parser.add_argument("-H", "--header", action="append", default=[])
    parser.add_argument("--input")
    parser.add_argument("--paginate", action="store_true")


def typed_value(value: str, invocation: Invocation) -> Any:  # noqa: ANN401
    if value in LITERALS:
        return LITERALS[value]
    if INTEGER_PATTERN.fullmatch(value):
        return int(value)
    if value == FILE_MARKER + STDIN_MARKER:
        return invocation.read_stdin()
    if value.startswith(FILE_MARKER):
        return Path(value[1:]).read_text()
    return value


def api_fields(options: argparse.Namespace, invocation: Invocation) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for raw in options.raw_field:
        key, _, value = raw.partition("=")
        fields[key] = value
    for typed in options.field:
        key, _, value = typed.partition("=")
        fields[key] = typed_value(value, invocation)
    return fields


def api_body(options: argparse.Namespace, invocation: Invocation) -> Any:  # noqa: ANN401
    source: str | None = options.input
    if source is None:
        return None
    text = invocation.read_stdin() if source == STDIN_MARKER else Path(source).read_text()
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise ApiError(BAD_REQUEST, messages.UNREADABLE_INPUT.format(reason=error.msg)) from error


def api_method(options: argparse.Namespace, has_fields: bool) -> str:
    method: str | None = options.method
    if method is not None:
        return method.upper()
    if options.endpoint.strip("/") == GRAPHQL_ENDPOINT or has_fields or options.input is not None:
        return POST
    return GET


def api_request(options: argparse.Namespace, invocation: Invocation) -> ApiRequest:
    fields = api_fields(options, invocation)
    return ApiRequest(
        method=api_method(options, bool(fields)),
        endpoint=options.endpoint,
        fields=fields,
        body=api_body(options, invocation),
        paginate=options.paginate,
    )


def api_failure(failure: ApiError) -> Answer:
    body = {
        "message": failure.message,
        "documentation_url": messages.DOCUMENTATION_URL,
        "status": str(failure.status),
    }
    stderr = messages.API_FAILURE.format(message=failure.message, status=failure.status)
    return Answer(exit_code=FAILURE_EXIT_CODE, stdout=compact(body) + "\n", stderr=stderr + "\n")


def graphql_failure(failure: GraphQLError) -> Answer:
    body = {"errors": [{"message": failure.message}]}
    stderr = messages.GRAPHQL_FAILURE.format(message=failure.message)
    return Answer(exit_code=FAILURE_EXIT_CODE, stdout=compact(body) + "\n", stderr=stderr + "\n")


def api(options: argparse.Namespace, invocation: Invocation) -> Answer:
    try:
        request = api_request(options, invocation)
        documents = respond(request, ApiContext(root=invocation.root, now=invocation.now))
    except ApiError as failure:
        return api_failure(failure)
    except GraphQLError as failure:
        return graphql_failure(failure)
    return succeeded("".join(compact(document) for document in documents) + "\n", "")


COMMANDS: Final[dict[tuple[str, ...], Command]] = {
    ("issue", "list"): Command(build_issue_list, issue_list, False),
    ("issue", "view"): Command(build_issue_view, issue_show, False),
    ("issue", "create"): Command(build_issue_create, issue_create, True),
    ("issue", "comment"): Command(build_issue_comment, issue_comment, True),
    ("issue", "edit"): Command(build_issue_edit, issue_edit, True),
    ("issue", "close"): Command(build_issue_close, issue_close, True),
    ("issue", "reopen"): Command(build_issue_reopen, issue_reopen, True),
    ("pr", "list"): Command(build_pr_list, pr_list, False),
    ("pr", "view"): Command(build_pr_view, pr_view, False),
    ("pr", "create"): Command(build_pr_create, pr_create, True),
    ("pr", "diff"): Command(build_pr_diff, pr_diff, False),
    ("pr", "close"): Command(build_pr_close, pr_close, True),
    ("label", "create"): Command(build_label_create, label_create, True),
    ("label", "list"): Command(build_label_list, label_list, False),
    API_COMMAND: Command(build_api, api, False),
}


def command_of(arguments: list[str]) -> tuple[tuple[str, ...], list[str]]:
    for name in COMMANDS:
        if tuple(arguments[: len(name)]) == name:
            return name, arguments[len(name) :]
    shown = " ".join(arguments[:2])
    raise GhError(messages.UNSUPPORTED_COMMAND.format(command=shown))


def parsed(name: tuple[str, ...], rest: list[str]) -> argparse.Namespace:
    parser = GhParser(prog=f"gh {' '.join(name)}", add_help=False, allow_abbrev=False)
    COMMANDS[name].build(parser)
    return parser.parse_args(rest)


def exclusive(name: tuple[str, ...], options: argparse.Namespace) -> bool:
    if name == API_COMMAND:
        return api_method(options, bool(options.raw_field or options.field)) != GET
    return COMMANDS[name].exclusive


def run_gh(invocation: Invocation) -> Answer:
    if invocation.arguments[:1] == ["--version"]:
        return succeeded(messages.VERSION.format(root=invocation.root) + "\n", "")
    try:
        name, rest = command_of(invocation.arguments)
        options = parsed(name, rest)
        with locked(invocation.root, exclusive(name, options)):
            return COMMANDS[name].handler(options, invocation)
    except GhError as failure:
        return Answer(exit_code=FAILURE_EXIT_CODE, stdout="", stderr=failure.message + "\n")
