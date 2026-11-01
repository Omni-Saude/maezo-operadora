"""Fiacao da flag dark `MAEZO_PAGTO_RESOLVE_LASTRO` (pacote PAGTO PR-D), ponta a ponta:

(a) alias de env + default OFF; (b) `_bring_up_dependencies` repassa a setting E a costura do
motor; (c) `register_default_workers` REAL repassa ao registro de PAGTO — provado pela EXECUCAO do
handler registrado no topico (`operadora.pagto.validate_payment_data`), nao por inspecao de kwargs:
ligada, a saida ganha os fatos do lastro, lidos atraves do `engine=` repassado; desligada (ou
omitida, ou valor-lixo), a saida e' a de sempre e o escopo de leitura do topico nao muda.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from maezo.runtime.worker_runtime.service import WorkerState, register_default_workers
from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport, HistoricInstanceRecord
from maezo.tools.workers.harness import FakeWorkerTransport, WorkerHarness
from maezo.tools.workers.pagto import register_pagto_workers
from maezo.tools.workers.pagto_lastro import LASTRO_SAIDAS, VALIDATE_COM_LASTRO_VARIABLES

_TOPIC = "operadora.pagto.validate_payment_data"
_ALIAS = "MAEZO_PAGTO_RESOLVE_LASTRO"

_ORDEM: dict[str, Any] = {
    "tenant_id": "amh",
    "ordem_pagamento_id": "ORDEM-CONTA-amh-LOTE-W-prov:w",
    "numero_lote_tiss": "LOTE-W",
    "prestador_id": "prov:w",
    "instrumento_pagamento": "ted",
    "valor_pagamento_cents": 50000,
    "lastro_origem": "contas_adjudicacao_humana",
    "lastro_confirmado": True,
}


def _motor_com_origem() -> FakeCibSevenTransport:
    engine = FakeCibSevenTransport()
    engine.seed_historic_instance_record(
        HistoricInstanceRecord(
            instance_id="pi-contas-w",
            business_key="CONTAS-amh-LOTE-W",
            process_key="SP-OP-CONTAS-001",
            state="COMPLETED",
            end_activity_id="End_ContaAprovadaHumano",
            variables={"numero_lote_tiss": "LOTE-W", "prestador_id": "prov:w", "valor_liberado_brl": 500.0},
        )
    )
    return engine


def _harness() -> WorkerHarness:
    return WorkerHarness(FakeWorkerTransport(), worker_id="unit-test-pagto-lastro-flag")


def _executa(harness: WorkerHarness) -> dict[str, Any]:
    worker = harness.registry.get(_TOPIC)
    assert worker is not None
    result: dict[str, Any] = worker.execute(dict(_ORDEM))
    return result


# (a) ----------------------------------------------------------------------------------------


def test_settings_alias_e_default_desligado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ALIAS, raising=False)
    assert WorkerRuntimeSettings().pagto_resolve_lastro is False
    monkeypatch.setenv(_ALIAS, "true")
    assert WorkerRuntimeSettings().pagto_resolve_lastro is True


# (b) ----------------------------------------------------------------------------------------


class _SpyHarness:
    def __init__(self, transport: Any, *, worker_id: str, audit_sink: Any = None, **kwargs: Any) -> None:
        del transport, worker_id, audit_sink, kwargs
        self._topics: list[str] = []

    def register_worker(self, worker: Any, **_kw: Any) -> None:
        self._topics.append(worker.topic)

    def register(self, topic: str, handler: Any, *, variables: Any = None) -> None:
        self._topics.append(topic)

    @property
    def registered_topics(self) -> list[str]:
        return sorted(self._topics)

    async def run(self) -> None:
        await asyncio.Event().wait()


@pytest.mark.parametrize("flag", [False, True])
async def test_bring_up_repassa_a_flag_e_o_motor(monkeypatch: pytest.MonkeyPatch, flag: bool) -> None:
    import maezo.runtime.worker_runtime.service as svc

    captured: dict[str, Any] = {}

    def _spy_register(_harness: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    async def _probe_red(_sink: Any, _timeout: float) -> bool:
        return False  # sem rotacao de fetch: este teste so prova a fiacao

    monkeypatch.setenv(_ALIAS, "true" if flag else "false")
    monkeypatch.setattr(svc, "WorkerHarness", _SpyHarness)
    monkeypatch.setattr(svc, "register_default_workers", _spy_register)
    monkeypatch.setattr(svc, "_expected_worker_topics", lambda installed=False: frozenset({"t"}))
    monkeypatch.setattr(svc, "_probe_audit_sink", _probe_red)

    state = WorkerState(
        settings=WorkerRuntimeSettings(DATABASE_URL="postgresql://maezo@localhost:5432/maezo")
    )
    try:
        await svc._bring_up_dependencies(state)
        assert captured["pagto_resolve_lastro"] is flag
        # A costura que o resolvedor usa e' a do motor da raiz (gated, fresh-client).
        assert captured["engine"] is state.engine_transport
    finally:
        if state.transport is not None:
            await state.transport.close()
        if state.audit_sink is not None:
            await state.audit_sink.aclose()


def test_engine_da_raiz_expoe_a_costura_de_resolucao() -> None:
    """Sem isto a flag ligada daria `INDISPONIVEL` para sempre no daemon: o decorator cercado so'
    expoe `resolve_historic_instance` quando o inner (`FreshClientCibSevenTransport`) a tem."""
    from maezo.gateway.seams.cibseven import GatedHistoryResolvingCibSevenTransport
    from maezo.gateway.tool_registry import build_cibseven_seam
    from maezo.runtime.worker_runtime.service import _worker_seam_cached
    from maezo.tools.mcp_cibseven.transport import HistoricInstanceResolvingTransport

    engine = build_cibseven_seam(
        seam=_worker_seam_cached("amh"), base_url="http://engine.invalid/engine-rest", fresh_client=True
    )
    assert isinstance(engine, GatedHistoryResolvingCibSevenTransport)
    assert isinstance(engine, HistoricInstanceResolvingTransport)


# (c) ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("kwargs", [{}, {"pagto_resolve_lastro": False}])
def test_register_default_workers_desligada_saida_de_sempre(kwargs: dict[str, Any]) -> None:
    harness = _harness()
    register_default_workers(harness, engine=_motor_com_origem(), **kwargs)
    out = _executa(harness)
    assert not (set(out) & LASTRO_SAIDAS)
    assert out["lastro_confirmado"] is True  # caminho legado (fatos estritos off): eco, como antes
    assert harness._topic_variables[_TOPIC] is None  # escopo do topico inalterado


def test_register_default_workers_ligada_resolve_pelo_motor_repassado() -> None:
    harness = _harness()
    register_default_workers(harness, engine=_motor_com_origem(), pagto_resolve_lastro=True)
    out = _executa(harness)
    assert out["lastro_status"] == "CONFIRMADO"
    assert out["lastro_origem_tipo"] == "contas_humana"
    assert out["lastro_origem_instancia_id"] == "pi-contas-w"
    # Rota inalterada: `lastro_confirmado` segue o caminho que `validate_pagto` ja tinha.
    assert out["lastro_confirmado"] is True
    assert harness._topic_variables[_TOPIC] == VALIDATE_COM_LASTRO_VARIABLES


def test_register_default_workers_ligada_com_fatos_estritos_combina() -> None:
    harness = _harness()
    register_default_workers(
        harness, engine=_motor_com_origem(), pagto_resolve_lastro=True, pagto_fatos_estritos=True
    )
    out = _executa(harness)
    assert out["lastro_confirmado"] is False
    assert out["lastro_status"] == "CONFIRMADO"


def test_register_default_workers_ligada_sem_motor_falha_fechado() -> None:
    harness = _harness()
    register_default_workers(harness, pagto_resolve_lastro=True)
    assert _executa(harness)["lastro_status"] == "INDISPONIVEL"


@pytest.mark.parametrize("lixo", ["true", 1, "1", object()])
def test_registro_de_pagto_so_liga_com_true(lixo: Any) -> None:
    harness = _harness()
    register_pagto_workers(harness, engine=_motor_com_origem(), pagto_resolve_lastro=lixo)
    assert not (set(_executa(harness)) & LASTRO_SAIDAS)


def test_topicos_esperados_nao_mudam_com_a_flag() -> None:
    """O topico e' o MESMO (`ST_ValidatePaymentData`): readiness e BPMN nao mudam com a flag."""
    desligada = _harness()
    register_default_workers(desligada)
    ligada = _harness()
    register_default_workers(ligada, pagto_resolve_lastro=True)
    assert desligada.registered_topics == ligada.registered_topics
