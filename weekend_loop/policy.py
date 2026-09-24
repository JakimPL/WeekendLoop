from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from weekend_loop.config_errors import ConfigError, render_validation_error
from weekend_loop.models import Policy, RepoTarget, Workspace
from weekend_loop.workspace import open_workspace


def read_document(path: Path) -> dict[str, Any]:
    document: dict[str, Any] = yaml.safe_load(path.read_text())
    return document


def load_policy(workspace: Workspace) -> Policy:
    document = read_document(workspace.config_path)
    try:
        policy = Policy.model_validate({**document, "workspace": workspace})
    except ValidationError as error:
        raise ConfigError(render_validation_error(Policy, workspace.config_path, error)) from error
    return resolve_paths(policy, workspace)


def policy_at(root: Path) -> Policy:
    return load_policy(open_workspace(root))


def resolved_repo(repo_key: str, repo: RepoTarget, workspace: Workspace) -> RepoTarget:
    token_file = (
        anchor(repo.token_file, workspace.root)
        if repo.token_file is not None
        else workspace.repository_token_path(repo_key)
    )
    conventions = (
        anchor(repo.conventions_prompt, workspace.root)
        if repo.conventions_prompt is not None
        else None
    )
    return repo.model_copy(update={"token_file": token_file, "conventions_prompt": conventions})


def resolve_paths(policy: Policy, workspace: Workspace) -> Policy:
    repos = {key: resolved_repo(key, repo, workspace) for key, repo in policy.repos.items()}
    return policy.model_copy(update={"repos": repos})


def anchor(path: Path, root: Path) -> Path:
    return path if path.is_absolute() else (root / path).resolve()


def repo_target(policy: Policy, repo_key: str) -> RepoTarget:
    if repo_key not in policy.repos:
        known = ", ".join(sorted(policy.repos))
        raise KeyError(f"unknown repo key {repo_key!r}; policy defines: {known}")
    return policy.repos[repo_key]
