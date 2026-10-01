from __future__ import annotations

import threading
from typing import Final

from weekend_loop.admission import (
    Admission,
    AdmissionLimits,
    MemoryReading,
    Reservation,
    ReservationKind,
    admits,
    caps_exceeding_the_pool,
    cpu_sets,
    parse_memory,
    tasks_at_once,
)

LIMITS: Final[AdmissionLimits] = AdmissionLimits(
    pool_gb=20.0, reserve_gb=4.0, task_memory_gb=8.0, gate_memory_gb=10.0, gates_at_once=1
)
ROOMY: Final[MemoryReading] = MemoryReading(total_gb=64.0, available_gb=48.0, pressure_percent=0.0)
WAIT_SECONDS: Final[float] = 5.0


def working(holder: int) -> Reservation:
    return Reservation(holder=holder, kind=ReservationKind.WORKING, memory_gb=8.0)


def gating(holder: int) -> Reservation:
    return Reservation(holder=holder, kind=ReservationKind.GATING, memory_gb=10.0)


def test_a_reading_names_the_memory_and_its_pressure() -> None:
    reading = parse_memory(
        "MemTotal:       67108864 kB\nMemFree:  1 kB\nMemAvailable:   33554432 kB\n",
        "some avg10=12.50 avg60=3.00 avg300=1.00 total=99\nfull avg10=2.00 avg60=0.0\n",
    )
    assert reading == MemoryReading(total_gb=64.0, available_gb=32.0, pressure_percent=12.5)


def test_a_request_alone_is_always_admitted() -> None:
    starved = MemoryReading(total_gb=64.0, available_gb=1.0, pressure_percent=90.0)
    assert admits(LIMITS, [], working(1), starved, False)
    assert admits(LIMITS, [working(1)], gating(1), starved, False)


def test_a_request_beyond_the_pool_waits() -> None:
    assert admits(LIMITS, [working(1)], working(2), ROOMY, False)
    assert not admits(LIMITS, [working(1), working(2)], working(3), ROOMY, False)
    assert not admits(LIMITS, [working(1), working(2)], gating(3), ROOMY, False)


def test_one_gate_runs_at_a_time() -> None:
    roomy_pool = LIMITS.model_copy(update={"pool_gb": 100.0})
    assert not admits(roomy_pool, [gating(1)], gating(2), ROOMY, False)
    assert admits(
        roomy_pool.model_copy(update={"gates_at_once": 2}), [gating(1)], gating(2), ROOMY, False
    )


def test_finishing_work_goes_before_starting_new_work() -> None:
    assert not admits(LIMITS, [working(1)], working(2), ROOMY, True)
    assert admits(LIMITS, [working(1)], gating(2), ROOMY, True)


def test_the_machine_keeps_its_reserve_and_its_calm() -> None:
    tight = ROOMY.model_copy(update={"available_gb": 11.0})
    assert not admits(LIMITS, [working(1)], working(2), tight, False)
    pressed = ROOMY.model_copy(update={"pressure_percent": 35.0})
    assert not admits(LIMITS, [working(1)], working(2), pressed, False)


def test_cpus_split_into_disjoint_sets_of_the_asked_size() -> None:
    assert cpu_sets(list(range(8)), 3) == [[0, 1, 2], [3, 4, 5]]
    assert cpu_sets([0, 1], 4) == [[0, 1]]


def test_the_pool_decides_how_many_tasks_start_at_once() -> None:
    assert tasks_at_once(LIMITS, 6) == 2
    assert tasks_at_once(LIMITS, 1) == 1
    assert caps_exceeding_the_pool(LIMITS.model_copy(update={"pool_gb": 9.0})) == ["gate_memory_gb"]


class TestTwoTasksSharingAPoolThatFitsOneGate:
    admission = Admission(
        LIMITS.model_copy(update={"pool_gb": 16.0}), lambda: ROOMY, [[0, 1], [2, 3]]
    )

    def hold_in_background(
        self, holder: int, kind: ReservationKind, admitted: threading.Event
    ) -> threading.Thread:
        def hold() -> None:
            if self.admission.hold(holder, kind, lambda: True, lambda: None):
                admitted.set()

        thread = threading.Thread(target=hold)
        thread.start()
        return thread

    def test_both_start_working_on_cpus_of_their_own(self) -> None:
        assert self.admission.hold(1, ReservationKind.WORKING, lambda: True, lambda: None)
        assert self.admission.hold(2, ReservationKind.WORKING, lambda: True, lambda: None)
        assert self.admission.cpus_of(1) != self.admission.cpus_of(2)

    def test_a_third_start_is_refused_once_its_wait_is_over(self) -> None:
        waits: list[int] = []
        assert not self.admission.hold(
            3, ReservationKind.WORKING, lambda: False, lambda: waits.append(3)
        )
        assert waits == []

    def test_a_gate_waits_while_the_other_task_still_works_and_neither_deadlocks(self) -> None:
        first_gate = threading.Event()
        first = self.hold_in_background(1, ReservationKind.GATING, first_gate)
        assert not first_gate.wait(0.5)
        second_gate = threading.Event()
        second = self.hold_in_background(2, ReservationKind.GATING, second_gate)
        assert second_gate.wait(WAIT_SECONDS)
        assert not first_gate.is_set()
        self.admission.release(2)
        assert first_gate.wait(WAIT_SECONDS)
        first.join(WAIT_SECONDS)
        second.join(WAIT_SECONDS)
