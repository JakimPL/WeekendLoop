from collections.abc import Mapping
from pathlib import Path
from typing import Final

from weekend_loop.models import Workspace

HOME_VARIABLE: Final[str] = "WEEKEND_LOOP_HOME"
DEFAULT_HOME_NAME: Final[str] = ".weekend-loop"
EMPTY_HOME_VARIABLE: Final[str] = (
    f"{HOME_VARIABLE} is set but empty; name a workspace or leave it unset"
)
MISSING_WORKSPACE_TEMPLATE: Final[str] = (
    "{root} is not a Weekend Loop workspace; these are missing:\n{parts}\n"
    "run: weekend-loop init --home {root}"
)


class WorkspaceError(Exception):
    pass


def home_directory(flag: Path | None, environment: Mapping[str, str], user_home: Path) -> Path:
    if flag is not None:
        return flag.expanduser().resolve()
    named = environment.get(HOME_VARIABLE)
    if named is not None:
        if not named.strip():
            raise WorkspaceError(EMPTY_HOME_VARIABLE)
        return Path(named).expanduser().resolve()
    return user_home / DEFAULT_HOME_NAME


def required_parts(workspace: Workspace) -> tuple[Path, ...]:
    return (
        workspace.config_path,
        workspace.secrets_dir,
        workspace.prompts_dir,
        workspace.acceptance_dir,
        workspace.agent_home,
        workspace.state_dir,
    )


def missing_parts(root: Path) -> list[Path]:
    return [path for path in required_parts(Workspace(root=root)) if not path.exists()]


def open_workspace(root: Path) -> Workspace:
    missing = missing_parts(root)
    if missing:
        listed = "\n".join(f"  {path}" for path in missing)
        raise WorkspaceError(MISSING_WORKSPACE_TEMPLATE.format(root=root, parts=listed))
    return Workspace(root=root)
