import json
import sys
from pathlib import Path
from typing import Any, Final

from weekend_loop.models import Assessment, Delivery, StructuredOutput

STRUCTURED_OUTPUT_MODELS: Final[dict[str, type[StructuredOutput]]] = {
    "assessment": Assessment,
    "delivery": Delivery,
}


def structured_output_schema(model: type[StructuredOutput]) -> dict[str, Any]:
    return model.model_json_schema()


def render_schema(model: type[StructuredOutput]) -> str:
    return json.dumps(structured_output_schema(model), indent=2, sort_keys=True) + "\n"


def schema_path(directory: Path, name: str) -> Path:
    return directory / f"{name}.json"


def write_schemas(directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, model in STRUCTURED_OUTPUT_MODELS.items():
        path = schema_path(directory, name)
        path.write_text(render_schema(model))
        written.append(path)
    return written


def main(arguments: list[str]) -> int:
    if len(arguments) != 1:
        print("usage: python -m weekend_loop.schemas <output-directory>", file=sys.stderr)
        return 2
    for path in write_schemas(Path(arguments[0])):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
