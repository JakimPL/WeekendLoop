from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from weekend_loop.models import Record
from weekend_loop.runs import RunDirectory
from weekend_loop.status import (
    STATUS_EVENT_COUNT,
    Liveness,
    ProcessCheck,
    RunStatus,
    clock,
    current_phrase,
    event_line,
    indented,
    load_status,
    parse_events,
    process_alive,
    render_status,
)
from weekend_loop.supervision import current_boot_id
from weekend_loop.transcript import (
    TranscriptCursor,
    appended_lines,
    attach_transcript,
    follow_transcript,
)

WATCH_POLL_SECONDS: Final[float] = 2.0
WATCH_BACKLOG_LINES: Final[int] = 20
HEADLINE_MARK: Final[str] = "=="
EVENT_MARK: Final[str] = "--"
ENDING_MESSAGES: Final[dict[Liveness, str]] = {
    Liveness.FINISHED: "the run has finished",
    Liveness.DEAD: "the run's process is gone; its pulse stopped",
}


class WatchPosition(Record):
    moment: str | None
    events_offset: int | None
    cursor: TranscriptCursor | None


class WatchTick(Record):
    position: WatchPosition
    output: list[str]
    finished: bool


def starting_position() -> WatchPosition:
    return WatchPosition(moment=None, events_offset=None, cursor=None)


def moment_key(status: RunStatus) -> str:
    activity = status.activity
    parts = [status.phase.value, status.liveness.value]
    if activity is not None:
        parts.extend([activity.kind.value, str(activity.issue_number), str(activity.started_at)])
    return "|".join(parts)


def display_path(path: Path, root: Path) -> str:
    return str(path.relative_to(root)) if path.is_relative_to(root) else str(path)


def headline(status: RunStatus) -> str:
    parts = [f"{clock(status.observed_at)} UTC", status.phase.value]
    if status.liveness is not Liveness.FINISHED:
        parts.append(status.liveness.value)
    phrase = current_phrase(status)
    if phrase is not None:
        parts.append(phrase)
    return f"{HEADLINE_MARK} {' · '.join(parts)}"


def header_lines(status: RunStatus, previous: str | None) -> list[str]:
    if previous is None:
        return render_status(status).splitlines()
    if previous != moment_key(status):
        return [headline(status)]
    return []


def flushed(cursor: TranscriptCursor | None) -> tuple[TranscriptCursor | None, list[str]]:
    if cursor is None:
        return None, []
    advanced, lines = follow_transcript(cursor)
    return advanced, indented(lines)


def new_events(run_directory: RunDirectory, offset: int | None) -> tuple[int, list[str]]:
    lines, advanced = appended_lines(run_directory.events_path, offset or 0)
    if offset is None:
        return advanced, []
    return advanced, [f"{EVENT_MARK} {event_line(event)}" for event in parse_events(lines)]


def switched(
    cursor: TranscriptCursor | None, target: Path | None, root: Path
) -> tuple[TranscriptCursor | None, list[str]]:
    if target is None or (cursor is not None and cursor.path == target):
        return cursor, []
    attached, backlog = attach_transcript(target, WATCH_BACKLOG_LINES)
    return attached, [f"{EVENT_MARK} transcript {display_path(target, root)}", *indented(backlog)]


def ending_lines(status: RunStatus) -> list[str]:
    message = ENDING_MESSAGES.get(status.liveness)
    return [] if message is None else [f"{HEADLINE_MARK} {message}"]


def watch_tick(
    run_directory: RunDirectory,
    position: WatchPosition,
    now: datetime,
    boot_id: str,
    is_alive: ProcessCheck,
) -> WatchTick:
    status = load_status(run_directory, 0, STATUS_EVENT_COUNT, now, boot_id, is_alive)
    cursor, transcript_lines = flushed(position.cursor)
    events_offset, event_lines = new_events(run_directory, position.events_offset)
    header = header_lines(status, position.moment)
    cursor, backlog = switched(cursor, status.transcript, run_directory.root)
    finished = status.liveness in ENDING_MESSAGES
    return WatchTick(
        position=WatchPosition(
            moment=moment_key(status), events_offset=events_offset, cursor=cursor
        ),
        output=[
            *transcript_lines,
            *event_lines,
            *header,
            *backlog,
            *ending_lines(status),
        ],
        finished=finished,
    )


def follow_run(
    run_directory: RunDirectory, poll_seconds: float, emit: Callable[[str], None]
) -> None:
    position = starting_position()
    while True:
        tick = watch_tick(
            run_directory, position, datetime.now(UTC), current_boot_id(), process_alive
        )
        for line in tick.output:
            emit(line)
        if tick.finished:
            return
        position = tick.position
        time.sleep(poll_seconds)
