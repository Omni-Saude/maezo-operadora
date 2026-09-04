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
  5. `population` continua `None` explicito — BLOCKED(external WB.4): nao existe cliente de lago
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
from maezo.agents.carolina.graph import CarolinaGraph
from maezo.gateway.seams.fhir import GatedFhirReader
from maezo.runtime.agent_runtime import a2a_composition
from maezo.runtime.worker_runtime.service import WorkerState
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
    async def generate(self, prompt: str, *, phi: bool = False) -> str:
        return "dossie sintetico"


class _RecordingReader:
    """Duplo do seam de leitura FHIR — satisfaz `SummaryReader`/`PatientSummaryReader` (os dois
    Protocols declaram EXATAMENTE `async read_patient(patient_id) -> dict`)."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.calls: list[str] = []

    async def read_patient(self, patient_id: str) -> dict[str, Any]:
        self.calls.append(patient_id)
        return {"resourceType": "Patient", "id": patient_id, "_label": self.label}


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
    out = await graph.gather(
        {
            "tenant_id": _TENANT,
            "prestador_id": "P-CC03-1",
            "patient_summary_ref": "Patient/pseudo-1",
        }  # type: ignore[arg-type]
    )

    assert not any(_CAROLINA_GAP_NOTE in note for note in out["gather_notes"]), (
        "o dossie de producao continua caindo no ramo degradado: a raiz nao injetou o leitor"
    )
    assert carolina_reader.calls == ["Patient/pseudo-1"]
    assert out["summary_facts"]["id"] == "Patient/pseudo-1"


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


async def test_worker_step_b_builds_gated_per_agent_fhir_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    """STEP B constroi o(s) seam(s) FHIR pelo construtor SANCIONADO e os repassa a raiz do
    dossie: wrapper GATEADO (nao o adaptador cru), principal CORRETO por agente, `population`
    explicitamente None (BLOCKED(external WB.4))."""
    import maezo.runtime.worker_runtime.service as svc
    from maezo.agents.rafael.adapters import FhirServerReader

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

    settings = WorkerRuntimeSettings(DATABASE_URL="postgresql://maezo@localhost:5432/maezo")
    state = WorkerState(settings=settings)
    try:
        await svc._bring_up_dependencies(state)

        assert set(captured["fhir"]) == {"carolina", "andre"}
        for agent_id in ("carolina", "andre"):
            seam = captured["fhir"][agent_id]
            # GATEADO, nao o adaptador cru: `leitura_phi_clinica` (C2) precisa passar pelo PEP.
            assert isinstance(seam, GatedFhirReader)
            assert not isinstance(seam, FhirServerReader)
            assert isinstance(seam.inner, FhirServerReader)
            # O principal e POR AGENTE: a decisao do PEP e por principal, entao um leitor
            # compartilhado atribuiria a leitura PHI de andre a capacidade de carolina.
            assert seam.seam_context.principal == agent_id
        assert captured["population"] is None
        # O daemon tambem passa a enxergar o seam FHIR na sua propria asserção de boot (I-11).
        assert state.effect_seams_gated is True
    finally:
        if state.harness_task is not None:
            state.harness_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await state.harness_task
        if state.transport is not None:
            await state.transport.close()
        if state.audit_sink is not None:
            await state.audit_sink.aclose()
