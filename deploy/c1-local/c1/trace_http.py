"""Diagnostico: roda um passo com cada resposta HTTP do engine impressa (status + corpo curto).

`python -m c1.trace_http <passo>`. Os clientes do repo recusam sem dizer o motivo (fail-closed, sem
eco de material); o C1 precisa do status para registrar ONDE parou. So o status e o corpo publico da
recusa do plugin (ex.: `{"error":"READ_DEPENDENCY_UNAVAILABLE"}`) sao impressos.
"""

from __future__ import annotations

import importlib
import sys

import httpx

_original = httpx.AsyncClient.send


async def _send(self, request, *args, **kwargs):  # type: ignore[no-untyped-def]
    response = await _original(self, request, *args, **kwargs)
    body = b""
    try:
        if not kwargs.get("stream"):
            body = response.content[:160]
        else:
            await response.aread()
            body = response.content[:160]
    except Exception:  # noqa: BLE001 - diagnostico
        pass
    print(f"HTTP {request.method} {request.url.path} -> {response.status_code} {body!r}", file=sys.stderr)
    return response


httpx.AsyncClient.send = _send  # type: ignore[method-assign]

if __name__ == "__main__":
    step = sys.argv[1]
    from c1.__main__ import STEPS

    importlib.import_module(STEPS[step]).main()
