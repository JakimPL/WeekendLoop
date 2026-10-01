from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

from weekend_loop.commands import command_environment, run_command
from weekend_loop.confinement import (
    Confinement,
    OomPolicy,
    Step,
    confined,
    confined_environment,
    fan_out_environment,
    scopes_available,
    unit_prefix,
)
from weekend_loop.models import ResourcesPolicy

UNIT: Final[str] = "wl-run-7-gate-abcd1234"
GATE: Final[Confinement] = Confinement(
    unit_prefix="wl-run-7-gate",
    memory_gb=2.0,
    cpus=[2, 3],
    oom_policy=OomPolicy.KILL,
    runtime_seconds=900,
    scoped=True,
)
HOG: Final[str] = "python3 -c \"blob = bytearray(256 * 1024 * 1024); print('survived')\""
HOLDER: Final[str] = 'python3 -c "import time; blob = bytearray(64 * 1024 * 1024); time.sleep(3)"'


def test_a_scoped_command_runs_capped_pinned_and_without_the_session_bus() -> None:
    command = confined(["pytest", "-q"], GATE, UNIT)
    scope, rest = command[: command.index("--") + 1], command[command.index("--") + 1 :]
    assert scope[:4] == ["systemd-run", "--user", "--scope", "--quiet"]
    assert f"--unit={UNIT}" in scope
    assert f"MemoryMax={2 * 1024**3}" in scope
    assert "MemorySwapMax=0" in scope
    assert "OOMPolicy=kill" in scope
    assert "RuntimeMaxSec=900" in scope
    assert rest == [
        "env",
        "-u",
        "XDG_RUNTIME_DIR",
        "-u",
        "DBUS_SESSION_BUS_ADDRESS",
        "choom",
        "-n",
        "800",
        "--",
        "taskset",
        "-c",
        "2,3",
        "pytest",
        "-q",
    ]


def test_without_systemd_a_command_still_yields_first_to_the_kernel() -> None:
    plain = GATE.model_copy(update={"scoped": False, "cpus": []})
    assert confined(["pytest"], plain, UNIT) == ["choom", "-n", "800", "--", "pytest"]
    assert confined_environment({"PATH": "/usr/bin"}, plain) == {"PATH": "/usr/bin"}
    assert "XDG_RUNTIME_DIR" in confined_environment({"PATH": "/usr/bin"}, GATE)


def test_the_fan_out_follows_the_cpus_a_task_gets() -> None:
    assert fan_out_environment(ResourcesPolicy(cpus_per_task=6)) == {
        "PYTEST_XDIST_AUTO_NUM_WORKERS": "6"
    }
    assert fan_out_environment(ResourcesPolicy()) == {}


def test_a_unit_name_keeps_only_the_characters_systemd_takes() -> None:
    assert unit_prefix("20260927 slowiki/x", 13, Step.GATE) == "wl-20260927_slowiki_x-13-gate"


@pytest.mark.skipif(not scopes_available(), reason="no systemd user manager to start scopes")
class TestARealScope:
    def test_a_command_past_its_cap_is_stopped_and_named(self, tmp_path: Path) -> None:
        capped = GATE.model_copy(update={"memory_gb": 0.0625, "cpus": []})
        result = run_command(HOG, tmp_path, command_environment({}), 60, capped)
        assert result.exit_code != 0
        assert result.stopped_at_memory_cap

    def test_a_command_within_its_cap_reports_its_peak(self, tmp_path: Path) -> None:
        roomy = GATE.model_copy(update={"memory_gb": 0.5, "cpus": []})
        result = run_command(HOLDER, tmp_path, command_environment({}), 60, roomy)
        assert result.exit_code == 0
        assert not result.stopped_at_memory_cap
        assert result.peak_memory_gb is not None and result.peak_memory_gb >= 0.06
