"""Unit tests for `FreshClientCibSevenTransport` — the FRESH-CLIENT-PER-CALL engine seam (T-D).

Proves the property that fixes the live-reproduced "Event loop is closed" defect: a NEW underlying
transport (hence a fresh httpx client) is constructed and closed PER method call, so consecutive
calls from distinct `asyncio.run(...)` bridges (fresh event loops — how the harness dispatches sync
workers) never share/outlive a client. Uses a spy factory asserting per-call construction (no live
engine), the approach the design names.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from maezo.tools.mcp_cibseven.transport import (
    CibSevenTransport,
    FakeCibSevenTransport,
    ProcessInstance,
)
from maezo.tools.workers.cibseven_engine import FreshClientCibSevenTransport


class _CountingClose:
    """Delegates to a shared fake but counts its own `close()` — one per per-call construction."""

    def __init__(self, inner: FakeCibSevenTransport, counters: dict[str, int]) -> None:
        self._inner = inner
        self._counters = counters

    async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
        return await self._inner.find_active_instance(business_key)

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        return await self._inner.start_process_instance(process_key, business_key, variables)

    async def correlate_message(
        self,
        message_name: str,
        business_key: str,
        variables: dict[str, Any],
        *,
        correlation_keys: dict[str, Any] | None = None,
        all_matching: bool = False,
    ) -> None:
        await self._inner.correlate_message(
            message_name,
            business_key,
            variables,
            correlation_keys=correlation_keys,
            all_matching=all_matching,
        )

    async def get_process_status(self, business_key: str) -> Any:
        return await self._inner.get_process_status(business_key)

    async def close(self) -> None:
        self._counters["closes"] += 1


def _spy_factory(shared: FakeCibSevenTransport, counters: dict[str, int]) -> Any:
    def factory() -> _CountingClose:
        counters["constructions"] += 1
        return _CountingClose(shared, counters)

    return factory


def test_conforms_to_cibseven_transport_protocol() -> None:
    engine = FreshClientCibSevenTransport("http://engine:8080/engine-rest")
    assert isinstance(engine, CibSevenTransport)


def test_fresh_underlying_transport_constructed_and_closed_per_call() -> None:
    """The core anti-'Event loop is closed' property: two calls across two SEPARATE asyncio.run
    loops construct (and close) a fresh underlying transport EACH time — never a shared client."""
    shared = FakeCibSevenTransport()
    counters = {"constructions": 0, "closes": 0}
    engine = FreshClientCibSevenTransport("http://x", transport_factory=_spy_factory(shared, counters))

    # Two dispatches, each on its own fresh event loop (mirrors `asyncio.run` per worker task).
    asyncio.run(engine.find_active_instance("CANCEL-t1-C-1"))
    asyncio.run(engine.find_active_instance("CANCEL-t1-C-1"))

    assert counters["constructions"] == 2, "a fresh underlying transport must be built PER call"
    assert counters["closes"] == 2, "each per-call transport must be closed (finally), no leak"


def test_start_process_instance_also_fresh_per_call() -> None:
    shared = FakeCibSevenTransport()
    counters = {"constructions": 0, "closes": 0}
    engine = FreshClientCibSevenTransport("http://x", transport_factory=_spy_factory(shared, counters))

    inst = asyncio.run(
        engine.start_process_instance(
            "SP-OP-CANCEL-001", "CANCEL-t1-C-9", {"tipo_solicitacao": "inadimplencia"}
        )
    )
    assert inst.business_key == "CANCEL-t1-C-9"
    assert counters["constructions"] == 1
    assert counters["closes"] == 1


def test_closes_underlying_even_when_call_raises() -> None:
    """The per-call transport is closed in a `finally` even when the delegated call raises."""
    counters = {"closes": 0}

    class _Boom:
        async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
            raise RuntimeError("engine down")

        async def close(self) -> None:
            counters["closes"] += 1

    engine = FreshClientCibSevenTransport("http://x", transport_factory=lambda: _Boom())
    with pytest.raises(RuntimeError, match="engine down"):
        asyncio.run(engine.find_active_instance("k"))
    assert counters["closes"] == 1


def test_close_is_noop() -> None:
    """No persistent client is held -> close() is a safe no-op (daemon calls it on drain)."""
    engine = FreshClientCibSevenTransport("http://x")
    assert asyncio.run(engine.close()) is None
