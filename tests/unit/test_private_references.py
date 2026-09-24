from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Final

import pytest

REPOSITORY_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
GIT_BINARY: Final[str] = "git"
EMAIL_PATTERN: Final[re.Pattern[str]] = re.compile(r"(?<![\w.+-])([\w.+-]+)@([\w-]+(?:\.[\w-]+)+)")
REMOTE_PATTERN: Final[re.Pattern[str]] = re.compile(r"github\.com[/:]([\w.-]+)/[\w.-]+")
SLUG_PATTERN: Final[re.Pattern[str]] = re.compile(r"""slug:\s*["']?([\w.-]+)/[\w.-]+""")
HOME_PATTERN: Final[re.Pattern[str]] = re.compile(r"/(?:home|Users)/[\w.-]+")
RESERVED_DOMAINS: Final[frozenset[str]] = frozenset(
    {"example.com", "example.net", "example.org", "example.invalid", "invalid", "localhost", "test"}
)
ALLOWED_ADDRESSES: Final[frozenset[str]] = frozenset({"git@github.com"})
ALLOWED_OWNERS: Final[frozenset[str]] = frozenset(
    {
        "jakimpl",
        "example-org",
        "your-org",
        "owner",
        "actions",
        "astral-sh",
        "anthropics",
        "pre-commit",
    }
)
BRAND_TERMS: Final[tuple[str, ...]] = ("deepsense", "sensai", "harbourlog")
GUARD_PATH: Final[str] = "tests/unit/test_private_references.py"
ALLOWED_PATHS: Final[frozenset[str]] = frozenset({GUARD_PATH})
SKIPPED_SUFFIXES: Final[tuple[str, ...]] = (".lock", ".png", ".jpg", ".gif", ".ico", ".woff2")


def tracked_files() -> list[Path]:
    listed = subprocess.run(
        [GIT_BINARY, "ls-files", "-z"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [Path(name) for name in listed.stdout.split("\0") if name]


def readable_files() -> list[Path]:
    return [path for path in tracked_files() if not path.name.endswith(SKIPPED_SUFFIXES)]


def numbered_lines(path: Path) -> list[tuple[int, str]]:
    text = (REPOSITORY_ROOT / path).read_text(errors="ignore")
    return list(enumerate(text.splitlines(), start=1))


def offences(pattern: re.Pattern[str], allowed: frozenset[str], group: int) -> list[str]:
    found: list[str] = []
    for path in readable_files():
        for number, line in numbered_lines(path):
            for match in pattern.finditer(line):
                if match.group(group).lower() not in allowed:
                    found.append(f"{path}:{number}: {match.group(0)}")
    return found


def test_no_tracked_file_carries_an_address_outside_the_reserved_domains() -> None:
    found = [
        offence
        for offence in offences(EMAIL_PATTERN, RESERVED_DOMAINS, 2)
        if not any(address in offence for address in ALLOWED_ADDRESSES)
    ]
    assert found == []


@pytest.mark.parametrize("pattern", [REMOTE_PATTERN, SLUG_PATTERN])
def test_no_tracked_file_names_a_repository_owner_outside_the_examples(
    pattern: re.Pattern[str],
) -> None:
    assert offences(pattern, ALLOWED_OWNERS, 1) == []


def test_no_tracked_file_carries_someones_home_directory() -> None:
    found = [
        f"{path}:{number}: {match.group(0)}"
        for path in readable_files()
        for number, line in numbered_lines(path)
        for match in [HOME_PATTERN.search(line)]
        if match is not None
    ]
    assert found == []


def test_no_tracked_file_names_the_organization_or_a_project_it_retired() -> None:
    found = [
        f"{path}:{number}"
        for path in readable_files()
        if str(path) not in ALLOWED_PATHS
        for number, line in numbered_lines(path)
        if any(term in line.lower() for term in BRAND_TERMS)
    ]
    assert found == []


def test_the_guard_excuses_only_itself() -> None:
    assert ALLOWED_PATHS == frozenset({GUARD_PATH})
