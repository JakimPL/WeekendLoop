import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from weekend_loop.models import Workspace
from weekend_loop.resources import FenceName, fence_template_text

OPERATOR_HOME_TOKEN: Final[str] = "__OPERATOR_HOME__"
WORKSPACE_HOME_TOKEN: Final[str] = "__WORKSPACE_HOME__"
RUNTIME_DIRECTORY_TOKEN: Final[str] = "__RUNTIME_DIR__"
RUNTIME_DIRECTORY_VARIABLE: Final[str] = "XDG_RUNTIME_DIR"
USER_RUNTIME_ROOT: Final[Path] = Path("/run/user")
PERMISSIONS_KEY: Final[str] = "permissions"
DENY_KEY: Final[str] = "deny"
WRITING_TOOLS: Final[tuple[str, ...]] = ("Edit", "Write")
NO_FORBIDDEN_PATHS: Final[list[str]] = []


def user_runtime_directory() -> Path:
    declared = os.environ.get(RUNTIME_DIRECTORY_VARIABLE)
    return Path(declared) if declared else USER_RUNTIME_ROOT / str(os.getuid())


def fence_tokens(workspace: Workspace, operator_home: Path) -> dict[str, str]:
    return {
        OPERATOR_HOME_TOKEN: str(operator_home),
        WORKSPACE_HOME_TOKEN: str(workspace.root),
        RUNTIME_DIRECTORY_TOKEN: str(user_runtime_directory()),
    }


def denied_for(paths: list[str]) -> list[str]:
    return [f"{tool}({path})" for path in paths for tool in WRITING_TOOLS]


def with_forbidden_paths(fence: dict[str, Any], paths: list[str]) -> dict[str, Any]:
    if PERMISSIONS_KEY not in fence or DENY_KEY not in fence[PERMISSIONS_KEY]:
        raise ValueError(f"fence carries no {PERMISSIONS_KEY}.{DENY_KEY} list")
    permissions: dict[str, Any] = dict(fence[PERMISSIONS_KEY])
    denied: list[str] = list(permissions[DENY_KEY])
    permissions[DENY_KEY] = denied + [rule for rule in denied_for(paths) if rule not in denied]
    return {**fence, PERMISSIONS_KEY: permissions}


def render_fence(
    template: str, fence: Path, tokens: Mapping[str, str], forbidden_paths: list[str]
) -> Path:
    missing = [token for token in tokens if token not in template]
    if missing:
        raise ValueError(f"fence template for {fence.name} names no {', '.join(missing)}")
    text = template
    for token, value in tokens.items():
        text = text.replace(token, value)
    rendered = with_forbidden_paths(json.loads(text), forbidden_paths)
    fence.parent.mkdir(parents=True, exist_ok=True)
    fence.write_text(json.dumps(rendered, indent=2) + "\n")
    return fence


def render_fences(
    workspace: Workspace, operator_home: Path, forbidden_paths: list[str]
) -> list[Path]:
    tokens = fence_tokens(workspace, operator_home)
    return [
        render_fence(fence_template_text(name), fence, tokens, forbidden_paths)
        for name, fence in (
            (FenceName.WORKER, workspace.worker_fence),
            (FenceName.ASSESSOR, workspace.assessor_fence),
        )
    ]
