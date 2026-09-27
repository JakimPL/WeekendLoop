from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from weekend_loop.fences import fence_tokens, render_fence
from weekend_loop.models import DEFAULT_FORBIDDEN_PATHS, Workspace
from weekend_loop.resources import FenceName, fence_template_text

OPERATOR_HOME = Path("/operator-home")
MUST_DENY = {
    "Bash(git *)",
    "Bash(gh *)",
    "Bash(pre-commit *)",
    "Bash(curl *)",
    "Edit(.github/**)",
    "Write(.github/**)",
    "Edit(**/uv.lock)",
    "Read(**/.env)",
    "WebFetch",
}
PROTECTED_VARIABLES = {"CLAUDE_CODE_OAUTH_TOKEN", "GH_TOKEN", "AWS_SECRET_ACCESS_KEY"}


def worker_settings(tmp_path: Path) -> dict[str, Any]:
    fence = render_fence(
        fence_template_text(FenceName.WORKER),
        tmp_path / "settings.json",
        fence_tokens(Workspace(root=tmp_path), OPERATOR_HOME),
        list(DEFAULT_FORBIDDEN_PATHS),
    )
    settings: dict[str, Any] = json.loads(fence.read_text())
    return settings


def test_the_worker_fence_denies_the_commands_and_paths_the_orchestrator_owns(
    tmp_path: Path,
) -> None:
    permissions = worker_settings(tmp_path)["permissions"]
    assert MUST_DENY <= set(permissions["deny"])
    assert "allow" not in permissions


def test_the_worker_fence_confines_the_sandbox_to_the_one_domain_it_needs(tmp_path: Path) -> None:
    sandbox = worker_settings(tmp_path)["sandbox"]
    assert sandbox["enabled"] is True
    assert sandbox["failIfUnavailable"] is True
    assert sandbox["allowUnsandboxedCommands"] is False
    assert sandbox["network"]["allowedDomains"] == ["api.anthropic.com"]
    assert sandbox["network"]["strictAllowlist"] is True


def test_the_worker_fence_keeps_every_credential_variable_from_the_sandbox(tmp_path: Path) -> None:
    sandbox = worker_settings(tmp_path)["sandbox"]
    protected = {entry["name"] for entry in sandbox["credentials"]["envVars"]}
    assert PROTECTED_VARIABLES <= protected


def test_the_worker_writes_under_the_operator_identity_and_stops_at_the_usage_limit(
    tmp_path: Path,
) -> None:
    settings = worker_settings(tmp_path)
    assert settings["attribution"] == {"commit": "", "pr": ""}
    assert settings["autoContinueAtUsageLimit"] is False
    assert settings["env"]["UV_NO_SYNC"] == "1"
