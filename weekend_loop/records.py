from pathlib import Path

from pydantic import BaseModel


def write_record(record: BaseModel, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(record.model_dump_json(indent=2) + "\n")
    temporary_path.replace(path)


def read_record[RecordType: BaseModel](record_type: type[RecordType], path: Path) -> RecordType:
    return record_type.model_validate_json(path.read_text())


def append_record(record: BaseModel, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as ledger:
        ledger.write(record.model_dump_json() + "\n")
