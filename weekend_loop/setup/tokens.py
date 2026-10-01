from __future__ import annotations

import getpass
import os
from pathlib import Path
from typing import Final, Protocol

from weekend_loop.claude_cli import read_oauth_token
from weekend_loop.models import CheckOutcome, RepoMode, RepoTarget
from weekend_loop.preflight import check_repository_access
from weekend_loop.setup import messages
from weekend_loop.setup.outcomes import StepOutcome, done, failed, todo

SECRET_DIRECTORY_MODE: Final[int] = 0o700
SECRET_FILE_MODE: Final[int] = 0o600
AGREEMENTS: Final[frozenset[str]] = frozenset({"y", "yes"})


class Operator(Protocol):
    can_ask: bool

    def secret(self, prompt: str) -> str: ...

    def agree(self, question: str) -> bool: ...


class TerminalOperator:
    def __init__(self, can_ask: bool, assume_yes: bool) -> None:
        self.can_ask = can_ask
        self.assume_yes = assume_yes

    def secret(self, prompt: str) -> str:
        return getpass.getpass(prompt)

    def agree(self, question: str) -> bool:
        if self.assume_yes:
            return True
        return input(question).strip().lower() in AGREEMENTS


def save_secret(path: Path, value: str) -> None:
    path.parent.mkdir(mode=SECRET_DIRECTORY_MODE, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, SECRET_FILE_MODE)
    with os.fdopen(descriptor, "w") as secret:
        secret.write(f"{value}\n")
    path.chmod(SECRET_FILE_MODE)


def claude_token_saved(path: Path) -> bool:
    try:
        read_oauth_token(path)
    except (FileNotFoundError, ValueError):
        return False
    return True


def claude_token_step(path: Path, operator: Operator) -> StepOutcome:
    if claude_token_saved(path):
        return done(messages.CLAUDE_TOKEN, messages.TOKEN_KEPT)
    if not operator.can_ask:
        return todo(messages.CLAUDE_TOKEN, messages.TOKEN_MISSING.format(path=path))
    save_secret(path, operator.secret(messages.CLAUDE_TOKEN_PROMPT).strip())
    try:
        read_oauth_token(path)
    except ValueError as error:
        path.unlink()
        return todo(messages.CLAUDE_TOKEN, str(error))
    return done(messages.CLAUDE_TOKEN, messages.TOKEN_SAVED)


def github_token_saved(path: Path) -> bool:
    return path.is_file() and bool(path.read_text().strip())


def github_token_step(repo: RepoTarget, state_directory: Path, operator: Operator) -> StepOutcome:
    path = repo.token_path()
    if not github_token_saved(path):
        if not operator.can_ask:
            return todo(messages.GITHUB_TOKEN, messages.TOKEN_MISSING.format(path=path))
        answer = operator.secret(messages.GITHUB_TOKEN_PROMPT.format(slug=repo.slug)).strip()
        if not answer:
            return todo(messages.GITHUB_TOKEN, messages.TOKEN_MISSING.format(path=path))
        save_secret(path, answer)
    check = check_repository_access(repo, state_directory)
    if check.outcome is not CheckOutcome.PASSED:
        return failed(messages.GITHUB_TOKEN, check.detail)
    access = messages.TOKEN_CAN_PUSH if repo.mode is RepoMode.EXECUTE else messages.TOKEN_CAN_READ
    return done(messages.GITHUB_TOKEN, access.format(slug=repo.slug))
