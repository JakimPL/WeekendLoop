from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from weekend_loop.models import DEFAULT_IDLE_MINUTES, Backend, Policy
from weekend_loop.policy import repo_target

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIG = REPOSITORY_ROOT / "examples" / "config.yaml"


def document(tmp_path: Path) -> dict[str, Any]:
    raw: dict[str, Any] = yaml.safe_load(EXAMPLE_CONFIG.read_text())
    raw["workspace"] = {"root": str(tmp_path)}
    return raw


def test_the_example_config_loads_with_its_paths_inside_the_workspace(
    tmp_path: Path, workspace_policy: Policy
) -> None:
    assert workspace_policy.state_dir == workspace_policy.workspace.root / "state"
    assert workspace_policy.agent_home == workspace_policy.workspace.root / "agent-home"
    demo = workspace_policy.repos["demo"]
    assert demo.token_path().is_relative_to(workspace_policy.workspace.root)
    assert demo.conventions_prompt is not None and demo.conventions_prompt.is_file()


def test_the_github_pilot_is_the_local_pilot_on_github(tmp_path: Path) -> None:
    policy = Policy.model_validate(document(tmp_path))
    local, github = policy.repos["demo"], policy.repos["demo-github"]
    assert (local.backend, github.backend) == (Backend.LOCAL, Backend.GITHUB)
    assert github.model_copy(update={"backend": Backend.LOCAL}) == local


def test_the_example_config_schedules_a_repository_it_defines(tmp_path: Path) -> None:
    policy = Policy.model_validate(document(tmp_path))
    assert policy.schedule.repo_key in policy.repos


def test_per_task_cap_above_the_envelope_is_rejected(tmp_path: Path) -> None:
    raw = document(tmp_path)
    raw["budget"]["per_task_usd"] = raw["budget"]["envelope_usd"] + 1
    with pytest.raises(ValidationError, match="per_task_usd"):
        Policy.model_validate(raw)


def test_repository_slug_requires_owner_and_name(tmp_path: Path) -> None:
    raw = document(tmp_path)
    raw["repos"]["demo"]["slug"] = "no-owner-here"
    with pytest.raises(ValidationError, match="slug"):
        Policy.model_validate(raw)


def test_unknown_repo_key_names_the_known_ones(tmp_path: Path) -> None:
    policy = Policy.model_validate(document(tmp_path))
    with pytest.raises(KeyError, match="demo"):
        repo_target(policy, "nope")


def test_the_example_config_names_its_usage_ceilings(tmp_path: Path) -> None:
    policy = Policy.model_validate(document(tmp_path))
    assert 0.0 < policy.usage.seven_day_ceiling <= 1.0
    assert policy.usage.seven_day_reserve < policy.usage.seven_day_ceiling
    assert policy.usage.five_hour_ceiling <= 1.0


def test_a_reserve_that_fills_the_ceiling_is_rejected(tmp_path: Path) -> None:
    raw = document(tmp_path)
    raw["usage"]["seven_day_reserve"] = raw["usage"]["seven_day_ceiling"]
    with pytest.raises(ValidationError, match="seven_day_reserve"):
        Policy.model_validate(raw)


def test_a_ceiling_above_a_whole_window_is_rejected(tmp_path: Path) -> None:
    raw = document(tmp_path)
    raw["usage"]["five_hour_ceiling"] = 1.5
    with pytest.raises(ValidationError, match="five_hour_ceiling"):
        Policy.model_validate(raw)


def test_a_config_written_before_the_idle_limit_loads_with_the_default(tmp_path: Path) -> None:
    raw = document(tmp_path)
    del raw["worker"]["idle_minutes"]
    assert Policy.model_validate(raw).worker.idle_minutes == DEFAULT_IDLE_MINUTES
