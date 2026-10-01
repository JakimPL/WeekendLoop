from __future__ import annotations

import json
from pathlib import Path

import pytest

from weekend_loop.fences import (
    NO_FORBIDDEN_PATHS,
    OPERATOR_HOME_TOKEN,
    RUNTIME_DIRECTORY_TOKEN,
    WORKSPACE_HOME_TOKEN,
    fence_tokens,
    render_fence,
    render_fences,
    user_runtime_directory,
)
from weekend_loop.models import DEFAULT_FORBIDDEN_PATHS, Workspace
from weekend_loop.resources import FenceName, ResourceKind, fence_template_text, packaged_text

OPERATOR_HOME = Path("/operator-home")
CREDENTIAL_DIRECTORIES = (".ssh", ".aws", ".config/gh", ".claude")
WORKSPACE_DIRECTORIES = ("secrets", "state", "agent-home")


def tokens_for(tmp_path: Path) -> dict[str, str]:
    return fence_tokens(Workspace(root=tmp_path), OPERATOR_HOME)


def test_rendering_names_both_homes_and_leaves_no_placeholder(tmp_path: Path) -> None:
    template = json.dumps(
        {
            "permissions": {
                "deny": [
                    f"Read(/{OPERATOR_HOME_TOKEN}/.ssh/**)",
                    f"Read({WORKSPACE_HOME_TOKEN}/state/**)",
                    f"Read({RUNTIME_DIRECTORY_TOKEN}/**)",
                ]
            }
        }
    )
    fence = render_fence(
        template, tmp_path / "settings.json", tokens_for(tmp_path), NO_FORBIDDEN_PATHS
    )
    text = fence.read_text()
    assert OPERATOR_HOME_TOKEN not in text and WORKSPACE_HOME_TOKEN not in text
    assert RUNTIME_DIRECTORY_TOKEN not in text
    assert "Read(//operator-home/.ssh/**)" in text
    assert f"Read({tmp_path}/state/**)" in text


def test_a_template_that_names_no_home_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        render_fence(
            json.dumps({"permissions": {"deny": ["Read(**/.env)"]}}),
            tmp_path / "settings.json",
            tokens_for(tmp_path),
            NO_FORBIDDEN_PATHS,
        )


def test_a_fence_the_package_does_not_carry_is_refused() -> None:
    with pytest.raises(FileNotFoundError):
        packaged_text(ResourceKind.FENCE, "absent.template.json")


def test_both_shipped_fences_deny_the_operator_credentials(tmp_path: Path) -> None:
    for name, rendered in (
        (FenceName.WORKER, tmp_path / "settings.json"),
        (FenceName.ASSESSOR, tmp_path / "assessor.settings.json"),
    ):
        render_fence(fence_template_text(name), rendered, tokens_for(tmp_path), NO_FORBIDDEN_PATHS)
        denied = json.loads(rendered.read_text())["permissions"]["deny"]
        for directory in CREDENTIAL_DIRECTORIES:
            assert f"Read(/{OPERATOR_HOME}/{directory}/**)" in denied


def test_both_shipped_fences_deny_the_workspace_the_orchestrator_keeps(tmp_path: Path) -> None:
    for name, rendered in (
        (FenceName.WORKER, tmp_path / "settings.json"),
        (FenceName.ASSESSOR, tmp_path / "assessor.settings.json"),
    ):
        render_fence(fence_template_text(name), rendered, tokens_for(tmp_path), NO_FORBIDDEN_PATHS)
        denied = json.loads(rendered.read_text())["permissions"]["deny"]
        for directory in WORKSPACE_DIRECTORIES:
            assert f"Read({tmp_path}/{directory}/**)" in denied


def test_the_worker_sandbox_denies_the_operator_credential_files(tmp_path: Path) -> None:
    fence = render_fence(
        fence_template_text(FenceName.WORKER),
        tmp_path / "settings.json",
        tokens_for(tmp_path),
        NO_FORBIDDEN_PATHS,
    )
    files = json.loads(fence.read_text())["sandbox"]["credentials"]["files"]
    denied = {entry["path"] for entry in files if entry["mode"] == "deny"}
    assert {str(OPERATOR_HOME / directory) for directory in CREDENTIAL_DIRECTORIES} <= denied
    assert {str(tmp_path / directory) for directory in WORKSPACE_DIRECTORIES} <= denied


def test_both_shipped_fences_hide_the_user_runtime_directory(tmp_path: Path) -> None:
    runtime = user_runtime_directory()
    for name, rendered in (
        (FenceName.WORKER, tmp_path / "settings.json"),
        (FenceName.ASSESSOR, tmp_path / "assessor.settings.json"),
    ):
        render_fence(fence_template_text(name), rendered, tokens_for(tmp_path), NO_FORBIDDEN_PATHS)
        assert f"Read({runtime}/**)" in json.loads(rendered.read_text())["permissions"]["deny"]
    sandbox = json.loads((tmp_path / "settings.json").read_text())["sandbox"]
    denied = {entry["path"] for entry in sandbox["credentials"]["files"] if entry["mode"] == "deny"}
    assert str(runtime) in denied
    assert sandbox["network"]["allowAllUnixSockets"] is False


def test_the_runtime_directory_follows_the_session_that_declares_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/4242")
    assert user_runtime_directory() == Path("/run/user/4242")
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    assert user_runtime_directory().parent == Path("/run/user")


def test_rendering_both_fences_writes_both(tmp_path: Path) -> None:
    workspace = Workspace(root=tmp_path)
    assert render_fences(workspace, OPERATOR_HOME, NO_FORBIDDEN_PATHS) == [
        workspace.worker_fence,
        workspace.assessor_fence,
    ]
    assert workspace.worker_fence.is_file() and workspace.assessor_fence.is_file()


def test_the_worker_fence_denies_the_paths_the_repository_forbids(tmp_path: Path) -> None:
    workspace = Workspace(root=tmp_path)
    render_fences(workspace, OPERATOR_HOME, ["docs/generated/**", "Makefile"])
    denied = json.loads(workspace.worker_fence.read_text())["permissions"]["deny"]
    assert "Edit(docs/generated/**)" in denied
    assert "Write(Makefile)" in denied


def test_the_shipped_worker_fence_leaves_every_path_to_forbidden_paths(tmp_path: Path) -> None:
    fence = render_fence(
        fence_template_text(FenceName.WORKER),
        tmp_path / "settings.json",
        tokens_for(tmp_path),
        NO_FORBIDDEN_PATHS,
    )
    denied = json.loads(fence.read_text())["permissions"]["deny"]
    assert not [rule for rule in denied if rule.startswith(("Edit(", "Write("))]


def test_the_default_forbidden_paths_keep_the_project_machinery(tmp_path: Path) -> None:
    workspace = Workspace(root=tmp_path)
    render_fences(workspace, OPERATOR_HOME, list(DEFAULT_FORBIDDEN_PATHS))
    denied = json.loads(workspace.worker_fence.read_text())["permissions"]["deny"]
    for path in (
        ".github/**",
        "**/pyproject.toml",
        "**/uv.lock",
        "**/Makefile",
        "**/.pre-commit-config.yaml",
        "config/**",
    ):
        assert f"Edit({path})" in denied and f"Write({path})" in denied
