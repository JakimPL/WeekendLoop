import re
from pathlib import Path
from typing import Final

README: Final[Path] = Path("README.md")
MINIMUM_WALKTHROUGH_WORDS: Final[int] = 40
ADDRESS_PATTERN: Final[re.Pattern[str]] = re.compile(r"(localhost|127\.0\.0\.1):8000")


def section(title: str) -> str:
    pattern = rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, README.read_text(), re.MULTILINE | re.DOTALL)
    assert match is not None, f"README has no '{title}' section"
    return match.group(1)


def test_no_placeholder_is_left() -> None:
    assert "TODO" not in README.read_text()


def test_how_to_run_names_the_commands_and_the_address() -> None:
    instructions = section("How to run it")
    assert "uv sync" in instructions
    assert "uv run pocketchat" in instructions
    assert ADDRESS_PATTERN.search(instructions) is not None


def test_how_to_use_walks_through_a_conversation() -> None:
    assert len(section("How to use it").split()) >= MINIMUM_WALKTHROUGH_WORDS
