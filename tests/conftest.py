from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

from weekend_loop import admission

STEADY_MEMINFO: Final[str] = "MemTotal:       67108864 kB\nMemAvailable:   50331648 kB\n"
CALM_PRESSURE: Final[str] = "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"


@pytest.fixture(autouse=True)
def steady_memory(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    directory = tmp_path_factory.mktemp("proc")
    meminfo = directory / "meminfo"
    meminfo.write_text(STEADY_MEMINFO)
    pressure = directory / "pressure"
    pressure.write_text(CALM_PRESSURE)
    monkeypatch.setattr(admission, "MEMINFO_PATH", meminfo)
    monkeypatch.setattr(admission, "MEMORY_PRESSURE_PATH", pressure)
    return directory
