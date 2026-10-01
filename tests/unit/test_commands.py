from __future__ import annotations

import shlex
from pathlib import Path
from typing import Final

from tests.unit.test_resume import process_gone
from weekend_loop.commands import TIMEOUT_EXIT_CODE, command_environment, run_command

SPAWN_GRANDCHILD: Final[str] = (
    "import subprocess, sys, time; "
    "child = subprocess.Popen(['sleep', '60']); "
    "open(sys.argv[1], 'w').write(str(child.pid)); "
    "time.sleep(60)"
)
SPAWN_TIMEOUT_SECONDS: Final[int] = 2


def test_a_command_reports_its_exit_code_and_the_tail_of_its_output(tmp_path: Path) -> None:
    result = run_command(
        "python3 -c \"import sys; print('built'); sys.exit(3)\"",
        tmp_path,
        command_environment({}),
        SPAWN_TIMEOUT_SECONDS,
    )
    assert result.exit_code == 3
    assert result.output_tail == "built"


def test_a_command_past_its_time_ends_with_every_process_it_started(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    result = run_command(
        f"python3 -c {shlex.quote(SPAWN_GRANDCHILD)} {pid_file}",
        tmp_path,
        command_environment({}),
        SPAWN_TIMEOUT_SECONDS,
    )
    assert result.exit_code == TIMEOUT_EXIT_CODE
    assert process_gone(int(pid_file.read_text()))
