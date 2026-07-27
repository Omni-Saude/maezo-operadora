"""Unit tests for Marina's REAL graph (T1.12 — SP-OP-CONTAS-001/RECURSO-001/REEMBOLSO-001).

Every node is exercised against fakes: `FakeDmnTransport` (never the local XML evaluator, ADR-
0028), `FakeCibSevenTransport` (never a fabricated process instance), and a small in-file fake
inference provider (no LLM SDK, no network). Live-engine acceptance status is reported in the PR
body (ADR-0011: real engine, never mocked there; never fabricated here).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.agents.marina.graph import (
    AdmissibilidadeRecurso,
    ElegibilidadeRecurso,
    MarinaGraph,
    MarinaState,
    Route,
    TriagemGlosa,
    _business_key,
    build,
)
from maezo.tools.mcp_cibseven.transport import CibSevenError, FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import DmnEvaluationError, FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_AGENTS_ROOT = Path(__file__).parent.parent.parent.parent / "spec" / "agents"


class _FakeInference:
    """Deterministic, in-order fake — never a real LLM SDK call."""

    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses) if responses else ["dossie factual sintetico"]
        self.calls: list[tuple[str, bool]] = []

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        self.calls.append((prompt, phi))
        return self._responses.pop(0) if self._responses else ""


class _FakePatientSummaryReader:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.calls: list[str] = []

    async def read_patient_summary(self, patient_id: str) -> dict[str, Any]:
        self.calls.append(patient_id)
        if self._fail:
            raise RuntimeError("HAPI FHIR unreachable")
        return {"resourceType": "Patient", "id": patient_id}


def _graph(
    *,
    inference: Any | None = None,
    dmn: FakeDmnTransport | None = None,
    cibseven: FakeCibSevenTransport | None = None,
    audit_sink: Any | None = None,
    fhir: Any | None = None,
) -> MarinaGraph:
    return MarinaGraph(
        inference=inference or _FakeInference(),
        dmn=dmn or FakeDmnTransport(),
        cibseven=cibseven or FakeCibSevenTransport(),
        audit_sink=audit_sink or FakeStartAuditSink(),
        fhir=fhir,
    )


def _contas_state(**overrides: Any) -> MarinaState:
    state: MarinaState = {
        "flow": "contas",
        "tenant_id": "amh",
        "canal": "a2a",
        "beneficiario_pseudo_id": "pseudo-123",
        "prestador_id": "prestador-1",
        "numero_lote_tiss": "LOTE-001",
        "numero_guia_tiss": "GUIA-001",
        "competencia": "2026-06",
        "valor_apresentado_brl": 1500.0,
        "tipo_lote": "sadt",
        "linhas_conta_refs": [{"ref": "linha-1"}],
        "reason_codes_tiss": ["VALOR_ACIMA_TABELA"],
        "divergencia_valor": True,
        "item_conforme_tabela": True,
        "documentacao_anexa": True,
        "denial_ratio": 0.3,
        "indicio_fraude_sinalizado": False,
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _recurso_state(**overrides: Any) -> MarinaState:
    state: MarinaState = {
        "flow": "recurso",
        "tenant_id": "amh",
        "canal": "a2a",
        "beneficiario_pseudo_id": "pseudo-123",
        "prestador_id": "prestador-1",
        "numero_guia_tiss": "GUIA-001",
        "glosa_id": "GLOSA-001",
        "numero_lote_tiss": "LOTE-001",
        "glosa_type": "administrativa",
        "glosa_reason_code": "VALOR_ACIMA_TABELA",
        "valor_glosado_brl": 800.0,
        "codigo_procedimento_tuss": "10101012",
        "documentos_recurso_refs": [],
        "data_ciencia_glosa": "2026-06-01",
        "glosa_existe": True,
        "dentro_prazo_recurso": True,
        "documentacao_recurso_completa": True,
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _reembolso_state(**overrides: Any) -> MarinaState:
    state: MarinaState = {
        "flow": "reembolso",
        "tenant_id": "amh",
        "canal": "a2a",
        "beneficiario_pseudo_id": "pseudo-123",
        "protocolo_reembolso": "REEMB-PROTO-001",
        "numero_guia_tiss": "GUIA-002",
        "tipo_reembolso": "livre_escolha",
        "categoria_procedimento": "consulta",
        "codigo_procedimento_tuss": "10101012",
        "valor_solicitado_cents": 30000,
        "valor_calculado_tabela_cents": 35000,
        "cobertura_prevista": True,
        "dentro_prazo": True,
        "dentro_tabela": True,
        "dentro_teto_l2": True,
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _register_normalization(dmn: FakeDmnTransport, categoria: str = "valor") -> None:
    dmn.register("glosa_reason_normalization", [{"categoria_normalizada": categoria, "descricao": "test"}])


def _register_classification(dmn: FakeDmnTransport, glosa_type: str = "valor") -> None:
    dmn.register("glosa_classification", [{"glosa_type": glosa_type, "glosa_extent": "linha"}])


def _register_contas_sla(dmn: FakeDmnTransport) -> None:
    dmn.register("contas_sla", [{"sla_analise": "P30D", "sla_alerta": "P20D", "fonte_regulatoria": "RN 424"}])


def _register_triage(dmn: FakeDmnTransport, roteamento: str = "RECORRER") -> None:
    dmn.register("glosa_triage", [{"roteamento": roteamento, "motivo": "test"}])


def _register_admissibility(dmn: FakeDmnTransport, roteamento: str = "SEGUE_ANALISE") -> None:
    dmn.register("recurso_admissibility", [{"roteamento": roteamento, "motivo": "test"}])


def _register_recurso_sla(dmn: FakeDmnTransport) -> None:
    dmn.register("recurso_sla", [{"sla_analise": "P10D", "sla_alerta": "P6D", "prazo_regulatorio": "P30D"}])


def _register_eligibility(
    dmn: FakeDmnTransport, roteamento: str = "RECORRIVEL", grupo_revisor: str = "analista-recurso-glosa"
) -> None:
    dmn.register(
        "recurso_eligibility", [{"roteamento": roteamento, "grupo_revisor": grupo_revisor, "motivo": "test"}]
    )


def _register_contas_dmn(
    dmn: FakeDmnTransport, *, categoria: str = "valor", glosa_type: str = "valor", triagem: str = "RECORRER"
) -> None:
    _register_normalization(dmn, categoria)
    _register_classification(dmn, glosa_type)
    _register_contas_sla(dmn)
    _register_triage(dmn, triagem)


def _register_recurso_dmn(
    dmn: FakeDmnTransport,
    *,
    admissibilidade: str = "SEGUE_ANALISE",
    elegibilidade: str = "RECORRIVEL",
    grupo_revisor: str = "analista-recurso-glosa",
) -> None:
    _register_admissibility(dmn, admissibilidade)
    _register_recurso_sla(dmn)
    _register_eligibility(dmn, elegibilidade, grupo_revisor)


# ---------------------------------------------------------------------------
# spec/agent.yaml sanity
# ---------------------------------------------------------------------------


def test_marina_agent_yaml_exists() -> None:
    agent_path = _AGENTS_ROOT / "marina" / "agent.yaml"
    assert agent_path.exists()


def test_marina_agent_yaml_has_required_fields() -> None:
    agent_path = _AGENTS_ROOT / "marina" / "agent.yaml"
    with open(agent_path) as f:
        data = yaml.safe_load(f)
    assert data["id"] == "marina"
    assert data["name"] == "Marina Andrade"
    assert data.get("phase") == 2
    assert "tools" in data


def test_marina_definition_loads() -> None:
    from maezo.agents import AgentLoader

    definition = AgentLoader().load(_AGENTS_ROOT / "marina" / "agent.yaml")
    assert definition.id == "marina"
    assert definition.phase == 2


# ---------------------------------------------------------------------------
# build(config) — fail-closed contract; fhir is OPTIONAL
# ---------------------------------------------------------------------------


def test_build_requires_inference_dmn_cibseven() -> None:
    with pytest.raises(ValueError, match="missing required dependencies"):
        build({})


def test_build_without_fhir_still_compiles() -> None:
    graph = build(
        {
            "inference": _FakeInference(),
            "dmn": FakeDmnTransport(),
            "cibseven": FakeCibSevenTransport(),
            "audit_sink": FakeStartAuditSink(),
        }
    )
    compiled = graph.compile()
    node_names = {n for n in compiled.get_graph().nodes if n not in ("__start__", "__end__")}
    assert {
        "receive",
        "gather",
        "assess",
        "auto_route",
        "human_review",
        "start_process",
        "finalize",
    } <= node_names


# ---------------------------------------------------------------------------
# receive / business key — per flow
# ---------------------------------------------------------------------------


def test_business_key_contas_by_lote() -> None:
    state = _contas_state(numero_guia_tiss="", numero_conta="")
    assert _business_key(state) == "CONTAS-amh-LOTE-001"


def test_business_key_contas_by_guia_conta() -> None:
    state = _contas_state(numero_guia_tiss="GUIA-42", numero_conta="CONTA-7")
    assert _business_key(state) == "CONTAS-amh-GUIA-42-CONTA-7"


def test_business_key_recurso() -> None:
    state = _recurso_state()
    assert _business_key(state) == "RECURSO-amh-GUIA-001-GLOSA-001"


def test_business_key_reembolso() -> None:
    state = _reembolso_state()
    assert _business_key(state) == "REEMB-amh-REEMB-PROTO-001"


async def test_receive_assigns_business_key_contas() -> None:
    graph = _graph()
    result = await graph.receive(_contas_state())
    assert result["business_key"] == "CONTAS-amh-LOTE-001"


async def test_receive_missing_tenant_routes_human_never_blocks() -> None:
    graph = _graph()
    result = await graph.receive(_contas_state(tenant_id=""))
    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "outro"
    assert "error" in result


async def test_receive_recurso_missing_glosa_id_routes_human() -> None:
    graph = _graph()
    result = await graph.receive(_recurso_state(glosa_id=""))
    assert result["route"] == "human_review"
    assert result["grupo_humano"] == "analista-recurso-glosa"


async def test_receive_reembolso_missing_protocolo_routes_human() -> None:
    graph = _graph()
    result = await graph.receive(_reembolso_state(protocolo_reembolso=""))
    assert result["route"] == "human_review"
    assert result["grupo_humano"] == "analise-reembolso"


# ---------------------------------------------------------------------------
# gather — best-effort FHIR, never blocks routing
# ---------------------------------------------------------------------------


async def test_gather_without_fhir_reader_notes_the_gap() -> None:
    graph = _graph(fhir=None)
    result = await graph.gather(_contas_state())
    assert result["gathered"] is True
    assert result["gather_notes"]


async def test_gather_with_fhir_reader_populates_facts() -> None:
    fhir = _FakePatientSummaryReader()
    graph = _graph(fhir=fhir)
    result = await graph.gather(_contas_state(patient_summary_ref="patient-1"))
    assert result["gathered"] is True
    assert result["gather_notes"] == []
    assert fhir.calls == ["patient-1"]


async def test_gather_fhir_failure_is_best_effort_never_raises() -> None:
    fhir = _FakePatientSummaryReader(fail=True)
    graph = _graph(fhir=fhir)
    result = await graph.gather(_contas_state(patient_summary_ref="patient-1"))
    assert result["gathered"] is True
    assert any("indisponivel" in note for note in result["gather_notes"])


async def test_gather_bails_fail_safe_when_already_errored() -> None:
    """R1 cycle-1: the error bail re-asserts the human route instead of returning `{}` (defense
    in depth — post-sanitization it is only reachable via `receive`'s own missing-context guard,
    but the bail itself must never be reachable with a non-human route)."""
    fhir = _FakePatientSummaryReader()
    graph = _graph(fhir=fhir)
    result = await graph.gather(_contas_state(error="contexto ausente"))
    assert result == {"route": "human_review"}
    assert fhir.calls == []


# ---------------------------------------------------------------------------
# assess — CONTAS: normalization -> classification -> triage (+ SLA informative)
# ---------------------------------------------------------------------------


async def test_assess_contas_sem_glosa_auto_routes() -> None:
    dmn = FakeDmnTransport()
    _register_contas_dmn(dmn, categoria="administrativa", triagem="SEM_GLOSA")
    graph = _graph(dmn=dmn)

    result = await graph.assess(_contas_state())

    assert result["route"] == "auto_route"
    assert result["desfecho"] == "sem_glosa"
    assert result["triagem"] == "SEM_GLOSA"


async def test_assess_contas_recorrer_auto_routes() -> None:
    dmn = FakeDmnTransport()
    _register_contas_dmn(dmn, triagem="RECORRER")
    graph = _graph(dmn=dmn)

    result = await graph.assess(_contas_state())

    assert result["route"] == "auto_route"
    assert result["desfecho"] == "encaminhada_recurso_triagem"


async def test_assess_contas_analise_humana_routes_human() -> None:
    dmn = FakeDmnTransport()
    _register_contas_dmn(dmn, categoria="tecnica", triagem="ANALISE_HUMANA")
    graph = _graph(dmn=dmn)

    result = await graph.assess(_contas_state())

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "triagem_analise_humana"
    assert result["grupo_humano"] == "auditoria-contas"


async def test_assess_contas_fraud_signal_routes_human_before_normalization() -> None:
    """Fraud signal is INFORMATIVE ONLY — never self-flags, always routes human, takes
    precedence over the routing DMN chain (L0 guard: never a fraud accusation)."""
    dmn = FakeDmnTransport()
    _register_contas_sla(dmn)
    graph = _graph(dmn=dmn)

    result = await graph.assess(_contas_state(indicio_fraude_sinalizado=True))

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "indicio_fraude"
    assert result["grupo_humano"] == "auditoria-contas"
    assert not any(table == "glosa_reason_normalization" for table, _ in dmn.calls)


async def test_assess_contas_normalization_dmn_unavailable_routes_human() -> None:
    """L0 guard / helena-class probe: assess-failure fail-closed. A DMN in the assess chain
    being unavailable NEVER falls through to auto_route — always human_review."""
    dmn = FakeDmnTransport()  # nothing registered -> DmnEvaluationError
    graph = _graph(dmn=dmn)

    result = await graph.assess(_contas_state())

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "dmn_indisponivel"
    assert "dmn_error" in result


async def test_assess_contas_classification_dmn_unavailable_routes_human() -> None:
    """Disclosed divergence from the donor (module docstring): classification failure now
    fail-closes to human_review rather than silently proceeding with glosa_type=''."""
    dmn = FakeDmnTransport()
    _register_normalization(dmn)
    # glosa_classification deliberately NOT registered.
    graph = _graph(dmn=dmn)

    result = await graph.assess(_contas_state())

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "dmn_indisponivel"
    assert not any(table == "glosa_triage" for table, _ in dmn.calls)


async def test_assess_contas_triage_dmn_unavailable_routes_human() -> None:
    dmn = FakeDmnTransport()
    _register_normalization(dmn)
    _register_classification(dmn)
    _register_contas_sla(dmn)
    # glosa_triage deliberately NOT registered.
    graph = _graph(dmn=dmn)

    result = await graph.assess(_contas_state())

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "dmn_indisponivel"


async def test_assess_contas_sla_dmn_failure_never_gates_routing() -> None:
    """`contas_sla` is PURELY INFORMATIVE — mirrors Rafael's `auth_sla` exactly. Its own failure
    never blocks a neutral auto_route."""
    dmn = FakeDmnTransport()
    _register_normalization(dmn, categoria="administrativa")
    _register_classification(dmn)
    _register_triage(dmn, "SEM_GLOSA")
    # contas_sla deliberately NOT registered.
    graph = _graph(dmn=dmn)

    result = await graph.assess(_contas_state())

    assert result["route"] == "auto_route"
    assert result["sla_analise"] == ""


async def test_assess_contas_unexpected_triagem_value_routes_human() -> None:
    """Fail-safe closed allowlist: ONLY SEM_GLOSA/RECORRER auto-route. Any other/unexpected
    value (even a plausible-looking but non-allow-listed one) falls to human by omission."""
    dmn = FakeDmnTransport()
    _register_contas_dmn(dmn, triagem="ACEITAR")  # not a real DMN output — simulates corruption
    graph = _graph(dmn=dmn)

    result = await graph.assess(_contas_state())

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "triagem_analise_humana"


# ---------------------------------------------------------------------------
# assess — RECURSO: admissibility -> eligibility (+ SLA informative)
# ---------------------------------------------------------------------------


async def test_assess_recurso_recorrivel_analista_auto_routes() -> None:
    dmn = FakeDmnTransport()
    _register_recurso_dmn(dmn, elegibilidade="RECORRIVEL", grupo_revisor="analista-recurso-glosa")
    graph = _graph(dmn=dmn)

    result = await graph.assess(_recurso_state())

    assert result["route"] == "auto_route"
    assert result["desfecho"] == "recurso_segue_analise"


async def test_assess_recurso_tecnica_clinica_always_routes_medico_auditor() -> None:
    """Glosa tecnica/clinica always routes to medico-auditor merit review — even when the
    eligibility DMN says RECORRIVEL, `grupo_revisor=medico-auditor` overrides auto_route."""
    dmn = FakeDmnTransport()
    _register_recurso_dmn(dmn, elegibilidade="RECORRIVEL", grupo_revisor="medico-auditor")
    graph = _graph(dmn=dmn)

    result = await graph.assess(_recurso_state(glosa_type="tecnica"))

    assert result["route"] == "human_review"
    assert result["grupo_humano"] == "medico-auditor"
    assert result["motivo_humano"] == "recurso_analise_humana"


async def test_assess_recurso_pendente_documentacao_routes_human_never_calls_eligibility() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "PENDENTE_DOCUMENTACAO")
    _register_recurso_sla(dmn)
    graph = _graph(dmn=dmn)

    result = await graph.assess(_recurso_state(documentacao_recurso_completa=False))

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "documentacao_pendente"
    assert not any(table == "recurso_eligibility" for table, _ in dmn.calls)


async def test_assess_recurso_admissibility_dmn_unavailable_routes_human() -> None:
    dmn = FakeDmnTransport()  # nothing registered
    graph = _graph(dmn=dmn)

    result = await graph.assess(_recurso_state())

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "dmn_indisponivel"


async def test_assess_recurso_eligibility_dmn_unavailable_routes_human() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "SEGUE_ANALISE")
    _register_recurso_sla(dmn)
    # recurso_eligibility deliberately NOT registered.
    graph = _graph(dmn=dmn)

    result = await graph.assess(_recurso_state())

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "dmn_indisponivel"


async def test_assess_recurso_unexpected_admissibilidade_value_routes_human() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "BOGUS_VALUE")
    _register_recurso_sla(dmn)
    graph = _graph(dmn=dmn)

    result = await graph.assess(_recurso_state())

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "recurso_analise_humana"


async def test_assess_recurso_unexpected_elegibilidade_value_routes_human() -> None:
    dmn = FakeDmnTransport()
    _register_recurso_dmn(dmn, elegibilidade="BOGUS_VALUE", grupo_revisor="analista-recurso-glosa")
    graph = _graph(dmn=dmn)

    result = await graph.assess(_recurso_state())

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "recurso_analise_humana"


def test_route_fail_safe_defaults_to_human_on_missing_route() -> None:
    assert MarinaGraph._route({}) == "human_review"
    assert MarinaGraph._route({"route": "auto_route"}) == "auto_route"


# ---------------------------------------------------------------------------
# assess — REEMBOLSO: no DMN re-evaluated, always human (no auto_route variant)
# ---------------------------------------------------------------------------


async def test_assess_reembolso_always_routes_human_no_dmn_calls() -> None:
    dmn = FakeDmnTransport()
    graph = _graph(dmn=dmn)

    result = await graph.assess(_reembolso_state())

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "reembolso_dossie"
    assert result["grupo_humano"] == "analise-reembolso"
    assert result["desfecho"] == "reembolso_dossie_humano"
    assert dmn.calls == []


async def test_assess_reembolso_dentro_teto_l2_is_pure_passthrough() -> None:
    """L0 guard: `dentro_teto_l2` never originates outside CeilingResolver
    (`tests/unit/sec/test_dentro_teto_source.py`). Marina's reembolso flow only REPORTS the
    pre-resolved fact — never computes/derives it, and evaluates no DMN at all."""
    dmn = FakeDmnTransport()
    graph = _graph(dmn=dmn)

    result_true = await graph.assess(_reembolso_state(dentro_teto_l2=True))
    result_false = await graph.assess(_reembolso_state(dentro_teto_l2=False))

    assert dmn.calls == []
    # Both routes are human_review regardless of the ceiling fact's value (structurally proves
    # the fact never drives an automatic outcome in this flow).
    assert result_true["route"] == "human_review"
    assert result_false["route"] == "human_review"

    facts_true = graph._reembolso_facts(_reembolso_state(dentro_teto_l2=True))
    facts_false = graph._reembolso_facts(_reembolso_state(dentro_teto_l2=False))
    assert facts_true["dentro_teto_l2"] is True
    assert facts_false["dentro_teto_l2"] is False


# ---------------------------------------------------------------------------
# auto_route / human_review — dossier structural guardrail (L0 hard)
# ---------------------------------------------------------------------------


async def test_auto_route_dossier_never_carries_an_adverse_decision() -> None:
    graph = _graph()
    result = await graph.auto_route(_contas_state(route="auto_route", triagem="RECORRER"))
    assert result["dossier"]["decisao_glosa"] is None
    assert result["dossier"]["decisao_recurso"] is None
    assert result["dossier"]["decisao_reembolso"] is None
    assert result["dossier"]["route"] == "auto_route"


async def test_human_review_dossier_carries_motivo_never_a_decision_contas() -> None:
    graph = _graph()
    result = await graph.human_review(
        _contas_state(
            route="human_review", motivo_humano="triagem_analise_humana", grupo_humano="auditoria-contas"
        )
    )
    assert result["dossier"]["decisao_glosa"] is None
    assert result["dossier"]["motivo_humano"] == "triagem_analise_humana"
    assert result["desfecho"] == "triagem_humana"


async def test_human_review_dossier_carries_motivo_never_a_decision_recurso() -> None:
    graph = _graph()
    result = await graph.human_review(
        _recurso_state(
            route="human_review", motivo_humano="recurso_analise_humana", grupo_humano="medico-auditor"
        )
    )
    assert result["dossier"]["decisao_recurso"] is None
    assert result["desfecho"] == "recurso_humano"


async def test_human_review_dossier_carries_motivo_never_a_decision_reembolso() -> None:
    graph = _graph()
    result = await graph.human_review(
        _reembolso_state(
            route="human_review", motivo_humano="reembolso_dossie", grupo_humano="analise-reembolso"
        )
    )
    assert result["dossier"]["decisao_reembolso"] is None
    assert result["desfecho"] == "reembolso_dossie_humano"


async def test_dossier_narrative_llm_call_is_phi_tagged() -> None:
    inference = _FakeInference(["narrativa"])
    graph = _graph(inference=inference)
    await graph.auto_route(_contas_state(route="auto_route"))
    assert inference.calls
    assert all(phi is True for _, phi in inference.calls)


async def test_dossier_llm_failure_never_blocks_the_route() -> None:
    """Hardening #1: an LLM failure never routes to a silent auto_route — here it degrades to an
    empty narrative while the (already-decided) route and the structural decision guardrail are
    unaffected."""

    class _FailingInference:
        async def generate(
            self,
            prompt: str,
            *,
            phi: bool = False,
            agent_id: str | None = None,
            tenant_id: str | None = None,
        ) -> str:
            raise RuntimeError("LLM down")

    graph = _graph(inference=_FailingInference())
    result = await graph.auto_route(_contas_state(route="auto_route"))
    assert result["dossier"]["narrativa"] == ""
    assert result["dossier"]["decisao_glosa"] is None


# ---------------------------------------------------------------------------
# start_process — idempotent, contract variables; no-op for reembolso
# ---------------------------------------------------------------------------


async def test_start_process_contas_starts_with_contract_variables() -> None:
    cibseven = FakeCibSevenTransport()
    graph = _graph(cibseven=cibseven)

    state = _contas_state(
        route="auto_route",
        # Low-entropy SYNTHETIC key (gitleaks hygiene — fernando precedent): keep the contract
        # format (`CONTAS-{tenant}-{lote}`) but NEVER use realistic-looking segment values in a
        # `business_key = "..."` literal — secret scanners flag high-entropy assignments.
        business_key="CONTAS-amh-000000001",
        dossier={"route": "auto_route"},
    )
    result = await graph.start_process(state)

    assert result["process_started"] is True
    assert result["process_ref"]["already_existed"] is False


async def test_start_process_recurso_idempotent_on_active_instance() -> None:
    cibseven = FakeCibSevenTransport()
    # Low-entropy SYNTHETIC key (gitleaks hygiene — fernando precedent): keep the contract
    # format (`RECURSO-{tenant}-{guia}-{glosa}`) but NEVER use realistic-looking segment values
    # in a `business_key = "..."` literal — secret scanners flag high-entropy assignments.
    business_key = "RECURSO-amh-000000001-000000002"
    cibseven.seed_instance(
        ProcessInstance(
            instance_id="existing-recurso-1",
            process_key="SP-OP-RECURSO-001",
            business_key=business_key,
            state="ACTIVE",
            already_existed=True,
        )
    )
    graph = _graph(cibseven=cibseven)

    result = await graph.start_process(_recurso_state(business_key=business_key, route="auto_route"))

    assert result["process_ref"]["instance_id"] == "existing-recurso-1"
    assert result["process_ref"]["already_existed"] is True


async def test_start_process_records_error_on_cibseven_failure() -> None:
    class _FailingCibSeven(FakeCibSevenTransport):
        async def start_process_instance(self, *args: Any, **kwargs: Any) -> ProcessInstance:
            raise CibSevenError("engine unreachable")

    graph = _graph(cibseven=_FailingCibSeven())
    result = await graph.start_process(_contas_state(route="human_review"))
    assert result["process_started"] is False
    assert "error" in result


async def test_start_process_reembolso_is_a_noop_never_starts_a_second_instance() -> None:
    """L0 guard: Marina NEVER starts SP-OP-REEMBOLSO-001 — the instance is already running."""

    class _RecordingCibSeven(FakeCibSevenTransport):
        async def start_process_instance(self, *args: Any, **kwargs: Any) -> ProcessInstance:
            raise AssertionError("start_process_instance must never be called for reembolso")

    graph = _graph(cibseven=_RecordingCibSeven())
    result = await graph.start_process(_reembolso_state(route="human_review"))
    assert result == {}


# ---------------------------------------------------------------------------
# contract variables — engine-variable hygiene (hardening #2)
# ---------------------------------------------------------------------------


def test_contract_variables_include_dossier_and_route_contas() -> None:
    graph = _graph()
    variables = graph._contract_variables(
        _contas_state(
            route="human_review", motivo_humano="triagem_analise_humana", dossier={"narrativa": "x"}
        )
    )
    assert variables["marina_route"] == "human_review"
    assert variables["motivo_encaminhamento"] == "triagem_analise_humana"
    assert variables["dossie_marina"] == {"narrativa": "x"}
    assert variables["source_agent_id"] == "marina"


def test_contract_variables_never_include_motivo_encaminhamento_on_auto_route() -> None:
    graph = _graph()
    variables = graph._contract_variables(_contas_state(route="auto_route"))
    assert "motivo_encaminhamento" not in variables


async def test_dmn_error_detail_never_reaches_engine_variables() -> None:
    """Helena-class probe (adapted to Marina's own vector): a DMN transport failure whose error
    text carries a PHI-like fragment (simulating an engine error response echoing back submitted
    variables) must NEVER reach engine-bound contract variables — only the bounded class token
    `motivo_encaminhamento="dmn_indisponivel"` may."""
    leaked_fragment = "CPF 123.456.789-00"

    class _LeakyDmnTransport(FakeDmnTransport):
        async def evaluate(
            self, decision_key: str, variables: dict[str, Any], *, tenant: str | None = None
        ) -> tuple[list[dict[str, Any]], Any]:
            raise DmnEvaluationError(f"engine 400: payload echo [{leaked_fragment}] for `{decision_key}`")

    recording: list[dict[str, Any]] = []

    class _RecordingCibSeven(FakeCibSevenTransport):
        async def start_process_instance(
            self, process_key: str, business_key: str, variables: dict[str, Any]
        ) -> ProcessInstance:
            recording.append(dict(variables))
            return await super().start_process_instance(process_key, business_key, variables)

    graph = _graph(dmn=_LeakyDmnTransport(), cibseven=_RecordingCibSeven()).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(_contas_state())

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "dmn_indisponivel"
    assert leaked_fragment in result["dmn_error"]  # state DOES carry it (observability)

    assert recording, "start_process must still run on the human_review route"
    import json as _json

    serialized = _json.dumps(recording[0], ensure_ascii=False, default=str)
    assert leaked_fragment not in serialized
    assert "123.456.789-00" not in serialized
    assert recording[0]["motivo_encaminhamento"] == "dmn_indisponivel"


# ---------------------------------------------------------------------------
# Full-graph turns (compiled, in-memory — no engine; live-engine status: see PR body)
# ---------------------------------------------------------------------------


async def test_full_turn_contas_auto_route_starts_process() -> None:
    dmn = FakeDmnTransport()
    _register_contas_dmn(dmn, categoria="administrativa", triagem="SEM_GLOSA")
    cibseven = FakeCibSevenTransport()
    inference = _FakeInference(["dossie sintetico"])
    graph = _graph(inference=inference, dmn=dmn, cibseven=cibseven).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(_contas_state())

    assert result["route"] == "auto_route"
    assert result["process_started"] is True
    assert result["dossier"]["decisao_glosa"] is None


async def test_full_turn_contas_human_review_starts_process() -> None:
    dmn = FakeDmnTransport()
    _register_contas_dmn(dmn, categoria="tecnica", triagem="ANALISE_HUMANA")
    cibseven = FakeCibSevenTransport()
    inference = _FakeInference(["dossie sintetico"])
    graph = _graph(inference=inference, dmn=dmn, cibseven=cibseven).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(_contas_state())

    assert result["route"] == "human_review"
    assert result["process_started"] is True
    assert result["desfecho"] == "triagem_analise_humana" or result["desfecho"] == "triagem_humana"


async def test_full_turn_recurso_auto_route_starts_process() -> None:
    dmn = FakeDmnTransport()
    _register_recurso_dmn(dmn)
    cibseven = FakeCibSevenTransport()
    inference = _FakeInference(["dossie sintetico"])
    graph = _graph(inference=inference, dmn=dmn, cibseven=cibseven).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(_recurso_state())

    assert result["route"] == "auto_route"
    assert result["process_started"] is True
    assert result["business_key"] == "RECURSO-amh-GUIA-001-GLOSA-001"


async def test_full_turn_recurso_human_review_medico_auditor() -> None:
    dmn = FakeDmnTransport()
    _register_recurso_dmn(dmn, elegibilidade="RECORRIVEL", grupo_revisor="medico-auditor")
    cibseven = FakeCibSevenTransport()
    inference = _FakeInference(["dossie sintetico"])
    graph = _graph(inference=inference, dmn=dmn, cibseven=cibseven).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(_recurso_state(glosa_type="clinica"))

    assert result["route"] == "human_review"
    assert result["grupo_humano"] == "medico-auditor"
    assert result["process_started"] is True


async def test_full_turn_reembolso_human_review_never_starts_second_instance() -> None:
    dmn = FakeDmnTransport()
    cibseven = FakeCibSevenTransport()
    inference = _FakeInference(["dossie sintetico"])
    graph = _graph(inference=inference, dmn=dmn, cibseven=cibseven).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(_reembolso_state())

    assert result["route"] == "human_review"
    assert result["grupo_humano"] == "analise-reembolso"
    # `False` is `receive`'s sanitization reset (R1 cycle-1) — start_process no-op'd and never
    # set it True; the engine-untouched proof is the probe-C regression test below.
    assert result["process_started"] is False
    assert result["dossier"]["decisao_reembolso"] is None
    assert dmn.calls == []


# ---------------------------------------------------------------------------
# Structural enum guards — no adverse variant exists (allowlists single-sourced, hardening #3)
# ---------------------------------------------------------------------------


def test_allowed_routes_never_include_an_adverse_variant() -> None:
    allowed = set(Route.__args__)  # type: ignore[attr-defined]
    assert allowed == {"auto_route", "human_review"}
    for adverse in ("accept_glosa", "deny_recurso", "deny_reembolso", "aceitar", "negar", "reduzir"):
        assert adverse not in allowed


def test_triagem_glosa_domain_never_includes_an_accept_variant() -> None:
    allowed = set(TriagemGlosa.__args__)  # type: ignore[attr-defined]
    assert allowed == {"SEM_GLOSA", "RECORRER", "ANALISE_HUMANA"}
    for adverse in ("ACEITAR", "CONFIRMAR", "ACEITAR_GLOSA"):
        assert adverse not in allowed


def test_admissibilidade_recurso_domain_never_includes_a_deny_variant() -> None:
    allowed = set(AdmissibilidadeRecurso.__args__)  # type: ignore[attr-defined]
    assert allowed == {"SEGUE_ANALISE", "PENDENTE_DOCUMENTACAO", "ANALISE_HUMANA"}
    for adverse in ("NEGAR", "INADMISSIVEL", "NAO_RECORRER"):
        assert adverse not in allowed


def test_elegibilidade_recurso_domain_never_includes_a_non_recorrivel_deny_variant() -> None:
    allowed = set(ElegibilidadeRecurso.__args__)  # type: ignore[attr-defined]
    assert allowed == {"RECORRIVEL", "ANALISE_HUMANA"}
    assert "NAO_RECORRIVEL" not in allowed


# ---------------------------------------------------------------------------
# R1 cycle-1 regression — caller-planted output fields (verifier probes A/B/C/D).
# `receive` must sanitize EVERY output-only field so a planted `error`/`route`/forged DMN fact
# can never bypass the DMN chain, forge dossier provenance, or reach engine-bound variables.
# ---------------------------------------------------------------------------

_SENTINEL = "PLANTED-0xC0FFEE-SENTINEL"


def _planted_outputs() -> dict[str, Any]:
    """A unique sentinel in EVERY output-only `MarinaState` field (the verifier's probe shape).

    Must mirror `graph._output_field_resets()`'s key set exactly —
    `test_receive_resets_every_output_only_field` asserts the two never drift apart.
    """
    return {
        "error": _SENTINEL,
        "route": "auto_route",  # the adversarial plant — must NEVER survive
        "motivo_humano": _SENTINEL,
        "grupo_humano": _SENTINEL,
        "business_key": _SENTINEL,
        "gathered": True,
        "summary_facts": {"planted": _SENTINEL},
        "gather_notes": [_SENTINEL],
        "categoria_normalizada": _SENTINEL,
        "glosa_classificada": _SENTINEL,
        "triagem": _SENTINEL,
        "admissibilidade_recurso": _SENTINEL,
        "elegibilidade_recurso": _SENTINEL,
        "grupo_revisor": _SENTINEL,
        "sla_analise": _SENTINEL,
        "sla_alerta": _SENTINEL,
        "dmn_refs": {"glosa_triage": _SENTINEL},
        "dmn_error": _SENTINEL,
        "dossier": {"narrativa": _SENTINEL},
        "desfecho": _SENTINEL,
        "process_started": True,
        "process_ref": {"instance_id": _SENTINEL},
    }


class _RecordingCibSeven(FakeCibSevenTransport):
    """Records every engine-bound variable set (the leak-surface the probes assert on)."""

    def __init__(self) -> None:
        super().__init__()
        self.recorded: list[dict[str, Any]] = []

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        self.recorded.append(dict(variables))
        return await super().start_process_instance(process_key, business_key, variables)


class _AssertingCibSeven(FakeCibSevenTransport):
    """Raises on ANY engine touch — for paths that must never reach the transport at all."""

    async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
        raise AssertionError("this path must never touch the engine transport")

    async def start_process_instance(self, *args: Any, **kwargs: Any) -> ProcessInstance:
        raise AssertionError("this path must never start a process")


async def test_receive_resets_every_output_only_field() -> None:
    """Structural core of the fix: `receive` overwrites every output-only field with its benign
    reset in its OWN state update — nothing caller-planted survives the first node."""
    from maezo.agents.marina.graph import _output_field_resets

    graph = _graph()
    planted = _planted_outputs()
    resets = _output_field_resets()
    # Drift guard: the probe fixture and the production reset list cover the SAME key set.
    assert set(planted) == set(resets), "planted fixture must mirror _output_field_resets keys"

    result = await graph.receive(_contas_state(**planted))

    for key in planted:
        assert key in result, f"receive did not overwrite planted output field {key!r}"
    for key, reset_value in resets.items():
        if key == "business_key":
            continue  # re-derived from input identifiers below, not a static reset
        assert result[key] == reset_value, f"{key!r} not reset: {result[key]!r}"
    assert result["business_key"] == "CONTAS-amh-LOTE-001"
    assert result["route"] == "human_review"  # fail-safe reset; assess recomputes
    assert _SENTINEL not in json.dumps(result, ensure_ascii=False, default=str)


async def test_full_turn_contas_planted_error_and_route_cannot_bypass_dmn_assessment() -> None:
    """Verifier probe A: planted `error` + `route="auto_route"` + forged SEM_GLOSA facts/
    dmn_refs must NEVER skip assess. Post-fix: receive sanitizes, assess re-evaluates the REAL
    DMN chain (4 calls), and the DMN-decided human route overwrites the plant before any engine
    start — no sentinel and no forged ref in the engine-bound variables."""
    dmn = FakeDmnTransport()
    _register_contas_dmn(dmn, categoria="tecnica", triagem="ANALISE_HUMANA")
    cibseven = _RecordingCibSeven()
    graph = _graph(dmn=dmn, cibseven=cibseven).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(
        _contas_state(
            error=_SENTINEL,
            route="auto_route",
            triagem="SEM_GLOSA",  # forged neutral outcome
            categoria_normalizada=_SENTINEL,
            glosa_classificada=_SENTINEL,
            dmn_refs={"glosa_triage": _SENTINEL},
            desfecho="sem_glosa",
            dossier={"narrativa": _SENTINEL},
        )
    )

    assert [table for table, _ in dmn.calls] == [
        "glosa_reason_normalization",
        "glosa_classification",
        "contas_sla",
        "glosa_triage",
    ], "assess must run the full real DMN chain — a planted error may never bypass it"
    assert result["route"] == "human_review"
    assert result["triagem"] == "ANALISE_HUMANA"  # DMN-decided; the forged SEM_GLOSA is gone
    assert result["motivo_humano"] == "triagem_analise_humana"

    assert cibseven.recorded, "the human-review route still starts the process"
    serialized = json.dumps(cibseven.recorded[0], ensure_ascii=False, default=str)
    assert _SENTINEL not in serialized
    assert cibseven.recorded[0]["marina_route"] == "human_review"
    assert cibseven.recorded[0]["dmn_decision_refs"]["glosa_triage"].startswith("glosa_triage#")


async def test_full_turn_recurso_planted_error_and_route_cannot_bypass_dmn_assessment() -> None:
    """Verifier probe D: the same plant against the RECURSO flow — the real admissibility DMN
    says ANALISE_HUMANA, which must defeat the planted auto_route and the forged eligibility."""
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "ANALISE_HUMANA")
    _register_recurso_sla(dmn)
    cibseven = _RecordingCibSeven()
    graph = _graph(dmn=dmn, cibseven=cibseven).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(
        _recurso_state(
            error=_SENTINEL,
            route="auto_route",
            admissibilidade_recurso="SEGUE_ANALISE",  # forged
            elegibilidade_recurso="RECORRIVEL",  # forged
            grupo_revisor=_SENTINEL,
            dmn_refs={"recurso_eligibility": _SENTINEL},
            desfecho="recurso_segue_analise",
        )
    )

    assert [table for table, _ in dmn.calls] == ["recurso_admissibility", "recurso_sla"]
    assert result["route"] == "human_review"
    assert result["admissibilidade_recurso"] == "ANALISE_HUMANA"
    assert result["motivo_humano"] == "recurso_analise_humana"

    assert cibseven.recorded
    serialized = json.dumps(cibseven.recorded[0], ensure_ascii=False, default=str)
    assert _SENTINEL not in serialized
    assert cibseven.recorded[0]["marina_route"] == "human_review"
    assert "recurso_eligibility" not in cibseven.recorded[0].get("dmn_decision_refs", {})


async def test_full_turn_reembolso_planted_route_and_error_never_start_a_process() -> None:
    """Verifier probe C (held pre-fix via the flow-keyed no-op; re-proven post-fix): even with
    error/route/process fields planted, the reembolso flow touches NO engine transport, stays
    human, and carries no sentinel."""
    dmn = FakeDmnTransport()
    graph = _graph(dmn=dmn, cibseven=_AssertingCibSeven()).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(
        _reembolso_state(
            error=_SENTINEL,
            route="auto_route",
            business_key=_SENTINEL,
            process_started=True,
            process_ref={"instance_id": _SENTINEL},
        )
    )

    assert result["route"] == "human_review"
    assert result["grupo_humano"] == "analise-reembolso"
    assert result["process_started"] is False
    assert result["business_key"] == "REEMB-amh-REEMB-PROTO-001"  # re-derived, plant gone
    assert dmn.calls == []
    assert _SENTINEL not in json.dumps(result["process_ref"], default=str)


async def test_dmn_down_shortcut_never_carries_planted_facts_into_dossier() -> None:
    """Verifier probe B (dmn-down variant): on the legitimate DMN-unavailable human shortcut,
    planted fact fields must NOT survive into the dossier `fatos` beside
    `motivo_humano=dmn_indisponivel` (forged contradictory provenance for the human auditor)."""
    dmn = FakeDmnTransport()  # nothing registered -> DMN unavailable
    cibseven = _RecordingCibSeven()
    graph = _graph(dmn=dmn, cibseven=cibseven).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(
        _contas_state(
            triagem="SEM_GLOSA",  # forged
            categoria_normalizada=_SENTINEL,
            glosa_classificada=_SENTINEL,
            dmn_refs={"glosa_triage": _SENTINEL},
            sla_analise=_SENTINEL,
        )
    )

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "dmn_indisponivel"
    fatos = result["dossier"]["fatos"]
    assert fatos["triagem"] is None  # the forged SEM_GLOSA is gone
    assert fatos["categoria_normalizada"] is None
    assert fatos["glosa_classificada"] is None
    assert _SENTINEL not in json.dumps(result["dossier"], ensure_ascii=False, default=str)
    assert cibseven.recorded
    assert _SENTINEL not in json.dumps(cibseven.recorded[0], ensure_ascii=False, default=str)


async def test_fraud_shortcut_never_carries_planted_facts_into_dossier() -> None:
    """Verifier probe B (fraud variant): the indicio_fraude human shortcut must carry ONLY the
    genuinely evaluated table (`contas_sla`) in dmn_decision_refs — no forged refs, no planted
    facts beside `motivo_humano=indicio_fraude`."""
    dmn = FakeDmnTransport()
    _register_contas_sla(dmn)
    cibseven = _RecordingCibSeven()
    graph = _graph(dmn=dmn, cibseven=cibseven).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(
        _contas_state(
            indicio_fraude_sinalizado=True,
            triagem="SEM_GLOSA",  # forged
            categoria_normalizada=_SENTINEL,
            glosa_classificada=_SENTINEL,
            dmn_refs={"glosa_triage": _SENTINEL},
        )
    )

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "indicio_fraude"
    fatos = result["dossier"]["fatos"]
    assert fatos["triagem"] is None
    assert fatos["categoria_normalizada"] is None
    assert _SENTINEL not in json.dumps(result["dossier"], ensure_ascii=False, default=str)
    assert cibseven.recorded
    assert set(cibseven.recorded[0]["dmn_decision_refs"]) == {"contas_sla"}
    assert _SENTINEL not in json.dumps(cibseven.recorded[0], ensure_ascii=False, default=str)


async def test_pendente_documentacao_shortcut_never_carries_planted_facts_into_dossier() -> None:
    """Verifier probe B (pendente variant, RECURSO): the PENDENTE_DOCUMENTACAO human shortcut
    never carries a forged eligibility outcome/ref the eligibility DMN was never asked for."""
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "PENDENTE_DOCUMENTACAO")
    _register_recurso_sla(dmn)
    cibseven = _RecordingCibSeven()
    graph = _graph(dmn=dmn, cibseven=cibseven).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(
        _recurso_state(
            documentacao_recurso_completa=False,
            elegibilidade_recurso="RECORRIVEL",  # forged
            grupo_revisor=_SENTINEL,
            dmn_refs={"recurso_eligibility": _SENTINEL},
        )
    )

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "documentacao_pendente"
    fatos = result["dossier"]["fatos"]
    assert fatos["elegibilidade_recurso"] is None  # the forged RECORRIVEL is gone
    assert fatos["grupo_revisor"] is None
    assert _SENTINEL not in json.dumps(result["dossier"], ensure_ascii=False, default=str)
    assert cibseven.recorded
    assert "recurso_eligibility" not in cibseven.recorded[0].get("dmn_decision_refs", {})
    assert _SENTINEL not in json.dumps(cibseven.recorded[0], ensure_ascii=False, default=str)


async def test_receive_fail_shortcut_never_starts_process_nor_carries_planted_outputs() -> None:
    """Missing runtime context + a fully planted output set: the turn must end route=human,
    touch NO engine transport (the planted business_key must not resurrect start_process's
    error short-circuit), evaluate NO DMN, and carry no sentinel anywhere in the final state."""
    dmn = FakeDmnTransport()
    graph = _graph(dmn=dmn, cibseven=_AssertingCibSeven()).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(_contas_state(tenant_id="", **_planted_outputs()))

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "outro"
    assert result["process_started"] is False
    assert result["business_key"] == ""  # planted key reset; error short-circuit stays closed
    assert dmn.calls == []
    assert _SENTINEL not in json.dumps(result, ensure_ascii=False, default=str)
