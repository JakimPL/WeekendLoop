from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from weekend_loop.mailbox import pause_requested, stop_requested
from weekend_loop.models import Activity, ActivityKind, Pulse
from weekend_loop.runs import RunDirectory, write_pulse

BOOT_ID_PATH: Final[Path] = Path("/proc/sys/kernel/random/boot_id")
PULSE_SECONDS: Final[float] = 30.0
POLL_SECONDS: Final[float] = 2.0
STOP_DETAIL: Final[str] = "stop requested"


class Hold(StrEnum):
    NOT_PAUSED = "not_paused"
    RESUMED = "resumed"
    STOPPED = "stopped"
    DEADLINE = "deadline"


def current_boot_id() -> str:
    return BOOT_ID_PATH.read_text().strip()


class RunSupervisor:
    def __init__(self, run_directory: RunDirectory, pulse_seconds: float) -> None:
        self.run_directory = run_directory
        self.pulse_seconds = pulse_seconds
        self.pid = os.getpid()
        self.boot_id = current_boot_id()
        self.activity: Activity | None = None
        self.last_pulse: float | None = None
        self.pulse()

    def stop_requested(self) -> bool:
        return stop_requested(self.run_directory.inbox)

    def pause_requested(self) -> bool:
        return pause_requested(self.run_directory.inbox)

    def transcript_for(self, role: str, issue_number: int | None) -> Path:
        return self.run_directory.next_transcript(role, issue_number)

    def enter(
        self,
        kind: ActivityKind,
        issue_number: int | None,
        transcript: Path | None,
        resumes_at: datetime | None,
    ) -> None:
        self.activity = Activity(
            kind=kind,
            issue_number=issue_number,
            started_at=datetime.now(UTC),
            transcript=transcript,
            resumes_at=resumes_at,
        )
        self.pulse()

    def beat(self) -> None:
        if self.last_pulse is not None and time.monotonic() - self.last_pulse < self.pulse_seconds:
            return
        self.pulse()

    def pulse(self) -> None:
        self.last_pulse = time.monotonic()
        write_pulse(
            self.run_directory,
            Pulse(
                pid=self.pid,
                boot_id=self.boot_id,
                heartbeat_at=datetime.now(UTC),
                activity=self.activity,
            ),
        )

    def park(self, until: datetime) -> bool:
        self.enter(ActivityKind.PARKED, None, None, until)
        while True:
            remaining = (until - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                return True
            if self.stop_requested():
                return False
            time.sleep(min(POLL_SECONDS, remaining))
            self.beat()

    def hold_while_paused(self, deadline: datetime) -> Hold:
        if not self.pause_requested():
            return Hold.NOT_PAUSED
        self.enter(ActivityKind.PAUSED, None, None, None)
        while self.pause_requested():
            if self.stop_requested():
                return Hold.STOPPED
            if datetime.now(UTC) >= deadline:
                return Hold.DEADLINE
            time.sleep(POLL_SECONDS)
            self.beat()
        return Hold.RESUMED
