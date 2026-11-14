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


# -------------------------------------------------------------------------------------------------
# Pacote PAGTO PR-D: as duas leituras do historico que `gate_cibseven` exige para expor
# `resolve_historic_instance` ao daemon de workers (consumidor: `pagto_lastro.resolve_lastro`).
# -------------------------------------------------------------------------------------------------


def _fake_com_historico() -> FakeCibSevenTransport:
    from maezo.tools.mcp_cibseven.transport import HistoricInstanceRecord, HistoricProcessVariables

    shared = FakeCibSevenTransport()
    shared.seed_historic_instance_record(
        HistoricInstanceRecord(
            instance_id="pi-h",
            business_key="CONTAS-amh-L",
            process_key="SP-OP-CONTAS-001",
            state="COMPLETED",
            end_activity_id="End_ContaAprovadaHumano",
            variables={"prestador_id": "prov:h", "valor_liberado_brl": 10.0},
        )
    )
    shared.seed_historic_variables(
        HistoricProcessVariables(
            instance_id="pi-h",
            business_key="CONTAS-amh-L",
            process_key="SP-OP-CONTAS-001",
            state="COMPLETED",
            variables={"prestador_id": "prov:h"},
        )
    )
    return shared


def test_resolve_historic_instance_delega_fresco_por_chamada() -> None:
    shared = _fake_com_historico()
    construidos: list[FakeCibSevenTransport] = []

    def factory() -> FakeCibSevenTransport:
        construidos.append(shared)
        return shared

    engine = FreshClientCibSevenTransport("http://x", transport_factory=factory)
    record = asyncio.run(
        engine.resolve_historic_instance(
            "SP-OP-CONTAS-001", variable_equals={"prestador_id": "prov:h"}, names=("valor_liberado_brl",)
        )
    )
    assert (record.instance_id, record.end_activity_id) == ("pi-h", "End_ContaAprovadaHumano")
    assert record.variables == {"valor_liberado_brl": 10.0}
    historico = asyncio.run(engine.read_historic_variables("pi-h", ("prestador_id",)))
    assert historico is not None and historico.variables == {"prestador_id": "prov:h"}
    assert len(construidos) == 2  # um inner por chamada


def test_leituras_do_historico_com_inner_sem_capacidade_levantam() -> None:
    from maezo.tools.mcp_cibseven.transport import CibSevenError

    counters = {"constructions": 0, "closes": 0}
    engine = FreshClientCibSevenTransport(
        "http://x", transport_factory=_spy_factory(FakeCibSevenTransport(), counters)
    )
    with pytest.raises(CibSevenError, match="resolve_historic_instance"):
        asyncio.run(engine.resolve_historic_instance("SP-OP-CONTAS-001", business_key="k"))
    with pytest.raises(CibSevenError, match="read_historic_variables"):
        asyncio.run(engine.read_historic_variables("pi", ("x",)))
    assert counters == {"constructions": 2, "closes": 2}  # fecha mesmo quando recusa


def test_gate_cibseven_sobre_o_fresh_expoe_a_resolucao() -> None:
    from maezo.gateway.seams.cibseven import GatedHistoryResolvingCibSevenTransport, gate_cibseven
    from maezo.runtime.worker_runtime.service import _worker_seam_cached

    gated = gate_cibseven(FreshClientCibSevenTransport("http://engine.invalid"), _worker_seam_cached("amh"))
    assert isinstance(gated, GatedHistoryResolvingCibSevenTransport)
