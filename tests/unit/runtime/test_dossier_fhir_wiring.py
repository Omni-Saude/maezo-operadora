"""CC-03 / AND-03 (perna `fhir`) — a raiz de composicao do dossie injeta o leitor FHIR GATEADO.

O defeito: `build_dossier_delegation_dispatcher` (a UNICA raiz que monta as tres arestas de
dossie originadas pelo worker daemon) nao tinha parametro `fhir`/`population` e chamava
`make_carolina_handler(llm, dmn=, cibseven=, audit_sink=)` / `make_andre_handler(...)` sem eles,
embora os dois aceitem `fhir=` (andre tambem `population=`). Consequencia: TODO dossie de
producao pelo caminho A2A caia no ramo degradado dos dois grafos —
`carolina/graph.py::CarolinaGraph.gather` anexava "leitor de resumo nao configurado ..." e
retornava cedo; `andre/graph.py::AndreGraph.gather` anexava "leitor FHIR nao configurado ...".
E o daemon worker (`worker_runtime/service.py` STEP B) nao construia seam FHIR nenhum.

O que estes testes provam:
  1. a raiz REPASSA o leitor por AGENTE aos dois handler factories (`fhir=`), e repassa
     `population=` explicitamente a andre;
  2. o objeto repassado a carolina BASTA para silenciar o ramo degradado do grafo dela (o
     `gather` real, com o MESMO objeto que a raiz entregou, nao emite mais a nota de lacuna e
     efetivamente le o resumo);
  3. a raiz FALHA FECHADO diante de uma chave de agente desconhecida no mapa (um typo em
     `{"carolinaa": reader}` degradaria o dossie em silencio — exatamente o defeito em questao);
  4. o daemon worker constroi os seams FHIR pelo CONSTRUTOR SANCIONADO
     (`gateway/tool_registry.py`), entregando o wrapper GATEADO (`GatedFhirReader`) e nao o
     adaptador cru, com o principal CORRETO por agente (carolina != andre — a decisao do PEP
     `leitura_phi_clinica` e por principal);
  5. a DEGRADACAO e OBSERVAVEL: `/readyz` publica o check nao-fatal `dossier_fhir_ready`, com
     ou sem `FHIR_BASE_URL` — antes disto `WorkerState.dossier_fhir_detail` era WRITE-ONLY (zero
     leituras) e um `FHIR_BASE_URL` vazio deixava `effect_seams_gated` VERDE (nada ungated existe
     quando nada foi construido), ou seja: dossie degradado, painel limpo;
  6. o mesmo vale para andre (simetria com o item 2): o leitor que a raiz entrega a ele basta
     para silenciar o ramo degradado do `AndreGraph.gather` (fluxo `pagto_dossier`);
  7. os dois literais de id de agente (`service._DOSSIER_FHIR_AGENT_IDS` e
     `a2a_composition._DOSSIER_EDGE_FHIR_AGENT_IDS`) sao IGUAIS — a recusa da raiz so cobre uma
     direcao (id que ela nao serve); a direcao oposta (aresta servida sem seam construido)
     degradaria em silencio e e este teste que a impede. Desde R-081 o par correto e' o
     SUBCONJUNTO FHIR da aresta, nao a aresta inteira: `_DOSSIER_EDGE_AGENT_IDS` passou a incluir
     `fernando`, cujo `build(config)` nao tem chave `fhir` alguma — e uma terceira cerca reapura
     esse subconjunto pela ASSINATURA real de cada `make_<id>_handler`, para que um agente da
     aresta que ACEITE `fhir=` nao possa ficar de fora dele em silencio;
  8. `population` continua `None` explicito — BLOCKED(external WB.4): nao existe cliente de lago
     concreto em `src/` (`andre/graph.py::PopulationFeatureClient` e so Protocol,
     `gateway/seams/population.py` "SHIPS UNWIRED", `build_agent_seams` omite a chave de
     proposito). Degradacao honesta, nunca um cliente inventado.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest
from tests.support.audit_fakes import FakeStartAuditSink
from tests.unit.a2a.fakes import RecordingProducer

from maezo.a2a import per_tenant_key_env_var
from maezo.agents.andre.graph import AndreGraph, AndreState
from maezo.agents.carolina.graph import CarolinaGraph, CarolinaState
from maezo.gateway.seams.fhir import GatedFhirReader
from maezo.runtime.agent_runtime import a2a_composition
from maezo.runtime.worker_runtime.service import WorkerState, build_readiness_checks
from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport

_TENANT = "amh"
_SIGNING_KEY_ENV = per_tenant_key_env_var(_TENANT)
_VALID_KEY = "unit-test-card-signing-key-0123456789abcdef"

#: A marca exata do ramo degradado de cada grafo (o defeito CC-03/AND-03 as produzia SEMPRE).
_CAROLINA_GAP_NOTE = "leitor de resumo nao configurado"
_ANDRE_GAP_NOTE = "leitor FHIR nao configurado"


@pytest.fixture(autouse=True)
def _clean_signing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nunca depender de estado ambiente de chave/modo — cada teste declara o que precisa."""
    monkeypatch.delenv(_SIGNING_KEY_ENV, raising=False)
    monkeypatch.delenv(a2a_composition.ALLOW_UNSIGNED_CARDS_ENV_VAR, raising=False)
    monkeypatch.delenv(a2a_composition.WORKER_RUNTIME_MODE_ENV_VAR, raising=False)


class _FakeInference:
    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        # CC-12 x integracao lote3 (LOTE3-INTEGRATION-FAKES-TASK-KIND): assinatura acompanha o
        # Protocol real (`runtime/inference::InferenceProvider.generate`), mesma especie do
        # defeito f1bc87f.
        task_kind: str | None = None,
    ) -> str:
        return "dossie sintetico"


class _RecordingReader:
    """Duplo do seam de leitura FHIR — satisfaz `carolina.graph.SummaryReader` E
    `andre.graph.PatientSummaryReader`.

    Os dois Protocols deixaram de ter o mesmo membro no WP FHIR-TOOL-SURFACE-PARITY (NEW-04):
    Carolina le `read_patient_summary` e Andre le `read_patient`, cada um o id que o seu proprio
    `agent.yaml` declara. O `GatedFhirReader` real expoe as quatro operacoes do catalogo, entao
    um duplo que sirva aos dois lados precisa expor as duas — e e por isso que este falso as
    declara em vez de escolher uma."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.calls: list[str] = []

    async def read_patient(self, patient_id: str) -> dict[str, Any]:
        self.calls.append(patient_id)
        return {"resourceType": "Patient", "id": patient_id, "_label": self.label}

    async def read_patient_summary(self, patient_id: str) -> dict[str, Any]:
        return await self.read_patient(patient_id)


def _dossier_deps() -> dict[str, Any]:
    dmn = FakeDmnTransport()
    dmn.register("cred_admissibility", [{"roteamento": "SEGUE_ANALISE"}])
    dmn.register("cred_route", [{"roteamento": "ANALISE_DESCREDENCIAMENTO"}])
    dmn.register("cred_sla", [{"sla_analise": "P10D", "sla_alerta": "P7D"}])
    dmn.register("cred_prior_notice", [{"exige_notificacao_previa": True}])
    return {
        "dmn": dmn,
        "cibseven": FakeCibSevenTransport(),
        "audit_sink": FakeStartAuditSink(),
        "inference": _FakeInference(),
    }


def _spy_handler_factories(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, Any]]:
    """Captura os kwargs que a RAIZ passa aos dois handler factories, delegando ao factory REAL
    (o dispatcher continua sendo montado de verdade — nada aqui e um stub que 'parece funcionar')."""
    captured: dict[str, dict[str, Any]] = {}
    real_carolina = a2a_composition.make_carolina_handler
    real_andre = a2a_composition.make_andre_handler

    def _spy_carolina(inference: Any, **kwargs: Any) -> Any:
        captured["carolina"] = dict(kwargs)
        return real_carolina(inference, **kwargs)

    def _spy_andre(inference: Any, **kwargs: Any) -> Any:
        captured["andre"] = dict(kwargs)
        return real_andre(inference, **kwargs)

    monkeypatch.setattr(a2a_composition, "make_carolina_handler", _spy_carolina)
    monkeypatch.setattr(a2a_composition, "make_andre_handler", _spy_andre)
    return captured


# --- 1/2/5: a raiz A2A repassa o leitor por agente ---------------------------------------------


def test_dossier_root_injects_per_agent_fhir_reader_into_both_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CC-03 + AND-03: `fhir=` chega aos DOIS factories, cada agente com o SEU leitor, e
    `population=` chega a andre EXPLICITAMENTE (None hoje — BLOCKED(external WB.4))."""
    monkeypatch.setenv(_SIGNING_KEY_ENV, _VALID_KEY)
    captured = _spy_handler_factories(monkeypatch)
    carolina_reader = _RecordingReader("carolina")
    andre_reader = _RecordingReader("andre")

    a2a_composition.build_dossier_delegation_dispatcher(
        tenant=_TENANT,
        runtime_mode="local",
        kafka_producer=RecordingProducer(),
        fhir={"carolina": carolina_reader, "andre": andre_reader},
        population=None,
        **_dossier_deps(),
    )

    assert captured["carolina"]["fhir"] is carolina_reader
    assert captured["andre"]["fhir"] is andre_reader
    # `population` e um kwarg EXPLICITO (nao o default silencioso do factory): a raiz declara a
    # lacuna WB.4 em vez de omiti-la.
    assert "population" in captured["andre"]
    assert captured["andre"]["population"] is None


async def test_carolina_gather_no_longer_reports_a_missing_summary_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O objeto que a RAIZ entrega a carolina basta para silenciar o ramo degradado do grafo dela
    — e o resumo e realmente lido (nao-vacuidade)."""
    monkeypatch.setenv(_SIGNING_KEY_ENV, _VALID_KEY)
    captured = _spy_handler_factories(monkeypatch)
    carolina_reader = _RecordingReader("carolina")
    deps = _dossier_deps()

    a2a_composition.build_dossier_delegation_dispatcher(
        tenant=_TENANT,
        runtime_mode="local",
        kafka_producer=RecordingProducer(),
        fhir={"carolina": carolina_reader, "andre": _RecordingReader("andre")},
        **deps,
    )

    graph = CarolinaGraph(
        inference=deps["inference"],
        dmn=deps["dmn"],
        cibseven=deps["cibseven"],
        audit_sink=deps["audit_sink"],
        fhir=captured["carolina"].get("fhir"),
    )
    case: CarolinaState = {
        "tenant_id": _TENANT,
        "prestador_id": "P-CC03-1",
        "patient_summary_ref": "Patient/pseudo-1",
    }
    out = await graph.gather(case)

    assert not any(_CAROLINA_GAP_NOTE in note for note in out["gather_notes"]), (
        "o dossie de producao continua caindo no ramo degradado: a raiz nao injetou o leitor"
    )
    assert carolina_reader.calls == ["Patient/pseudo-1"]
    assert out["summary_facts"]["id"] == "Patient/pseudo-1"


async def test_andre_gather_no_longer_reports_a_missing_fhir_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SIMETRIA com carolina (o achado INFO-5 do verificador: so um dos dois lados do defeito
    AND-03 estava exercitado de ponta a ponta). O objeto que a RAIZ entrega a ANDRE basta para
    silenciar o ramo degradado do grafo dele — e o resumo e realmente lido (nao-vacuidade).

    O fluxo importa: a sonda FHIR de andre so existe em `pagto_dossier`, que e o default de
    `andre/graph.py::_flow` e exatamente o caminho da aresta de dossie de PAGAMENTO."""
    monkeypatch.setenv(_SIGNING_KEY_ENV, _VALID_KEY)
    captured = _spy_handler_factories(monkeypatch)
    andre_reader = _RecordingReader("andre")
    deps = _dossier_deps()

    a2a_composition.build_dossier_delegation_dispatcher(
        tenant=_TENANT,
        runtime_mode="local",
        kafka_producer=RecordingProducer(),
        fhir={"carolina": _RecordingReader("carolina"), "andre": andre_reader},
        **deps,
    )

    graph = AndreGraph(
        inference=deps["inference"],
        dmn=deps["dmn"],
        cibseven=deps["cibseven"],
        audit_sink=deps["audit_sink"],
        fhir=captured["andre"].get("fhir"),
        population=captured["andre"].get("population"),
    )
    case: AndreState = {
        "tenant_id": _TENANT,
        "flow": "pagto_dossier",
        "ordem_pagamento_id": "OP-CC03-1",
        "patient_summary_ref": "Patient/pseudo-2",
    }
    out = await graph.gather(case)

    assert not any(_ANDRE_GAP_NOTE in note for note in out["gather_notes"]), (
        "o dossie de pagamento continua caindo no ramo degradado: a raiz nao injetou o leitor"
    )
    assert andre_reader.calls == ["Patient/pseudo-2"]
    # A lacuna WB.4 (cliente de populacao) continua DECLARADA e nao se confunde com a do FHIR:
    # sem `cohort_id` andre nem chega a consultar populacao, entao a unica nota possivel aqui
    # seria a do FHIR — que e justamente a que nao deve mais existir.
    assert out["gather_notes"] == []


def test_dossier_root_fails_closed_on_an_unknown_agent_in_the_fhir_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Um typo na chave degradaria o dossie em SILENCIO (o defeito CC-03 em miniatura). A raiz
    recusa a compor em vez de aceitar um mapa que nao alcanca ninguem."""
    monkeypatch.setenv(_SIGNING_KEY_ENV, _VALID_KEY)
    with pytest.raises(ValueError, match="carolinaa"):
        a2a_composition.build_dossier_delegation_dispatcher(
            tenant=_TENANT,
            runtime_mode="local",
            kafka_producer=RecordingProducer(),
            fhir={"carolinaa": _RecordingReader("typo")},
            **_dossier_deps(),
        )


def test_andre_gap_note_marker_is_the_one_the_graph_emits() -> None:
    """Nao-vacuidade da marca usada acima: o texto vem do grafo real de andre, nao deste teste."""
    from maezo.agents.andre import graph as andre_graph

    source = andre_graph.__file__
    assert source is not None
    with open(source, encoding="utf-8") as handle:
        assert _ANDRE_GAP_NOTE in handle.read()


# --- 4: o daemon worker constroi os seams pelo construtor sancionado ---------------------------


class _SpyHarness:
    """Stand-in minimo de `WorkerHarness` (mesma forma do usado em test_worker_runtime_service)."""

    def __init__(self, transport: Any, *, worker_id: str, audit_sink: Any = None, **kwargs: Any) -> None:
        self.transport = transport
        self.worker_id = worker_id
        self.audit_sink = audit_sink
        self._topics: list[str] = []

    def register_worker(self, worker: Any) -> None:
        self._topics.append(worker.topic)

    def register(self, topic: str, handler: Any, *, variables: Any = None) -> None:
        self._topics.append(topic)

    @property
    def registered_topics(self) -> list[str]:
        return sorted(self._topics)

    async def run(self) -> None:
        await asyncio.Event().wait()


_DSN = "postgresql://maezo@localhost:5432/maezo"


async def _bring_up_worker(
    monkeypatch: pytest.MonkeyPatch, settings: WorkerRuntimeSettings
) -> tuple[WorkerState, dict[str, Any]]:
    """Roda o STEP B REAL do daemon com as unicas bordas que exigem processo externo trocadas
    (harness, registro de workers, sonda do sink) e com a RAIZ do dossie espionada. Devolve o
    `WorkerState` resultante e os kwargs que a raiz recebeu."""
    import maezo.runtime.worker_runtime.service as svc

    captured: dict[str, Any] = {}

    def _spy_build_dossier(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return object()  # sentinela: estes testes provam a MONTAGEM, nao o roteamento

    async def _probe_ok(_sink: Any, _timeout: float) -> bool:
        return True

    monkeypatch.setattr(a2a_composition, "build_dossier_delegation_dispatcher", _spy_build_dossier)
    monkeypatch.setattr(svc, "WorkerHarness", _SpyHarness)
    monkeypatch.setattr(svc, "register_default_workers", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_expected_worker_topics", lambda: frozenset({"t"}))
    monkeypatch.setattr(svc, "_probe_audit_sink", _probe_ok)

    state = WorkerState(settings=settings)
    await svc._bring_up_dependencies(state)
    return state, captured


async def _teardown_worker_state(state: WorkerState) -> None:
    if state.harness_task is not None:
        state.harness_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await state.harness_task
    if state.transport is not None:
        await state.transport.close()
    if state.audit_sink is not None:
        await state.audit_sink.aclose()


async def test_worker_step_b_builds_gated_per_agent_fhir_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    """STEP B constroi o(s) seam(s) FHIR pelo construtor SANCIONADO e os repassa a raiz do
    dossie: wrapper GATEADO (nao o adaptador cru), principal CORRETO por agente, `population`
    explicitamente None (BLOCKED(external WB.4))."""
    from maezo.agents.rafael.adapters import FhirServerReader as PatientShim
    from maezo.agents.valentina.adapters import FhirServerReader as SummaryShim
    from maezo.gateway.tool_registry import _FHIR_ADAPTER_BY_AGENT

    state, captured = await _bring_up_worker(monkeypatch, WorkerRuntimeSettings(DATABASE_URL=_DSN))
    try:
        assert set(captured["fhir"]) == {"carolina", "andre"}
        for agent_id in ("carolina", "andre"):
            seam = captured["fhir"][agent_id]
            # GATEADO, nao o adaptador cru: `leitura_phi_clinica` (C2) precisa passar pelo PEP.
            assert isinstance(seam, GatedFhirReader)
            assert not isinstance(seam, PatientShim | SummaryShim)
            assert isinstance(seam.inner, PatientShim | SummaryShim)
            # O shim embrulhado tem de implementar a operacao que o mapa sancionado escolheu para
            # ESTE agente. Os dois alvos do dossie NAO compartilham mais a mesma operacao desde o
            # WP FHIR-TOOL-SURFACE-PARITY (NEW-04): carolina le `read_patient_summary` (o id que o
            # `agent.yaml` dela declara) e andre le `read_patient` — ids de tool distintos no
            # catalogo, decididos separadamente pelo L1 do PEP. Asserir a CLASSE do shim aqui
            # amarraria o teste a escolha de reuso (ADR-0004), nao a garantia; asserir a OPERACAO
            # amarra o que importa.
            operacao = _FHIR_ADAPTER_BY_AGENT[agent_id]
            assert hasattr(seam.inner, operacao), (
                f"{agent_id}: o shim embrulhado nao implementa {operacao!r} — o mapa de "
                "adaptadores e o grafo divergiram"
            )
            # O principal e POR AGENTE: a decisao do PEP e por principal, entao um leitor
            # compartilhado atribuiria a leitura PHI de andre a capacidade de carolina.
            assert seam.seam_context.principal == agent_id
        assert captured["population"] is None
        # O daemon tambem passa a enxergar o seam FHIR na sua propria asserção de boot (I-11).
        assert state.effect_seams_gated is True
    finally:
        await _teardown_worker_state(state)


# --- F1 (REVISE): a degradacao do leitor FHIR e OBSERVAVEL no readiness ------------------------


async def test_readyz_publishes_dossier_fhir_ready_naming_both_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`WorkerState.dossier_fhir_detail` era WRITE-ONLY: escrito no STEP B, lido por NINGUEM.
    O check nao-fatal `dossier_fhir_ready` (espelho de `dossier_delegation_ready`) e o unico
    lugar onde o operador ve QUAIS agentes de dossie tem leitor gateado."""
    state, _ = await _bring_up_worker(monkeypatch, WorkerRuntimeSettings(DATABASE_URL=_DSN))
    try:
        checks = {c.__name__: c for c in build_readiness_checks(state)}
        assert "dossier_fhir_ready" in checks, (
            "o detalhe do seam FHIR continua write-only: nada em /readyz o le"
        )
        result = await checks["dossier_fhir_ready"]()
        assert result.name == "dossier_fhir_ready"
        assert result.healthy is True
        assert result.detail is not None
        assert "carolina" in result.detail and "andre" in result.detail
    finally:
        await _teardown_worker_state(state)


async def test_readyz_discloses_the_fhir_degradation_when_base_url_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`FHIR_BASE_URL=""` = "este deploy nao tem FHIR": NENHUM leitor, os dois grafos emitem a
    nota de lacuna declarada — e isso precisa APARECER. Degradacao, NUNCA falha de readiness:
    o dossie "instrui, nao decide", entao um leitor ausente nao pode tirar da rotacao um daemon
    que serve ~110 topicos."""
    settings = WorkerRuntimeSettings(DATABASE_URL=_DSN, FHIR_BASE_URL="")
    state, captured = await _bring_up_worker(monkeypatch, settings)
    try:
        assert state.dossier_fhir_seams == {}
        assert captured["fhir"] is None
        assert state.dossier_fhir_detail.startswith("DEGRADED")
        # A RAZAO de o check existir: a assercao de boot I-11 fica VERDE mesmo assim (nada
        # ungated existe quando nada foi construido), entao sem `dossier_fhir_ready` o painel
        # do operador nao teria UM sinal do dossie degradado.
        assert state.effect_seams_gated is True

        checks = {c.__name__: c for c in build_readiness_checks(state)}
        assert "dossier_fhir_ready" in checks
        result = await checks["dossier_fhir_ready"]()
        assert result.healthy is True, "degradacao do dossie NUNCA derruba /readyz do daemon"
        assert result.detail is not None
        assert result.detail.startswith("DEGRADED")
        assert "carolina" in result.detail and "andre" in result.detail
    finally:
        await _teardown_worker_state(state)


# --- INFO-3: a recusa da raiz so cobre UMA direcao --------------------------------------------


def test_worker_fhir_agent_ids_match_the_a2a_root_edge_agents() -> None:
    """`service._DOSSIER_FHIR_AGENT_IDS` e um literal LOCAL (o modulo A2A e importado tarde, de
    proposito). A raiz recusa uma chave que ela NAO serve — mas a direcao oposta (uma aresta de
    dossie servida pela raiz que este literal esquece) nao levanta nada: aquele agente
    simplesmente ficaria sem leitor, em silencio, que e o proprio defeito CC-03/AND-03.
    Este teste e a guarda dessa direcao, no unico lugar onde ela e barata e nao pode ser
    apagada por `python -O` nem virar uma queda de dossie em bring-up.

    O par correto e' `_DOSSIER_EDGE_FHIR_AGENT_IDS` (o SUBCONJUNTO da aresta cujos grafos
    declaram leitor FHIR), nao `_DOSSIER_EDGE_AGENT_IDS`: desde R-081 a aresta serve tambem
    `fernando`, cujo `build(config)` nao tem chave `fhir` alguma — igualar os dois literais faria
    a raiz ACEITAR `fhir={"fernando": leitor}` e engolir em silencio um leitor que nada consome.
    """
    from maezo.runtime.agent_runtime.a2a_composition import (
        _DOSSIER_EDGE_AGENT_IDS,
        _DOSSIER_EDGE_FHIR_AGENT_IDS,
    )
    from maezo.runtime.worker_runtime.service import _DOSSIER_FHIR_AGENT_IDS

    assert set(_DOSSIER_FHIR_AGENT_IDS) == set(_DOSSIER_EDGE_FHIR_AGENT_IDS)
    assert set(_DOSSIER_EDGE_FHIR_AGENT_IDS) <= set(_DOSSIER_EDGE_AGENT_IDS)


def test_the_fhir_subset_is_exactly_the_edge_agents_whose_handler_takes_a_reader() -> None:
    """A terceira direcao, a que ficou aberta quando o subconjunto FHIR se separou da aresta.

    `_DOSSIER_EDGE_FHIR_AGENT_IDS` passa a ser um literal curado a mao; um agente da aresta cujo
    `make_<id>_handler` ACEITA `fhir=` mas que fique de fora dele nao levanta nada — a raiz
    simplesmente nunca lhe passaria leitor, e todo dossie desse agente cairia no ramo degradado
    em silencio. E' o defeito CC-03/AND-03 na sua forma original. Reapurado aqui pela ASSINATURA
    real de cada fabrica, nunca por uma segunda lista.
    """
    import importlib
    import inspect

    from maezo.runtime.agent_runtime.a2a_composition import (
        _DOSSIER_EDGE_AGENT_IDS,
        _DOSSIER_EDGE_FHIR_AGENT_IDS,
    )

    def _fabrica(agent_id: str) -> Any:
        modulo = importlib.import_module(f"maezo.agents.{agent_id}.delegation")
        return getattr(modulo, f"make_{agent_id}_handler")

    aceitam_fhir = {
        agent_id
        for agent_id in _DOSSIER_EDGE_AGENT_IDS
        if "fhir" in inspect.signature(_fabrica(agent_id)).parameters
    }
    assert aceitam_fhir == set(_DOSSIER_EDGE_FHIR_AGENT_IDS), (
        f"as fabricas de handler da aresta de dossie que aceitam `fhir=` sao {sorted(aceitam_fhir)}, "
        f"mas `_DOSSIER_EDGE_FHIR_AGENT_IDS` diz {sorted(_DOSSIER_EDGE_FHIR_AGENT_IDS)} — um "
        "agente esquecido aqui recebe `None` e degrada todo dossie em silencio (CC-03/AND-03)"
    )
