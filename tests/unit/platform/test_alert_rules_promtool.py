"""R-085: behavioural rule tests for `deploy/observability/alert-rules.yml` via `promtool test rules`.

The YAML fences in this directory prove the SHAPE of each rule; these promtool cases prove the
BEHAVIOUR the owner decision of 2026-10-31 (#1084) asked for, on synthetic series: no +Inf on the
agent error ratio, DLQ by new arrivals (and clearing), escalation needs volume and both windows,
crash loops on container restarts, Kafka stalled consumer, and the L1 approval drop against its own
baseline. Skipped where the `promtool` binary is not installed (it is not a Python dependency).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Final

import pytest

_DIR: Final[Path] = Path(__file__).resolve().parent / "promtool"
_PROMTOOL: Final[str | None] = shutil.which("promtool")


@pytest.mark.skipif(_PROMTOOL is None, reason="promtool binary not installed")
@pytest.mark.parametrize("case", ["alert-rules.promtool.yml", "alert-rules-hitl.promtool.yml"])
def test_alert_rules_behave_as_decided(case: str) -> None:
    assert _PROMTOOL is not None
    result = subprocess.run(
        [_PROMTOOL, "test", "rules", str(_DIR / case)],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
