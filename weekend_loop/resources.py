from enum import StrEnum
from importlib.resources import files
from pathlib import Path
from typing import Final

REPOSITORY_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
RESOURCE_PACKAGE: Final[str] = "weekend_loop"
RESOURCE_ROOT: Final[str] = "resources"


class ResourceKind(StrEnum):
    PROMPT = "prompts"
    SCHEMA = "schemas"
    FENCE = "fences"
    CONFIG = "config"
    AGENT = "agent"


class PromptName(StrEnum):
    ASSESSOR_SYSTEM = "assessor_system.md"
    ASSESSOR_TASK = "assessor_task.md"
    WORKER_SYSTEM = "worker_system.md"
    TASK_TEMPLATE = "task_template.md"
    CONVENTIONS_DEFAULT = "conventions_default.md"


class SchemaName(StrEnum):
    ASSESSMENT = "assessment.json"
    DELIVERY = "delivery.json"


class FenceName(StrEnum):
    WORKER = "worker.template.json"
    ASSESSOR = "assessor.template.json"


def packaged_text(kind: ResourceKind, name: str) -> str:
    resource = files(RESOURCE_PACKAGE).joinpath(RESOURCE_ROOT, kind.value, name)
    if not resource.is_file():
        raise FileNotFoundError(f"packaged resource {kind.value}/{name} is missing")
    return resource.read_text()


def prompt_text(name: PromptName, overrides: Path | None) -> str:
    if overrides is not None:
        written = overrides / name.value
        if written.is_file():
            return written.read_text()
    return packaged_text(ResourceKind.PROMPT, name.value)


def schema_text(name: SchemaName) -> str:
    return packaged_text(ResourceKind.SCHEMA, name.value)


def fence_template_text(name: FenceName) -> str:
    return packaged_text(ResourceKind.FENCE, name.value)


def read_resource(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"resource {path} is missing")
    return path.read_text()
