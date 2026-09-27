from __future__ import annotations

import re
import shlex
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Final

from weekend_loop.commands import DEFAULT_COMMAND_TIMEOUT_SECONDS, run_command
from weekend_loop.models import ChangedFile, CommandResult, GateResult

SECRET_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    "anthropic key": re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}"),
    "github token": re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    "github fine-grained token": re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    "aws access key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}
CHANGED_PYTHON_FILES_PLACEHOLDER: Final[str] = "{changed_python_files}"
PYTHON_SUFFIX: Final[str] = ".py"
BINARY_MARKER: Final[str] = "-"
RENAME_SEPARATOR: Final[str] = " => "


def parse_numstat(output: str) -> list[ChangedFile]:
    files: list[ChangedFile] = []
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != 3:
            continue
        added, removed, path = fields
        binary = added == BINARY_MARKER or removed == BINARY_MARKER
        files.append(
            ChangedFile(
                path=path.split(RENAME_SEPARATOR)[-1].strip("{} "),
                added=0 if binary else int(added),
                removed=0 if binary else int(removed),
                binary=binary,
            )
        )
    return files


def diff_line_count(files: list[ChangedFile]) -> int:
    return sum(changed.added + changed.removed for changed in files)


def changed_python_files(files: list[ChangedFile]) -> list[str]:
    return [changed.path for changed in files if changed.path.endswith(PYTHON_SUFFIX)]


def binary_files(files: list[ChangedFile]) -> list[str]:
    return [changed.path for changed in files if changed.binary]


def path_matches(path: str, pattern: str) -> bool:
    if fnmatchcase(path, pattern):
        return True
    if pattern.startswith("**/") and fnmatchcase(path, pattern[3:]):
        return True
    prefix = pattern[:-3]
    return pattern.endswith("/**") and (path == prefix or path.startswith(f"{prefix}/"))


def forbidden_matches(files: list[ChangedFile], patterns: list[str]) -> list[str]:
    return sorted(
        {
            changed.path
            for changed in files
            for pattern in patterns
            if path_matches(changed.path, pattern)
        }
    )


def secret_matches(diff: str) -> list[str]:
    return sorted(name for name, pattern in SECRET_PATTERNS.items() if pattern.search(diff))


def render_command(command: str, python_files: list[str]) -> str | None:
    if CHANGED_PYTHON_FILES_PLACEHOLDER not in command:
        return command
    if not python_files:
        return None
    quoted = " ".join(shlex.quote(path) for path in python_files)
    return command.replace(CHANGED_PYTHON_FILES_PLACEHOLDER, quoted)


def run_gate_commands(
    commands: list[str],
    python_files: list[str],
    workbench: Path,
    environment: dict[str, str],
) -> list[CommandResult]:
    results: list[CommandResult] = []
    for command in commands:
        rendered = render_command(command, python_files)
        if rendered is None:
            continue
        result = run_command(rendered, workbench, environment, DEFAULT_COMMAND_TIMEOUT_SECONDS)
        results.append(result)
        if result.exit_code != 0:
            break
    return results


def evaluate_gate(
    files: list[ChangedFile],
    diff: str,
    results: list[CommandResult],
    commits: int,
    max_diff_lines: int,
    forbidden_paths: list[str],
) -> GateResult:
    forbidden = forbidden_matches(files, forbidden_paths)
    secrets = secret_matches(diff)
    binaries = binary_files(files)
    lines = diff_line_count(files)
    passed = (
        bool(files)
        and commits > 0
        and all(result.exit_code == 0 for result in results)
        and lines <= max_diff_lines
        and not forbidden
        and not secrets
        and not binaries
    )
    return GateResult(
        passed=passed,
        commands=results,
        diff_lines=lines,
        forbidden_paths_touched=forbidden,
        secret_matches=secrets,
        binary_files=binaries,
        commit_count=commits,
        changed_paths=[changed.path for changed in files],
    )
