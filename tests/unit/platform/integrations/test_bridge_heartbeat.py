"""R-061 / F-149 — heartbeat de liveness das pontes Kafka (substitui `pgrep`).

A imagem de runtime (`python:3.12-slim` + `libpq5`, deploy/Dockerfile) nao tem `procps`: a probe
`pgrep -f ...` das tres pontes saia 127 e o kubelet reiniciava o pod para sempre. O helper
`bridge_heartbeat` toca um arquivo periodicamente enquanto o event loop da ponte esta vivo; a
probe do chart compara o mtime contra `max(3 * intervalo, 5s)`.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from maezo.platform.integrations import bridge_heartbeat as hb
from maezo.platform.integrations.notifications_bridge import NotificationsBridgeSettings


@pytest.fixture(autouse=True)
def _reset_once_flag() -> None:
    hb._reset_write_failure_logged_for_tests()


def test_touch_creates_and_refreshes_the_file(tmp_path: Path) -> None:
    path = tmp_path / "bridge.heartbeat"
    hb.touch_heartbeat(str(path))
    assert path.exists()
    os.utime(path, (0, 0))
    hb.touch_heartbeat(str(path))
    assert time.time() - path.stat().st_mtime < 5


@pytest.mark.parametrize("path", [None, ""])
def test_empty_path_is_a_no_op(path: str | None, tmp_path: Path) -> None:
    hb.touch_heartbeat(path)
    assert list(tmp_path.iterdir()) == []


def test_write_failure_is_swallowed_and_logged_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(hb.logger, "warning", lambda event, **_: calls.append(event))
    bad = str(tmp_path / "missing-dir" / "bridge.heartbeat")
    hb.touch_heartbeat(bad)
    hb.touch_heartbeat(bad)
    assert calls == ["bridge_heartbeat_write_failed"]


@pytest.mark.parametrize(("interval", "expected"), [(10.0, 30.0), (0.5, 5.0), (1.0, 5.0)])
def test_stale_threshold_is_floored(interval: float, expected: float) -> None:
    assert hb.heartbeat_stale_after_s(interval) == expected


def test_run_heartbeat_keeps_touching_until_stopped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "bridge.heartbeat"
    touches: list[str | None] = []
    original = hb.touch_heartbeat

    def counting(p: str | None) -> None:
        touches.append(p)
        original(p)

    monkeypatch.setattr(hb, "touch_heartbeat", counting)

    async def scenario() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(hb.run_heartbeat(str(path), interval_s=0.01, stop_event=stop))
        await asyncio.sleep(0.08)
        stop.set()
        await asyncio.wait_for(task, timeout=1.0)

    asyncio.run(scenario())
    assert len(touches) >= 3
    assert path.exists()


def test_run_heartbeat_with_empty_path_returns_immediately() -> None:
    async def scenario() -> None:
        await asyncio.wait_for(hb.run_heartbeat("", interval_s=0.01, stop_event=asyncio.Event()), 1.0)

    asyncio.run(scenario())


def test_settings_read_the_chart_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIDGE_HEARTBEAT_PATH", "/tmp/x.heartbeat")
    monkeypatch.setenv("BRIDGE_HEARTBEAT_INTERVAL_S", "7.5")
    settings = NotificationsBridgeSettings(_env_file=None)  # type: ignore[call-arg]
    assert settings.heartbeat_path == "/tmp/x.heartbeat"
    assert settings.heartbeat_interval_s == 7.5


def test_settings_default_heartbeat_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRIDGE_HEARTBEAT_PATH", raising=False)
    monkeypatch.delenv("BRIDGE_HEARTBEAT_INTERVAL_S", raising=False)
    settings = NotificationsBridgeSettings(_env_file=None)  # type: ignore[call-arg]
    assert settings.heartbeat_path is None
    assert settings.heartbeat_interval_s == hb.DEFAULT_HEARTBEAT_INTERVAL_S
