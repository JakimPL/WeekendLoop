import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest

from tests.support.fakes import install_fake
from weekend_loop.commands import run_command
from weekend_loop.github import GitHubReader
from weekend_loop.models import CheckOutcome
from weekend_loop.preflight import check_binary
from weekend_loop.workbench import run_git

DEVICE_NULL: Final[str] = "/dev/null"
SYSTEM_PATH: Final[str] = "/usr/bin:/bin"
RECORDING_SCRIPT: Final[str] = """#!/bin/sh
readlink /proc/self/fd/0 > "$0.stdin"
if [ "$1" = "issue" ]; then cat > "$0.body"; fi
echo "fake 1.0"
"""
SLEEPING_SCRIPT: Final[str] = "#!/bin/sh\nexec sleep 30\n"
COMMAND_TIMEOUT_SECONDS: Final[int] = 10


@pytest.fixture
def piped_stdin() -> Iterator[None]:
    # pytest points fd 0 at /dev/null; a pipe nobody writes shows whether a child inherits it.
    read_end, write_end = os.pipe()
    saved = os.dup(0)
    os.dup2(read_end, 0)
    try:
        yield
    finally:
        os.dup2(saved, 0)
        for descriptor in (saved, read_end, write_end):
            os.close(descriptor)


@pytest.fixture
def binaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "bin"
    directory.mkdir()
    monkeypatch.setenv("PATH", f"{directory}:{SYSTEM_PATH}")
    return directory


def recorded_stdin(binary: Path) -> str:
    return binary.with_name(f"{binary.name}.stdin").read_text().strip()


def build_reader(tmp_path: Path) -> GitHubReader:
    return GitHubReader("owner/repo", "fake-token", tmp_path / "gh-config")


def test_git_reads_nothing_from_the_orchestrator_input(
    tmp_path: Path, binaries: Path, piped_stdin: None
) -> None:
    git = install_fake(binaries, "git", RECORDING_SCRIPT)

    run_git(["status"], tmp_path, {"PATH": f"{binaries}:{SYSTEM_PATH}"})

    assert recorded_stdin(git) == DEVICE_NULL


def test_a_git_call_that_hangs_ends_in_a_timeout(
    tmp_path: Path, binaries: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(binaries, "git", SLEEPING_SCRIPT)
    monkeypatch.setattr("weekend_loop.workbench.GIT_TIMEOUT_SECONDS", 1)

    with pytest.raises(subprocess.TimeoutExpired):
        run_git(["fetch", "origin"], tmp_path, {"PATH": f"{binaries}:{SYSTEM_PATH}"})


def test_gh_reads_nothing_from_the_orchestrator_input_without_a_body(
    tmp_path: Path, binaries: Path, piped_stdin: None
) -> None:
    gh = install_fake(binaries, "gh", RECORDING_SCRIPT)

    build_reader(tmp_path).run(["api", "user"])

    assert recorded_stdin(gh) == DEVICE_NULL


def test_gh_still_receives_a_piped_body(tmp_path: Path, binaries: Path) -> None:
    gh = install_fake(binaries, "gh", RECORDING_SCRIPT)

    build_reader(tmp_path).run(["issue", "comment", "7", "--body-file", "-"], stdin="the body")

    assert gh.with_name("gh.body").read_text() == "the body"


def test_a_gh_call_that_hangs_ends_in_a_timeout(
    tmp_path: Path, binaries: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(binaries, "gh", SLEEPING_SCRIPT)
    monkeypatch.setattr("weekend_loop.github.GH_TIMEOUT_SECONDS", 1)

    with pytest.raises(subprocess.TimeoutExpired):
        build_reader(tmp_path).run(["issue", "list"])


def test_a_policy_command_reads_nothing_from_the_orchestrator_input(
    tmp_path: Path, piped_stdin: None
) -> None:
    result = run_command(
        "readlink /proc/self/fd/0", tmp_path, {"PATH": SYSTEM_PATH}, COMMAND_TIMEOUT_SECONDS
    )

    assert result.output_tail == DEVICE_NULL


def test_a_version_check_reads_nothing_from_the_orchestrator_input(
    binaries: Path, piped_stdin: None
) -> None:
    tool = install_fake(binaries, "tool", RECORDING_SCRIPT)

    check = check_binary("tool", "tool", True)

    assert check.outcome is CheckOutcome.PASSED
    assert recorded_stdin(tool) == DEVICE_NULL
