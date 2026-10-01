from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Final

from weekend_loop.commands import DEFAULT_COMMAND_TIMEOUT_SECONDS, run_command
from weekend_loop.confinement import Confinement
from weekend_loop.models import CommandResult, RepoTarget

ACCEPTANCE_MAP_FILENAME: Final[str] = "acceptance.json"
ACCEPTANCE_DIRECTORY_NAME: Final[str] = ".weekend-acceptance"
TEST_FILE_PLACEHOLDER: Final[str] = "{test_file}"


def acceptance_map_path(state_directory: Path) -> Path:
    return state_directory / ACCEPTANCE_MAP_FILENAME


def load_acceptance_map(path: Path, root: Path) -> dict[int, Path]:
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text())
    return {int(number): root / location for number, location in raw.items()}


def run_acceptance_test(
    repo: RepoTarget,
    test_file: Path,
    workbench: Path,
    environment: dict[str, str],
    confinement: Confinement | None,
) -> CommandResult | None:
    if repo.acceptance_command is None:
        return None
    directory = workbench / ACCEPTANCE_DIRECTORY_NAME
    directory.mkdir(parents=True, exist_ok=True)
    copied = directory / test_file.name
    shutil.copyfile(test_file, copied)
    command = repo.acceptance_command.replace(
        TEST_FILE_PLACEHOLDER, str(copied.relative_to(workbench))
    )
    try:
        return run_command(
            command, workbench, environment, DEFAULT_COMMAND_TIMEOUT_SECONDS, confinement
        )
    finally:
        shutil.rmtree(directory, ignore_errors=True)
