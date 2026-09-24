import os
from pathlib import Path

import pytest

from weekend_loop.lock import RunLockHeldError, lock_path, run_lock


def test_a_refused_attempt_leaves_the_holder_pid_in_the_lock_file(tmp_path: Path) -> None:
    holder = f"{os.getpid()}\n"
    with run_lock(tmp_path) as path:
        with pytest.raises(RunLockHeldError, match=f"pid {os.getpid()}"):
            with run_lock(tmp_path):
                pass
        assert path.read_text() == holder


def test_a_new_holder_replaces_whatever_the_previous_one_wrote(tmp_path: Path) -> None:
    lock_path(tmp_path).write_text("4194304 left behind by a longer previous pid\n")

    with run_lock(tmp_path) as path:
        assert path.read_text() == f"{os.getpid()}\n"
