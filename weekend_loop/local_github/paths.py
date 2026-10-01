from pathlib import Path
from typing import Final

BOARD_DIRECTORY_NAME: Final[str] = "board"
REPOSITORY_DIRECTORY_NAME: Final[str] = "repo.git"
ISSUES_DIRECTORY_NAME: Final[str] = "issues"
COMMENTS_DIRECTORY_NAME: Final[str] = "comments"
PULL_REQUESTS_DIRECTORY_NAME: Final[str] = "pull_requests"
INDEX_FILENAME: Final[str] = "index.json"
ACCOUNT_FILENAME: Final[str] = "account.json"
LOCK_FILENAME: Final[str] = ".lock"
WRAPPER_DIRECTORY_NAME: Final[str] = "bin"
WRAPPER_NAME: Final[str] = "gh"
SLUG_SEPARATOR: Final[str] = "__"
URL_ROOT: Final[str] = "https://board.invalid"
API_ROOT: Final[str] = "https://api.board.invalid"
UNREACHABLE_HOST_SUFFIX: Final[str] = ".invalid"


def board_root(state_directory: Path) -> Path:
    return state_directory / BOARD_DIRECTORY_NAME


def repository_directory(root: Path, slug: str) -> Path:
    return root / slug.replace("/", SLUG_SEPARATOR)


def slug_of(directory: Path) -> str:
    return directory.name.replace(SLUG_SEPARATOR, "/", 1)


def bare_repository(root: Path, slug: str) -> Path:
    return repository_directory(root, slug) / REPOSITORY_DIRECTORY_NAME


def local_repository_path(state_directory: Path, slug: str) -> Path:
    return bare_repository(board_root(state_directory), slug)


def account_path(root: Path) -> Path:
    return root / ACCOUNT_FILENAME


def lock_path(root: Path) -> Path:
    return root / LOCK_FILENAME


def wrapper_path(state_directory: Path) -> Path:
    return board_root(state_directory) / WRAPPER_DIRECTORY_NAME / WRAPPER_NAME


def issue_url(slug: str, number: int) -> str:
    return f"{URL_ROOT}/{slug}/issues/{number}"


def pull_request_url(slug: str, number: int) -> str:
    return f"{URL_ROOT}/{slug}/pull/{number}"


def comment_url(slug: str, number: int, comment_id: int) -> str:
    return f"{issue_url(slug, number)}#issuecomment-{comment_id}"


def api_issue_url(slug: str, number: int) -> str:
    return f"{API_ROOT}/repos/{slug}/issues/{number}"
