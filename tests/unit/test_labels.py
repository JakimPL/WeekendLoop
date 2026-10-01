import subprocess
from typing import Final

from weekend_loop.labels import (
    LabelRefusal,
    board_labels,
    create_labels,
    label_arguments,
)
from weekend_loop.models import LabelPolicy

SLUG = "example-org/example-board"
LABELS: Final[LabelPolicy] = LabelPolicy.model_validate({})
REFUSAL = "HTTP 403: Resource not accessible by personal access token"


class RecordingCommands:
    def __init__(self, refused_label: str | None) -> None:
        self.refused_label = refused_label
        self.calls: list[list[str]] = []

    def run(self, arguments: list[str], stdin: str | None = None) -> str:
        self.calls.append(arguments)
        if self.refused_label in arguments:
            raise subprocess.CalledProcessError(1, ["gh", *arguments], "", f"gh: {REFUSAL}\n")
        return ""


def label_calls() -> list[list[str]]:
    return [label_arguments(SLUG, label) for label in board_labels(LABELS)]


def test_every_weekend_label_is_created_with_force() -> None:
    commands = RecordingCommands(None)
    assert create_labels(commands, label_calls()) is None
    assert len(commands.calls) == len(board_labels(LABELS))
    assert all(call[:2] == ["label", "create"] and "--force" in call for call in commands.calls)


def test_the_first_refused_label_stops_the_rest_and_says_why() -> None:
    refused = board_labels(LABELS)[1].name
    commands = RecordingCommands(refused)
    refusal = create_labels(commands, label_calls())
    assert refusal == LabelRefusal(label=refused, reason=f"gh: {REFUSAL}")
    assert len(commands.calls) == 2
