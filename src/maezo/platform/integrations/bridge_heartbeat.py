"""Heartbeat de liveness das pontes Kafka (R-061 / F-149).

As pontes (`notifications_bridge` hoje; `network_change_bridge` e `consent_revocation_bridge`
quando forem construidas) nao sobem servidor de health. A liveness do chart era
`pgrep -f <modulo>`, mas a imagem de runtime (`python:3.12-slim` + `libpq5`, deploy/Dockerfile)
nao instala `procps`: `pgrep` saia 127 e o kubelet reiniciava o pod para sempre (o mesmo achado F3
que o a2a-outbox-relay ja corrigiu com heartbeat em `maezo.a2a.outbox_relay`).

Mecanismo: `run_heartbeat` e uma task asyncio no MESMO event loop do consumidor; toca o arquivo a
cada `interval_s`. A probe `exec` do chart (`python -c`, sem importar `maezo`) le
`BRIDGE_HEARTBEAT_PATH`/`BRIDGE_HEARTBEAT_INTERVAL_S` do proprio ambiente do container e falha se
o mtime estiver mais velho que `heartbeat_stale_after_s(interval_s)`.

Limite honesto: o sinal prova que o processo e o event loop estao vivos e responsivos (um loop
travado por chamada sincrona bloqueante deixa de tocar o arquivo e o pod reinicia). Um `await`
pendurado para sempre em I/O NAO e detectado — o mesmo limite do `pgrep` que este heartbeat
substitui. Uma ponte ociosa (topico sem mensagens) continua viva, como deve.

`path` vazio/`None` desliga o heartbeat (default das settings: desligado; o chart o liga).
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Final

import structlog

logger = structlog.get_logger(__name__)

#: Intervalo default entre toques do arquivo (segundos).
DEFAULT_HEARTBEAT_INTERVAL_S: Final[float] = 10.0

#: Piso do limiar de envelhecimento (mesma regra de `outbox_relay.MIN_HEARTBEAT_STALE_AFTER_S`).
MIN_HEARTBEAT_STALE_AFTER_S: Final[float] = 5.0

_write_failure_logged = False


def _reset_write_failure_logged_for_tests() -> None:
    """Somente testes: reseta a flag de log-unico de `touch_heartbeat`."""
    global _write_failure_logged
    _write_failure_logged = False


def heartbeat_stale_after_s(interval_s: float) -> float:
    """Limiar de envelhecimento `max(3 * interval_s, 5s)` — a formula que a probe do chart replica."""
    return max(3.0 * interval_s, MIN_HEARTBEAT_STALE_AFTER_S)


def touch_heartbeat(path: str | None) -> None:
    """Atualiza o mtime do arquivo. Falha de escrita nunca derruba a ponte: loga UMA vez e segue."""
    global _write_failure_logged
    if not path:
        return
    try:
        Path(path).touch()
    except OSError as exc:
        if not _write_failure_logged:
            logger.warning("bridge_heartbeat_write_failed", path=path, error=str(exc))
            _write_failure_logged = True


async def run_heartbeat(path: str | None, *, interval_s: float, stop_event: asyncio.Event) -> None:
    """Toca `path` imediatamente e a cada `interval_s` ate `stop_event` ser setado."""
    if not path:
        return
    while not stop_event.is_set():
        touch_heartbeat(path)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
