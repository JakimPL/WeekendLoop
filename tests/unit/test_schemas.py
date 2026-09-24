from pathlib import Path

import pytest

from weekend_loop.resources import SchemaName, schema_text
from weekend_loop.schemas import (
    STRUCTURED_OUTPUT_MODELS,
    render_schema,
    write_schemas,
)


@pytest.mark.parametrize("name", sorted(STRUCTURED_OUTPUT_MODELS))
def test_the_packaged_schema_matches_the_model(name: str) -> None:
    assert schema_text(SchemaName(f"{name}.json")) == render_schema(STRUCTURED_OUTPUT_MODELS[name])


def test_write_schemas_creates_one_file_per_model(tmp_path: Path) -> None:
    written = write_schemas(tmp_path / "schemas")
    assert sorted(path.stem for path in written) == sorted(STRUCTURED_OUTPUT_MODELS)
