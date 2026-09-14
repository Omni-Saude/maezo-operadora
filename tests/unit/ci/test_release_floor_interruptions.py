"""Owned measurement processes must not survive interruption at spawn boundaries."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from scripts.ci import generate_release_floor as floor


@pytest.mark.parametrize("boundary", ["registration", "mask-restoration"])
def test_measurement_reaps_spawned_group_on_early_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    (tmp_path / "pytest.py").write_text("import time; time.sleep(30)\n")
    groups: list[int] = []
    original_record = floor.process_groups._record_pending
    original_guard = floor.process_groups._spawn_signal_guard
    previous_handler = signal.getsignal(signal.SIGTERM)

    def record(pgid: int) -> None:
        groups.append(pgid)
        original_record(pgid)
        if boundary == "registration":
            raise RuntimeError("registration failed after ownership recorded")

    entered = 0

    @contextmanager
    def mask(*args, **kwargs) -> Iterator[None]:
        nonlocal entered
        entered += 1
        first = entered == 1
        with original_guard(*args, **kwargs):
            yield
        if first and boundary == "mask-restoration":
            raise KeyboardInterrupt

    monkeypatch.setattr(floor.process_groups, "_record_pending", record)
    monkeypatch.setattr(floor.process_groups, "_spawn_signal_guard", mask)
    with pytest.raises(RuntimeError if boundary == "registration" else KeyboardInterrupt):
        floor._run_unit_measurement(tmp_path, sys.executable)
    assert len(groups) == 1
    assert not floor.process_groups._group_exists(groups[0])
    assert groups[0] not in floor.process_groups._pending_groups
    assert signal.getsignal(signal.SIGTERM) == previous_handler


def test_real_sigterm_reaps_measurement_group(tmp_path: Path) -> None:
    (tmp_path / "pytest.py").write_text(
        "import os,pathlib,time\npathlib.Path('child-pgid').write_text(str(os.getpgrp()))\ntime.sleep(30)\n"
    )
    code = (
        "import pathlib,sys\n"
        "from scripts.ci.generate_release_floor import _run_unit_measurement\n"
        "_run_unit_measurement(pathlib.Path(sys.argv[1]), sys.executable)\n"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[3],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pgid: int | None = None
    try:
        deadline = time.monotonic() + 10
        marker = tmp_path / "child-pgid"
        while not marker.exists():
            assert parent.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.01)
        pgid = int(marker.read_text())
        parent.send_signal(signal.SIGTERM)
        assert parent.wait(timeout=10) != 0
        assert not floor.process_groups._group_exists(pgid)
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=10)
        # Test cleanup is limited to the exact subprocess group this probe created.
        if pgid is not None and floor.process_groups._group_exists(pgid):
            os.killpg(pgid, signal.SIGKILL)
