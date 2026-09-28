from pathlib import Path

import pytest

from weekend_loop.resources import PromptName, prompt_text

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONVENTIONS = REPOSITORY_ROOT / "examples"

REQUIRED_PLACEHOLDERS = {
    "assessor_task.md": {
        "{issue_number}",
        "{repo_slug}",
        "{spec_signals}",
        "{worker_limits}",
        "{briefing}",
        "{issue_title}",
        "{issue_body}",
    },
    "task_template.md": {
        "{issue_number}",
        "{issue_title}",
        "{repo_slug}",
        "{branch}",
        "{base_branch}",
        "{max_diff_lines}",
        "{delivery_fallback}",
        "{plan}",
        "{answers}",
        "{wave_paths}",
        "{shared_paths}",
        "{issue_body}",
    },
}


@pytest.mark.parametrize(("name", "placeholders"), sorted(REQUIRED_PLACEHOLDERS.items()))
def test_templates_carry_their_placeholders(name: str, placeholders: set[str]) -> None:
    text = prompt_text(PromptName(name), None)
    missing = {placeholder for placeholder in placeholders if placeholder not in text}
    assert not missing, missing


@pytest.mark.parametrize("name", ["assessor_task.md", "task_template.md"])
def test_issue_text_is_wrapped_as_untrusted(name: str) -> None:
    text = prompt_text(PromptName(name), None)
    assert "<untrusted_issue" in text and "</untrusted_issue>" in text


def test_every_example_conventions_prompt_names_its_gate_and_forbidden_paths() -> None:
    paths = sorted(CONVENTIONS.glob("conventions*.md"))
    assert paths
    for path in paths:
        text = path.read_text()
        assert "pytest" in text, path.name
        assert "Forbidden" in text, path.name


def test_a_prompt_in_the_workspace_wins_over_the_one_the_package_carries(tmp_path: Path) -> None:
    override = tmp_path / PromptName.WORKER_SYSTEM.value
    override.write_text("Follow the house rules of this repository and nothing else.\n")
    assert prompt_text(PromptName.WORKER_SYSTEM, tmp_path) == override.read_text()
    assert prompt_text(PromptName.WORKER_SYSTEM, None) != override.read_text()
