import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest

from weekend_loop.demo.playground import git
from weekend_loop.github import blockers_from_pages
from weekend_loop.github_queries import BLOCKERS_QUERY
from weekend_loop.local_github import messages
from weekend_loop.local_github.commands import Answer, Invocation, run_gh
from weekend_loop.local_github.paths import bare_repository
from weekend_loop.local_github.payloads import issue_id
from weekend_loop.local_github.repository import initialise_bare
from weekend_loop.local_github.store import board_at, create_board, write_account
from weekend_loop.models import BoardAccount
from weekend_loop.preflight import status_of

SLUG: Final[str] = "example-org/example-board"
READ_ONLY_SLUG: Final[str] = "example-org/read-only"
OPERATOR: Final[str] = "example-operator"
BASE: Final[str] = "main"
BRANCH: Final[str] = "weekend/1-greeting"
NOW: Final[datetime] = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
LATER: Final[datetime] = NOW + timedelta(hours=1)
ISSUE_LIST_FIELDS: Final[str] = (
    "number,title,body,labels,assignees,milestone,author,createdAt,updatedAt,url"
)
ABSENT_COMMIT: Final[str] = "0" * 40


def gh(
    root: Path,
    arguments: list[str],
    stdin: str = "",
    environment: dict[str, str] | None = None,
    working_directory: Path | None = None,
    now: datetime = NOW,
) -> Answer:
    return run_gh(
        Invocation(
            root=root,
            arguments=arguments,
            read_stdin=lambda: stdin,
            environment=environment or {},
            working_directory=working_directory or root,
            now=now,
        )
    )


def json_of(answer: Answer) -> Any:  # noqa: ANN401
    assert answer.exit_code == 0, answer.stderr
    return json.loads(answer.stdout)


def push_branches(root: Path, checkout: Path) -> None:
    repository = bare_repository(root, SLUG)
    initialise_bare(repository, BASE)
    checkout.mkdir()
    git(["init", "--initial-branch", BASE], cwd=checkout)
    (checkout / "README.md").write_text("# Pocketchat\n")
    git(["add", "--all"], cwd=checkout)
    git(["commit", "--message", "Added: the README"], cwd=checkout)
    git(["remote", "add", "origin", str(repository)], cwd=checkout)
    git(["push", "origin", BASE], cwd=checkout)
    git(["checkout", "-b", BRANCH], cwd=checkout)
    (checkout / "README.md").write_text("# Pocketchat\n\nSay hello.\n")
    git(["commit", "--all", "--message", "Added: a greeting"], cwd=checkout)
    git(["push", "origin", BRANCH], cwd=checkout)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    board_root = tmp_path / "board"
    write_account(board_root, BoardAccount(login=OPERATOR))
    create_board(board_root, SLUG, True)
    create_board(board_root, READ_ONLY_SLUG, False)
    initialise_bare(bare_repository(board_root, READ_ONLY_SLUG), BASE)
    push_branches(board_root, tmp_path / "checkout")
    return board_root


def create_issue(root: Path, title: str, labels: list[str]) -> int:
    arguments = ["issue", "create", "--repo", SLUG, "--title", title, "--body-file", "-"]
    for label in labels:
        arguments.extend(["--label", label])
    answer = gh(root, arguments, stdin=f"Body of {title}\n")
    assert answer.exit_code == 0, answer.stderr
    return int(answer.stdout.strip().rsplit("/", 1)[1])


def failure_of(answer: Answer, arguments: list[str]) -> subprocess.CalledProcessError:
    return subprocess.CalledProcessError(answer.exit_code, arguments, answer.stdout, answer.stderr)


def test_the_account_answers_as_the_viewer(root: Path) -> None:
    assert json_of(gh(root, ["api", "user"]))["login"] == OPERATOR
    assert gh(root, ["--version"]).stdout.startswith("gh version local")


def test_issues_come_back_newest_first_in_github_shapes(root: Path) -> None:
    first = create_issue(root, "The New chat button does nothing", ["enhancement"])
    second = create_issue(root, "README still has TODOs", ["documentation"])
    listed = json_of(gh(root, ["issue", "list", "--repo", SLUG, "--json", ISSUE_LIST_FIELDS]))
    assert [issue["number"] for issue in listed] == [second, first]
    issue = listed[1]
    assert issue["labels"][0]["name"] == "enhancement" and issue["labels"][0]["color"]
    assert issue["author"]["login"] == OPERATOR
    assert issue["assignees"] == [] and issue["milestone"] is None
    assert issue["createdAt"] == "2026-10-01T12:00:00Z"
    assert datetime.fromisoformat(issue["updatedAt"]) == NOW
    assert issue["url"] == f"https://board.invalid/{SLUG}/issues/{first}"
    limited = json_of(gh(root, ["issue", "list", "-R", SLUG, "-L", "1", "--json", "number"]))
    assert limited == [{"number": second}]
    labelled = gh(root, ["issue", "list", "-R", SLUG, "-l", "documentation", "--json", "number"])
    assert json_of(labelled) == [{"number": second}]


def test_an_issue_with_a_label_the_repository_lacks_is_refused(root: Path) -> None:
    answer = gh(
        root, ["issue", "create", "-R", SLUG, "-t", "Title", "-b", "Body", "-l", "weekend:auto"]
    )
    assert answer.exit_code == 1
    assert answer.stderr.strip() == "could not add label: 'weekend:auto' not found"


def test_labels_are_added_and_removed_like_github(root: Path) -> None:
    number = create_issue(root, "Ratings for answers", ["enhancement"])
    created = gh(root, ["label", "create", "weekend:review", "-R", SLUG, "--force"])
    assert created.exit_code == 0
    edit = ["issue", "edit", str(number), "-R", SLUG]
    added = gh(root, [*edit, "--add-label", "weekend:review,bug"], now=LATER)
    assert added.stdout.strip() == f"https://board.invalid/{SLUG}/issues/{number}"
    issue = board_at(root, SLUG).read_issue(number)
    assert issue.labels == ["enhancement", "weekend:review", "bug"]
    assert issue.updated_at == LATER
    assert gh(root, [*edit, "--remove-label", "question"]).exit_code == 0
    refused = gh(root, [*edit, "--add-label", "weekend:unknown"])
    assert refused.exit_code == 1
    assert "'weekend:unknown' not found" in refused.stderr


def test_an_existing_label_needs_force(root: Path) -> None:
    answer = gh(root, ["label", "create", "bug", "-R", SLUG])
    assert answer.exit_code == 1 and "--force" in answer.stderr
    forced = gh(root, ["label", "create", "bug", "-R", SLUG, "-d", "Broken", "-c", "000000", "-f"])
    assert forced.exit_code == 0
    labels = json_of(gh(root, ["label", "list", "-R", SLUG, "--json", "name,color"]))
    assert {"name": "bug", "color": "000000"} in labels


def test_a_comment_is_signed_by_the_viewer_and_bumps_the_issue(root: Path) -> None:
    number = create_issue(root, "Ratings for answers", [])
    comment = ["issue", "comment", str(number), "--repo", SLUG, "--body-file", "-"]
    answer = gh(root, comment, stdin="1. Stars.\n", now=LATER)
    assert answer.stdout.strip().startswith(f"https://board.invalid/{SLUG}/issues/{number}#")
    viewed = json_of(gh(root, ["issue", "view", str(number), "-R", SLUG, "--json", "comments"]))
    [only] = viewed["comments"]
    assert only["author"] == {"login": OPERATOR}
    assert only["body"] == "1. Stars.\n"
    assert only["createdAt"] == "2026-10-01T13:00:00Z"
    assert board_at(root, SLUG).read_issue(number).updated_at == LATER


def test_an_unknown_json_field_names_the_available_ones(root: Path) -> None:
    answer = gh(root, ["issue", "list", "-R", SLUG, "--json", "number,reactions"])
    assert answer.exit_code == 1
    assert answer.stderr.startswith('Unknown JSON field: "reactions"')
    assert "  updatedAt" in answer.stderr


@pytest.mark.parametrize(
    ("arguments", "refused"),
    [
        (["api", "user", "--jq", ".login"], "unrecognized arguments: --jq .login"),
        (["issue", "list", "-R", SLUG, "--web"], "unrecognized arguments: --web"),
        (["repo", "view"], "`gh repo view` is not available on the local board"),
    ],
)
def test_what_the_local_board_does_not_offer_is_refused_by_name(
    root: Path, arguments: list[str], refused: str
) -> None:
    answer = gh(root, arguments)
    assert answer.exit_code == 1
    assert refused in answer.stderr


def test_an_unknown_repository_is_missing_for_commands_and_the_api(root: Path) -> None:
    listed = gh(root, ["issue", "list", "-R", "example-org/elsewhere"])
    assert "Could not resolve to a Repository" in listed.stderr
    arguments = ["api", "repos/example-org/elsewhere"]
    answer = gh(root, arguments)
    assert answer.exit_code == 1
    assert json.loads(answer.stdout)["status"] == "404"
    assert answer.stderr.strip() == "gh: Not Found (HTTP 404)"
    assert status_of(failure_of(answer, arguments)) == "404"


@pytest.mark.parametrize(("slug", "status"), [(SLUG, "422"), (READ_ONLY_SLUG, "403")])
def test_the_write_probe_answers_as_the_tokens_permission_says(
    root: Path, slug: str, status: str
) -> None:
    arguments = [
        "api",
        "-X",
        "POST",
        f"repos/{slug}/git/refs",
        "-f",
        "ref=refs/heads/weekend-loop/preflight-probe",
        "-f",
        f"sha={ABSENT_COMMIT}",
    ]
    answer = gh(root, arguments)
    assert answer.exit_code == 1
    assert status_of(failure_of(answer, arguments)) == status
    readable = json_of(gh(root, ["api", f"repos/{slug}"]))
    assert readable["full_name"] == slug
    assert readable["permissions"]["push"] is (status == "422")


class TestPullRequests:
    def test_a_pull_request_needs_its_head_branch(self, root: Path) -> None:
        create = ["pr", "create", "-R", SLUG, "--draft", "--base", BASE, "-t", "T", "-b", "B"]
        answer = gh(root, [*create, "--head", "weekend/9-missing"])
        assert answer.exit_code == 1
        assert answer.stderr.strip() == messages.BRANCH_MISSING

    def test_a_branch_without_commits_is_refused(self, root: Path) -> None:
        create = ["pr", "create", "-R", SLUG, "--head", BASE, "--base", BASE, "-t", "T", "-b", "B"]
        assert "No commits between main and main" in gh(root, create).stderr

    def test_a_draft_opens_once_lists_and_shows_its_diff(self, root: Path) -> None:
        create = ["pr", "create", "-R", SLUG, "--draft", "--head", BRANCH, "--base", BASE]
        opened = gh(root, [*create, "--title", "Greeting", "--body-file", "-"], stdin="Refs #1\n")
        url = opened.stdout.strip()
        assert url.startswith(f"https://board.invalid/{SLUG}/pull/")
        again = gh(root, [*create, "--title", "Greeting", "--body", "again"])
        assert again.exit_code == 1 and "already exists" in again.stderr and url in again.stderr
        fields = "number,headRefName,baseRefName,isDraft,url,state"
        [listed] = json_of(gh(root, ["pr", "list", "-R", SLUG, "--json", fields]))
        assert listed["headRefName"] == BRANCH and listed["baseRefName"] == BASE
        assert listed["isDraft"] is True and listed["url"] == url and listed["state"] == "OPEN"
        headed = gh(root, ["pr", "list", "-R", SLUG, "--head", BRANCH, "--json", "number"])
        assert json_of(headed) == [{"number": listed["number"]}]
        diff = gh(root, ["pr", "diff", str(listed["number"]), "-R", SLUG])
        assert "+Say hello." in diff.stdout
        assert gh(root, ["pr", "close", str(listed["number"]), "-R", SLUG]).exit_code == 0
        assert json_of(gh(root, ["pr", "list", "-R", SLUG, "--json", "number"])) == []
        closed = gh(root, ["pr", "list", "-R", SLUG, "--state", "all", "--json", "state"])
        assert json_of(closed) == [{"state": "CLOSED"}]

    def test_a_stack_records_pull_requests_that_exist(self, root: Path) -> None:
        create = ["pr", "create", "-R", SLUG, "--head", BRANCH, "--base", BASE, "-t", "T"]
        number = int(gh(root, [*create, "-b", "B"]).stdout.strip().rsplit("/", 1)[1])
        stack = ["api", "-X", "POST", f"repos/{SLUG}/stacks", "-H", "X: 1", "--input", "-"]
        assert gh(root, stack, stdin=json.dumps({"pull_requests": [number]})).exit_code == 0
        assert board_at(root, SLUG).read_index().stacks == [[number]]
        refused = gh(root, stack, stdin=json.dumps({"pull_requests": [number, 99]}))
        assert refused.exit_code == 1 and "(HTTP 422)" in refused.stderr


class TestDependencies:
    def test_a_blocker_is_linked_by_its_id_and_listed(self, root: Path) -> None:
        parent = create_issue(root, "The New chat button does nothing", [])
        child = create_issue(root, "Count the new chats", [])
        issue = json_of(gh(root, ["api", f"repos/{SLUG}/issues/{parent}"]))
        assert issue["id"] == issue_id(parent) and issue["number"] == parent
        link = ["api", "-X", "POST", f"repos/{SLUG}/issues/{child}/dependencies/blocked_by"]
        linked = gh(root, [*link, "-F", f"issue_id={issue['id']}"])
        assert json_of(linked)["number"] == parent
        listed = json_of(gh(root, ["api", f"repos/{SLUG}/issues/{child}/dependencies/blocked_by"]))
        assert [blocker["number"] for blocker in listed] == [parent]
        again = gh(root, [*link, "-F", f"issue_id={issue['id']}"])
        assert again.exit_code == 1 and "(HTTP 422)" in again.stderr

    def test_an_id_sent_as_text_is_refused_as_github_does(self, root: Path) -> None:
        child = create_issue(root, "Count the new chats", [])
        link = ["api", "-X", "POST", f"repos/{SLUG}/issues/{child}/dependencies/blocked_by"]
        answer = gh(root, [*link, "-f", f"issue_id={issue_id(child)}"])
        assert answer.exit_code == 1 and "is not an integer" in answer.stdout

    def test_the_blocked_by_query_pages_open_issues_and_names_closed_blockers(
        self, root: Path
    ) -> None:
        numbers = [create_issue(root, f"Issue {index}", []) for index in range(101)]
        parent, child = numbers[0], numbers[-1]
        closed = numbers[1]
        link = ["api", "-X", "POST", f"repos/{SLUG}/issues/{child}/dependencies/blocked_by"]
        for blocker in (parent, closed):
            assert gh(root, [*link, "-F", f"issue_id={issue_id(blocker)}"]).exit_code == 0
        assert gh(root, ["issue", "close", str(closed), "-R", SLUG]).exit_code == 0
        query = ["api", "graphql", "-f", f"query={BLOCKERS_QUERY}"]
        repository = ["-f", "owner=example-org", "-f", "name=example-board"]
        answer = gh(root, [*query, "--paginate", *repository])
        blockers = blockers_from_pages(answer.stdout)
        assert len(blockers) == len(numbers) - 1
        assert blockers[child] == [parent]
        single = blockers_from_pages(gh(root, [*query, *repository]).stdout)
        assert len(single) == 100

    def test_any_other_query_is_refused(self, root: Path) -> None:
        answer = gh(root, ["api", "graphql", "-f", "query={ viewer { login } }"])
        assert answer.exit_code == 1
        assert answer.stderr.strip() == f"gh: {messages.UNSUPPORTED_QUERY}"


def test_the_repository_comes_from_gh_repo_or_the_clone_s_origin(
    root: Path, tmp_path: Path
) -> None:
    number = create_issue(root, "README still has TODOs", [])
    environment = {"GH_REPO": f"github.com/{SLUG}"}
    by_variable = gh(
        root, ["issue", "view", str(number), "--json", "title"], environment=environment
    )
    assert json_of(by_variable) == {"title": "README still has TODOs"}
    by_origin = gh(
        root, ["issue", "list", "--json", "number"], working_directory=tmp_path / "checkout"
    )
    assert json_of(by_origin) == [{"number": number}]
    nowhere = gh(root, ["issue", "list"], working_directory=tmp_path)
    assert nowhere.exit_code == 1 and "could not determine base repo" in nowhere.stderr
