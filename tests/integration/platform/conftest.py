"""Local fixture override for `tests/integration/platform/` (T2.6-EB3 part 5).

The parent `tests/integration/conftest.py` declares an `autouse=True` session fixture
(`_skip_if_engine_unreachable`) that gates EVERY test collected under `tests/integration/` on a
reachable CIB Seven engine — correct for the BPMN-process suites that live there, but wrong for
this directory's live-Kafka proof (`test_notifications_bridge_live_kafka.py`), which needs a
Kafka broker, NOT a CIB Seven engine (its own `NotificationBridge` uses a spy starter, never a
real `CibSevenTransport`). Overriding the fixture by name HERE ONLY (pytest resolves the
closest-directory conftest.py first) removes that unrelated dependency for this suite while every
OTHER integration suite keeps the parent engine gate unchanged.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _skip_if_engine_unreachable() -> None:
    """No-op override: this directory's own `kafka_bootstrap_servers` fixture (in
    `test_notifications_bridge_live_kafka.py`) is the real reachability gate — a CIB Seven
    engine is not part of this suite's honest external boundary."""
    return None
