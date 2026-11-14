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


def test_main_wires_settings_heartbeat_path_into_run_heartbeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`notifications_bridge.main()` liga o heartbeat com `settings.heartbeat_path` (revisao N1).

    Kafka/motor/DLQ substituidos por fakes; o `run_consumer_loop` real roda sobre um consumidor
    fake cuja iteracao espera o arquivo aparecer (com mtime recente) e entao termina, o que faz o
    `main()` encerrar pelo caminho normal. Se o `main()` passasse `None` (ou outro path) ao
    `run_heartbeat`, o arquivo nunca apareceria e o teste falharia.
    """
    from maezo.platform.integrations import notifications_bridge as nb

    hb_path = tmp_path / "hb"
    monkeypatch.setenv("BRIDGE_HEARTBEAT_PATH", str(hb_path))
    monkeypatch.setenv("BRIDGE_HEARTBEAT_INTERVAL_S", "0.05")
    observed: dict[str, float] = {}
    events: list[str] = []

    class _FakeTransport:
        async def close(self) -> None:
            events.append("transport.close")

    class _FakePublisher:
        async def start(self) -> None:
            events.append("dlq.start")

        async def stop(self) -> None:
            events.append("dlq.stop")

    class _FakeDlq:
        publisher = _FakePublisher()

    class _FakeConsumer:
        def __init__(self, **_: object) -> None:
            pass

        async def start(self) -> None:
            events.append("consumer.start")

        async def stop(self) -> None:
            events.append("consumer.stop")

        async def commit(self) -> None:  # pragma: no cover - nenhuma mensagem e entregue
            raise AssertionError("nenhuma mensagem deveria ser confirmada")

        async def __aiter__(self):  # type: ignore[no-untyped-def]
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if hb_path.exists():
                    observed["age_s"] = time.time() - hb_path.stat().st_mtime
                    return
                await asyncio.sleep(0.01)
            return
            yield  # pragma: no cover - torna o metodo um gerador assincrono

    monkeypatch.setattr(nb, "build_bridge", lambda settings: (object(), _FakeTransport(), object()))
    monkeypatch.setattr(nb, "AioKafkaBridgeConsumer", _FakeConsumer)
    monkeypatch.setattr(nb, "build_dlq_shunt", lambda settings, audit_sink: _FakeDlq())

    asyncio.run(asyncio.wait_for(nb.main(), timeout=5.0))

    assert "age_s" in observed, "main() nao tocou BRIDGE_HEARTBEAT_PATH enquanto o loop rodava"
    assert observed["age_s"] < 5.0
    assert events == ["consumer.start", "dlq.start", "consumer.stop", "dlq.stop", "transport.close"]
