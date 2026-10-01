from __future__ import annotations

from enum import StrEnum

from weekend_loop.models import Record
from weekend_loop.setup import messages


class Mark(StrEnum):
    DONE = "ok"
    TODO = "todo"
    FAILED = "FAIL"


class StepOutcome(Record):
    mark: Mark
    name: str
    detail: str


def done(name: str, detail: str) -> StepOutcome:
    return StepOutcome(mark=Mark.DONE, name=name, detail=detail)


def todo(name: str, detail: str) -> StepOutcome:
    return StepOutcome(mark=Mark.TODO, name=name, detail=detail)


def failed(name: str, detail: str) -> StepOutcome:
    return StepOutcome(mark=Mark.FAILED, name=name, detail=detail)


def render_outcome(outcome: StepOutcome) -> str:
    return f"  {outcome.mark.value:<4}  {outcome.name}: {outcome.detail}"


def open_items(outcomes: list[StepOutcome]) -> int:
    return sum(1 for outcome in outcomes if outcome.mark is not Mark.DONE)


def closing_line(outcomes: list[StepOutcome], next_run: str | None, command: str) -> str:
    left = open_items(outcomes)
    if left == 1:
        return messages.LEFT_ONE.format(command=command)
    if left > 1:
        return messages.LEFT_MANY.format(count=left, command=command)
    return messages.READY if next_run is None else messages.READY_NEXT.format(next=next_run)


def render_root_block(commands: list[str]) -> list[str]:
    if not commands:
        return []
    return ["", messages.ROOT_HEADER, *(f"  {command}" for command in commands)]
