from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from weekend_loop.models import Activity, ActivityKind, Pulse, RunState
from weekend_loop.status import Liveness, RunExtras, RunStatus, build_status

STAND_IN_SECONDS: Final[int] = 60
STAND_IN_SCRIPT: Final[str] = (
    f"import time; print('ready', flush=True); time.sleep({STAND_IN_SECONDS})"
)
ORCHESTRATOR_ARGUMENT: Final[str] = "weekend_loop"


# Popen returns once exec closes the error pipe, before the kernel fills /proc/<pid>/cmdline;
# waiting for the child's first line guarantees its command line is readable.
@contextmanager
def sleeping_process(extra_arguments: list[str]) -> Iterator[int]:
    command = [sys.executable, "-c", STAND_IN_SCRIPT, *extra_arguments]
    with subprocess.Popen(command, stdout=subprocess.PIPE) as process:
        try:
            assert process.stdout is not None
            process.stdout.readline()
            yield process.pid
        finally:
            process.kill()


@contextmanager
def orchestrator_stand_in() -> Iterator[int]:
    with sleeping_process([ORCHESTRATOR_ARGUMENT]) as pid:
        yield pid


def build_activity(
    kind: ActivityKind,
    issue_number: int | None,
    transcript: Path | None,
    started_at: datetime,
    resumes_at: datetime | None,
) -> Activity:
    return Activity(
        kind=kind,
        issue_number=issue_number,
        started_at=started_at,
        transcript=transcript,
        resumes_at=resumes_at,
    )


def build_pulse(pid: int, boot_id: str, heartbeat_at: datetime, activity: Activity | None) -> Pulse:
    return Pulse(pid=pid, boot_id=boot_id, heartbeat_at=heartbeat_at, activity=activity)


def quiet_status(state: RunState, now: datetime) -> RunStatus:
    return build_status(state, RunExtras(), None, Liveness.UNKNOWN, [], None, [], now)


def assistant(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "assistant", "message": {"role": "assistant", "content": blocks}}


def said(text: str) -> dict[str, Any]:
    return assistant([{"type": "text", "text": text}])


def thought(text: str) -> dict[str, Any]:
    return assistant([{"type": "thinking", "thinking": text, "signature": "opaque"}])


def tool_call(name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    return assistant([{"type": "tool_use", "id": "toolu_1", "name": name, "input": tool_input}])


def tool_results(results: list[tuple[Any, bool]]) -> dict[str, Any]:
    blocks = [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": content, "is_error": error}
        for content, error in results
    ]
    return {"type": "user", "message": {"role": "user", "content": blocks}}


def write_transcript(path: Path, messages: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(message) + "\n" for message in messages))
    return path


def append_transcript(path: Path, messages: list[dict[str, Any]]) -> None:
    with path.open("a") as transcript:
        transcript.write("".join(json.dumps(message) + "\n" for message in messages))
