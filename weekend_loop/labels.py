from typing import Final

from weekend_loop.board import open_board
from weekend_loop.models import Backend, BoardLabel, LabelPolicy, Policy, RepoTarget

LABEL_COLOUR: Final[str] = "5319E7"
LABEL_DESCRIPTIONS: Final[tuple[tuple[str, str], ...]] = (
    ("auto", "Pre-consented: Weekend Loop may work on this without asking"),
    ("approved", "Proposal accepted: Weekend Loop may work on this"),
    ("never", "Weekend Loop never touches this issue"),
    ("review", "Weekend Loop delivered a branch; awaiting human review"),
    ("needs_input", "Weekend Loop stopped on a question; awaiting an answer"),
    ("unfinished", "Weekend Loop left unfinished work; do not merge as is"),
)


def board_labels(labels: LabelPolicy) -> list[BoardLabel]:
    return [
        BoardLabel(name=getattr(labels, field), description=description, colour=LABEL_COLOUR)
        for field, description in LABEL_DESCRIPTIONS
    ]


def sync_local_labels(policy: Policy, repo: RepoTarget) -> list[str]:
    board = open_board(policy.state_dir, repo.slug)
    index = board.read_index()
    wanted = board_labels(policy.labels)
    names = {label.name for label in wanted}
    kept = [label for label in index.labels if label.name not in names]
    board.write_index(index.model_copy(update={"labels": [*kept, *wanted]}))
    return [label.name for label in wanted]


def label_arguments(slug: str, label: BoardLabel) -> list[str]:
    return [
        "gh",
        "label",
        "create",
        label.name,
        "--repo",
        slug,
        "--description",
        label.description,
        "--color",
        label.colour,
        "--force",
    ]


def label_commands(policy: Policy, repo: RepoTarget) -> list[list[str]]:
    return [label_arguments(repo.slug, label) for label in board_labels(policy.labels)]


def backend_of(repo: RepoTarget) -> Backend:
    return repo.backend
