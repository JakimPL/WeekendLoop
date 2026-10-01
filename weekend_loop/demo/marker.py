from datetime import datetime
from pathlib import Path

from weekend_loop.models import DemoMarker, Workspace
from weekend_loop.records import write_record


def is_demo(workspace: Workspace) -> bool:
    return workspace.demo_marker_path.is_file()


def write_marker(workspace: Workspace, examples: Path, now: datetime) -> None:
    write_record(DemoMarker(created_at=now, examples=examples), workspace.demo_marker_path)
