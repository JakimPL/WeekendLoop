from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Final

from weekend_loop.board import local_repository_path, open_board
from weekend_loop.claude_cli import (
    SETPRIV_BINARY,
    TIMEOUT_BINARY,
    UNWATCHED,
    agent_environment,
    read_oauth_token,
)
from weekend_loop.github import CONFIG_DIRECTORY_NAME, GH_BINARY, GitHubReader, read_token
from weekend_loop.limits import (
    NO_READING_REASON,
    live_window,
    percentage,
    read_usage,
    seven_day_decision,
)
from weekend_loop.models import (
    Backend,
    BudgetPolicy,
    CheckOutcome,
    IdentityPolicy,
    Policy,
    PreflightCheck,
    PreflightReport,
    RepoMode,
    RepoTarget,
    UsagePolicy,
    UsageReading,
)
from weekend_loop.policy import repo_target
from weekend_loop.workbench import GIT_BINARY

CLAUDE_BINARY: Final[str] = "claude"
SANDBOX_BINARIES: Final[tuple[str, ...]] = ("bwrap", "socat")
PROBE_TRANSCRIPT_FILENAME: Final[str] = "preflight-probe.jsonl"
SECRET_FILE_MODE_MASK: Final[int] = 0o077
VERSION_TIMEOUT_SECONDS: Final[int] = 20
DETAIL_TAIL_CHARACTERS: Final[int] = 200
PROBE_REFERENCE: Final[str] = "refs/heads/weekend-loop/preflight-probe"
ABSENT_COMMIT: Final[str] = "0" * 40
WRITABLE_STATUS: Final[str] = "422"
READ_ONLY_STATUS: Final[str] = "403"
REFUSED_STATUS: Final[str] = "401"
HIDDEN_STATUS: Final[str] = "404"
VISIBLE_STATUS: Final[str] = "200"
CONTENTS_LEVELS: Final[dict[RepoMode, str]] = {
    RepoMode.DRY_RUN: "Read-only",
    RepoMode.EXECUTE: "Read and write",
}
REFUSED_TOKEN_DETAIL: Final[str] = (
    "GitHub refused the token (HTTP 401): it is revoked or expired; save a new one to {path}"
)
UNSEEN_REPOSITORY_DETAIL: Final[str] = (
    "the token cannot see {slug}: add the repository under the token's Repository access"
)
NO_CONTENTS_DETAIL: Final[str] = (
    "the token sees {slug} but has no access to its contents: set Contents to {level}"
)
UNREACHABLE_DETAIL: Final[str] = (
    "the write probe answered HTTP {status}; the token cannot reach the repository"
)
STATUS_PATTERN: Final[re.Pattern[str]] = re.compile(r"\(HTTP (\d{3})\)")
SANDBOX_PROBE: Final[tuple[str, ...]] = (
    "bwrap",
    "--unshare-user",
    "--unshare-net",
    "--ro-bind",
    "/",
    "/",
    "--dev",
    "/dev",
    "--proc",
    "/proc",
    "true",
)
NPM_BINARY: Final[str] = "npm"
SOCKET_FILTER_PACKAGE: Final[Path] = Path("@anthropic-ai") / "sandbox-runtime"
SOCKET_FILTER_FIX: Final[str] = (
    "sandboxed commands can open Unix sockets outside the masked runtime directory; install the "
    "filter with `npm install -g @anthropic-ai/sandbox-runtime`"
)
SANDBOX_FIX: Final[str] = (
    "the worker's shell commands fail until bwrap may create user namespaces; allow them for "
    "/usr/bin/bwrap with an AppArmor profile (Ubuntu restricts them through "
    "kernel.apparmor_restrict_unprivileged_userns)"
)


def passed(name: str, required: bool, detail: str) -> PreflightCheck:
    return PreflightCheck(name=name, outcome=CheckOutcome.PASSED, required=required, detail=detail)


def failed(name: str, required: bool, detail: str) -> PreflightCheck:
    return PreflightCheck(name=name, outcome=CheckOutcome.FAILED, required=required, detail=detail)


def check_binary(name: str, binary: str, required: bool) -> PreflightCheck:
    location = shutil.which(binary)
    if location is None:
        return failed(name, required, f"{binary} is not on PATH")
    completed = subprocess.run(
        [binary, "--version"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=VERSION_TIMEOUT_SECONDS,
    )
    version = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else location
    return passed(name, required, version)


def check_secret_file(name: str, path: Path, required: bool) -> PreflightCheck:
    if not path.is_file():
        return failed(name, required, f"{path} is missing")
    if not path.read_text().strip():
        return failed(name, required, f"{path} is empty")
    mode = path.stat().st_mode & SECRET_FILE_MODE_MASK
    if mode:
        return failed(name, required, f"{path} is readable by others (mode {oct(mode)})")
    return passed(name, required, str(path))


def check_settings_file(name: str, path: Path, required: bool) -> PreflightCheck:
    if not path.is_file():
        return failed(name, required, f"{path} is missing")
    try:
        json.loads(path.read_text())
    except json.JSONDecodeError as error:
        return failed(name, required, f"{path} is not valid JSON: {error}")
    return passed(name, required, str(path))


def check_oauth_token(name: str, path: Path, required: bool) -> PreflightCheck:
    secret = check_secret_file(name, path, required)
    if secret.outcome is not CheckOutcome.PASSED:
        return secret
    try:
        read_oauth_token(path)
    except ValueError as error:
        return failed(name, required, str(error))
    return secret


def probe_reading(policy: Policy) -> UsageReading | None:
    token_path = policy.workspace.oauth_token_path
    if not token_path.is_file():
        return None
    try:
        token = read_oauth_token(token_path)
    except ValueError:
        return None
    environment = agent_environment(policy.agent_home, token, {})
    transcript = policy.state_dir / PROBE_TRANSCRIPT_FILENAME
    return read_usage(policy, policy.agent_home, environment, UNWATCHED, transcript).usage


def check_usage_headroom(
    reading: UsageReading | None, usage: UsagePolicy, now: datetime
) -> PreflightCheck:
    name = "allowance headroom"
    required = True
    if reading is None:
        return passed(name, required, NO_READING_REASON)
    window = live_window(reading.seven_day, now)
    if window is None:
        return passed(name, required, "the seven-day window has just reset")
    spent = seven_day_decision(window, usage, [])
    if spent is not None:
        return failed(name, required, spent.reason)
    return passed(
        name,
        required,
        f"seven-day at {percentage(window.utilization)}, "
        f"five-hour at {percentage(reading.five_hour.utilization)}",
    )


def check_operator_identity(identity: IdentityPolicy) -> PreflightCheck:
    name = "board operator"
    if identity.operator_login is not None:
        return passed(name, False, f"@{identity.operator_login} owns the board")
    return passed(
        name,
        False,
        "no identity.operator_login; the account behind the token counts as the operator",
    )


def check_weekly_reset(budget: BudgetPolicy, now: datetime) -> PreflightCheck:
    name = "weekly reset guard"
    if budget.weekly_reset_at is None:
        return passed(name, True, "no operator override; the live allowance reading governs")
    if budget.weekly_reset_at <= now:
        return failed(
            name,
            True,
            f"the recorded weekly reset {budget.weekly_reset_at:%Y-%m-%d %H:%M} has passed; "
            "update or clear budget.weekly_reset_at",
        )
    return passed(
        name, True, f"the run takes no task after {budget.weekly_reset_at:%Y-%m-%d %H:%M}"
    )


def check_usage_credits(reading: UsageReading | None, required: bool) -> PreflightCheck:
    name = "usage credits"
    if reading is None:
        return passed(name, required, NO_READING_REASON)
    if reading.is_using_overage:
        return failed(name, required, "the allowance is spent; the run would bill credits")
    return passed(name, required, "the subscription allowance covers the run")


def check_sandbox_starts(required: bool) -> PreflightCheck:
    name = "sandbox namespace"
    if shutil.which(SANDBOX_PROBE[0]) is None:
        return failed(name, required, f"{SANDBOX_PROBE[0]} is not on PATH")
    completed = subprocess.run(
        SANDBOX_PROBE,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=VERSION_TIMEOUT_SECONDS,
    )
    if completed.returncode == 0:
        return passed(name, required, "the worker's sandbox starts here")
    reason = completed.stderr.strip().splitlines()[0] if completed.stderr.strip() else "no output"
    return failed(name, required, f"{reason}; {SANDBOX_FIX}")


def global_node_modules() -> Path | None:
    if shutil.which(NPM_BINARY) is None:
        return None
    completed = subprocess.run(
        [NPM_BINARY, "root", "-g"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=VERSION_TIMEOUT_SECONDS,
    )
    root = completed.stdout.strip()
    return Path(root) if completed.returncode == 0 and root else None


def check_socket_filter(node_modules: Path | None, required: bool) -> PreflightCheck:
    name = "sandbox socket filter"
    if node_modules is None:
        return failed(name, required, f"{NPM_BINARY} is not on PATH; {SOCKET_FILTER_FIX}")
    package = node_modules / SOCKET_FILTER_PACKAGE
    if package.is_dir():
        return passed(name, required, f"Unix sockets are filtered by {package}")
    return failed(name, required, f"{package} is missing; {SOCKET_FILTER_FIX}")


def check_state_directory(state_directory: Path) -> PreflightCheck:
    name = "state directory"
    state_directory.mkdir(parents=True, exist_ok=True)
    probe = state_directory / ".preflight"
    probe.write_text("ok")
    probe.unlink()
    return passed(name, True, str(state_directory))


def check_local_board(repo: RepoTarget, state_directory: Path) -> PreflightCheck:
    name = f"local board for {repo.slug}"
    board = open_board(state_directory, repo.slug)
    if not board.exists():
        return failed(name, True, f"no board at {board.directory}; seed it first")
    repository = local_repository_path(state_directory, repo.slug)
    if not (repository / "HEAD").is_file():
        return failed(name, True, f"no repository at {repository}; seed it first")
    return passed(name, True, f"{len(board.open_issues())} open issues at {board.directory}")


def check_repository_access(repo: RepoTarget, state_directory: Path) -> PreflightCheck:
    name = f"github access to {repo.slug}"
    try:
        token = read_token(repo.token_path())
    except (FileNotFoundError, ValueError) as error:
        return failed(name, True, str(error))
    reader = GitHubReader(repo.slug, token, state_directory / CONFIG_DIRECTORY_NAME)
    status = write_probe_status(reader, repo.slug)
    writable = probe_outcome(status)
    if writable is None:
        read_status = read_probe_status(reader, repo.slug) if status == HIDDEN_STATUS else None
        return failed(name, True, unreachable_detail(repo, status, read_status))
    if repo.mode is RepoMode.DRY_RUN and writable:
        return failed(name, True, "the token can push to a dry-run repository; scope it read-only")
    if repo.mode is RepoMode.EXECUTE and not writable:
        return failed(name, True, "the token cannot push to an execute repository")
    return passed(name, True, f"push={str(writable).lower()}, mode={repo.mode.value}")


def write_probe_status(reader: GitHubReader, slug: str) -> str:
    arguments = [
        "api",
        "-X",
        "POST",
        f"repos/{slug}/git/refs",
        "-f",
        f"ref={PROBE_REFERENCE}",
        "-f",
        f"sha={ABSENT_COMMIT}",
    ]
    try:
        reader.run(arguments)
    except subprocess.CalledProcessError as error:
        return status_of(error)
    return WRITABLE_STATUS


def read_probe_status(reader: GitHubReader, slug: str) -> str:
    try:
        reader.run(["api", f"repos/{slug}", "--jq", ".full_name"])
    except subprocess.CalledProcessError as error:
        return status_of(error)
    return VISIBLE_STATUS


def unreachable_detail(repo: RepoTarget, write_status: str, read_status: str | None) -> str:
    if write_status == REFUSED_STATUS:
        return REFUSED_TOKEN_DETAIL.format(path=repo.token_path())
    if write_status == HIDDEN_STATUS and read_status == HIDDEN_STATUS:
        return UNSEEN_REPOSITORY_DETAIL.format(slug=repo.slug)
    if write_status == HIDDEN_STATUS and read_status == VISIBLE_STATUS:
        return NO_CONTENTS_DETAIL.format(slug=repo.slug, level=CONTENTS_LEVELS[repo.mode])
    return UNREACHABLE_DETAIL.format(status=write_status)


def status_of(error: subprocess.CalledProcessError) -> str:
    body = error.stdout or ""
    try:
        answered = json.loads(body)
    except json.JSONDecodeError:
        answered = {}
    status = answered.get("status") if isinstance(answered, dict) else None
    if isinstance(status, str):
        return status
    match = STATUS_PATTERN.search(error.stderr or "")
    return match.group(1) if match else "unknown"


def probe_outcome(status: str) -> bool | None:
    if status == WRITABLE_STATUS:
        return True
    if status == READ_ONLY_STATUS:
        return False
    return None


def github_checks(repo: RepoTarget, repo_key: str, state_directory: Path) -> list[PreflightCheck]:
    return [
        check_binary("gh", GH_BINARY, True),
        check_secret_file(f"{repo_key} github token", repo.token_path(), True),
        check_repository_access(repo, state_directory),
    ]


def backend_checks(repo: RepoTarget, repo_key: str, state_directory: Path) -> list[PreflightCheck]:
    if repo.backend is Backend.LOCAL:
        return [check_local_board(repo, state_directory)]
    return github_checks(repo, repo_key, state_directory)


def run_preflight(
    policy: Policy, repo_key: str, now: datetime, reading: UsageReading | None
) -> PreflightReport:
    repo = repo_target(policy, repo_key)
    writes = repo.mode is RepoMode.EXECUTE
    checks = [
        check_binary("claude", CLAUDE_BINARY, True),
        check_binary("git", GIT_BINARY, True),
        check_binary("setpriv", SETPRIV_BINARY, True),
        check_binary("timeout", TIMEOUT_BINARY, True),
        check_state_directory(policy.state_dir),
        check_oauth_token("claude oauth token", policy.workspace.oauth_token_path, True),
        check_settings_file("assessor settings", policy.settings.assessor, True),
        check_settings_file("worker settings", policy.settings.worker, writes),
        *[check_binary(f"sandbox {binary}", binary, writes) for binary in SANDBOX_BINARIES],
        check_sandbox_starts(False),
        check_socket_filter(global_node_modules(), False),
        check_operator_identity(policy.identity),
        check_weekly_reset(policy.budget, now),
        check_usage_headroom(reading, policy.usage, now),
        check_usage_credits(reading, True),
        *backend_checks(repo, repo_key, policy.state_dir),
    ]
    return PreflightReport(repo_key=repo_key, mode=repo.mode, checked_at=now, checks=checks)


def render_preflight(report: PreflightReport) -> str:
    lines = [f"preflight {report.repo_key} ({report.mode.value})"]
    for check in report.checks:
        marker = {
            CheckOutcome.PASSED: "ok  ",
            CheckOutcome.FAILED: "FAIL" if check.required else "warn",
            CheckOutcome.SKIPPED: "skip",
        }[check.outcome]
        lines.append(f"  {marker}  {check.name}: {check.detail}")
    lines.append("clear to run" if report.clear_to_run else "blocked")
    return "\n".join(lines)
