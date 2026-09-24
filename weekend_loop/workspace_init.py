import shutil
from pathlib import Path
from typing import Final

from weekend_loop.config_view import reference_document
from weekend_loop.fences import NO_FORBIDDEN_PATHS, render_fences
from weekend_loop.models import Workspace
from weekend_loop.resources import ResourceKind, packaged_text

STARTER_CONFIG: Final[str] = "starter.yaml"
CONFIG_FILENAME: Final[str] = "config.yaml"
CONVENTIONS_FILENAME: Final[str] = "conventions.md"
AGENT_MEMORY: Final[str] = "CLAUDE.md"
WORKSPACE_MODE: Final[int] = 0o700
SECRET_MODE: Final[int] = 0o600
OPEN_MODE: Final[int] = 0o755
AGENT_MEMORY_PATH: Final[tuple[str, str]] = (".claude", "CLAUDE.md")
PROMPTS_README: Final[str] = (
    "# Prompt overrides\n\n"
    "A file named after one of Weekend Loop's own prompts wins over the packaged copy.\n"
    "`weekend-loop prompts export <name>` puts one here to edit.\n"
)
ACCEPTANCE_README: Final[str] = (
    "# Acceptance tests\n\n"
    "Hidden tests the worker never sees, named by `state/acceptance.json` and run against a\n"
    "delivered branch. The demo writes both; a repository of your own needs neither.\n"
)


def make_directory(path: Path, mode: int) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(mode)
    return path


def write_secret(path: Path, text: str) -> Path:
    make_directory(path.parent, WORKSPACE_MODE)
    path.write_text(text)
    path.chmod(SECRET_MODE)
    return path


def write_once(path: Path, text: str) -> Path | None:
    if path.exists():
        return None
    path.write_text(text)
    return path


def managed_directories(workspace: Workspace) -> tuple[tuple[Path, int], ...]:
    return (
        (workspace.root, WORKSPACE_MODE),
        (workspace.secrets_dir, WORKSPACE_MODE),
        (workspace.agent_home, WORKSPACE_MODE),
        (workspace.state_dir, WORKSPACE_MODE),
        (workspace.prompts_dir, OPEN_MODE),
        (workspace.acceptance_dir, OPEN_MODE),
        (workspace.work_dir, OPEN_MODE),
    )


def example_configuration(example: Path, workspace: Workspace) -> str:
    for name in (CONVENTIONS_FILENAME,):
        source = example / name
        if source.is_file():
            shutil.copy2(source, workspace.prompts_dir / name)
    return (example / CONFIG_FILENAME).read_text()


def initialise_workspace(
    root: Path, operator_home: Path, force: bool, example: Path | None
) -> list[Path]:
    workspace = Workspace(root=root)
    written: list[Path] = [
        make_directory(path, mode) for path, mode in managed_directories(workspace)
    ]
    starter = (
        example_configuration(example, workspace)
        if example is not None
        else packaged_text(ResourceKind.CONFIG, STARTER_CONFIG)
    )
    config = write_once(workspace.config_path, starter)
    if config is not None:
        config.chmod(SECRET_MODE)
        written.append(config)
    if force or not workspace.reference_path.exists():
        workspace.reference_path.write_text(reference_document())
        written.append(workspace.reference_path)
    memory = workspace.agent_home / AGENT_MEMORY_PATH[0] / AGENT_MEMORY_PATH[1]
    make_directory(memory.parent, WORKSPACE_MODE)
    if force or not memory.exists():
        memory.write_text(packaged_text(ResourceKind.AGENT, AGENT_MEMORY))
        written.append(memory)
    for path, text in (
        (workspace.prompts_dir / "README.md", PROMPTS_README),
        (workspace.acceptance_dir / "README.md", ACCEPTANCE_README),
    ):
        if force or not path.exists():
            path.write_text(text)
            written.append(path)
    written.extend(render_fences(workspace, operator_home, NO_FORBIDDEN_PATHS))
    return written
