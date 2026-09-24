from difflib import get_close_matches
from pathlib import Path
from typing import Any, Final, get_args

from pydantic import BaseModel, ValidationError
from pydantic_core import ErrorDetails

UNKNOWN_KEY_TYPE: Final[str] = "extra_forbidden"
SUGGESTION_COUNT: Final[int] = 1
SUGGESTION_CUTOFF: Final[float] = 0.6
UNKNOWN_KEY_MESSAGE: Final[str] = "unknown key"


class ConfigError(Exception):
    pass


def model_of(annotation: Any) -> type[BaseModel] | None:  # noqa: ANN401
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for argument in get_args(annotation):
        found = model_of(argument)
        if found is not None:
            return found
    return None


def model_at(root: type[BaseModel], location: tuple[int | str, ...]) -> type[BaseModel] | None:
    model: type[BaseModel] | None = root
    for step in location:
        if model is None:
            return None
        field = model.model_fields.get(str(step))
        if field is None:
            continue
        model = model_of(field.annotation)
    return model


def suggestion(unknown: str, candidates: list[str]) -> str | None:
    close = get_close_matches(unknown, candidates, n=SUGGESTION_COUNT, cutoff=SUGGESTION_CUTOFF)
    return close[0] if close else None


def dotted(location: tuple[int | str, ...]) -> str:
    return ".".join(str(step) for step in location)


def render_error(root: type[BaseModel], origin: Path, error: ErrorDetails) -> str:
    location = tuple(error["loc"])
    if error["type"] != UNKNOWN_KEY_TYPE:
        return f"{origin}: {dotted(location)}: {error['msg'].removeprefix('Value error, ')}"
    owner = model_at(root, location[:-1])
    candidates = sorted(owner.model_fields) if owner is not None else []
    hint = suggestion(str(location[-1]), candidates)
    named = f"{dotted(location)}: {UNKNOWN_KEY_MESSAGE}"
    return f"{origin}: {named}" + (f" (did you mean {hint}?)" if hint is not None else "")


def render_validation_error(root: type[BaseModel], origin: Path, error: ValidationError) -> str:
    return "\n".join(render_error(root, origin, entry) for entry in error.errors())
